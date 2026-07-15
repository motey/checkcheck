from typing import Optional


from typing import List, Literal, Annotated, NoReturn
import asyncio
import uuid

from fastapi import (
    HTTPException,
    status,
    Security,
    Depends,
    APIRouter,
    Form,
    Header,
    Query,
    Request,
)

from fastapi.security import (
    OAuth2PasswordBearer,
    OAuth2PasswordRequestForm,
    OAuth2AuthorizationCodeBearer,
    OpenIdConnect,
)


from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

#
from checkcheckserver.utils import slugify_string
from checkcheckserver.db.user import UserCRUD, User
from checkcheckserver.db.user_auth import (
    UserAuthCRUD,
    UserAuth,
    AllowedAuthSchemeType,
    UserAuthUpdate,
)
from checkcheckserver.db.user_session import UserSessionCRUD, UserSession
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.api.auth.utils import (
    oidc_refresh_access_token,
    register_and_create_oauth_clients,
    validate_api_token,
    wipe_expired_user_session_or_user_auth,
    OAuthContainer,
)

log = get_logger()
config = Config()


oauth_clients: dict[str, OAuthContainer] = register_and_create_oauth_clients()

SESSION_COOKIE_NAME = f"session_{slugify_string(config.APP_NAME,'_')}"


def caller_restricted_to_own_groups(current_user_auth: "UserAuth") -> bool:
    """True when the caller logged in via an OIDC provider configured with
    ``RESTRICT_USER_SEARCH_TO_OWN_GROUPS`` — meaning they may only see / target
    other users (and OIDC groups) they share at least one group with.

    Local users and callers from an unrestricted provider return ``False``
    (unrestricted). Shared by user-search (routes_user) and group-share
    (routes_checklist_share) so both apply the exact same scoping rule."""
    if (
        current_user_auth is not None
        and current_user_auth.auth_source_type == AllowedAuthSchemeType.oidc
        and current_user_auth.oidc_provider_slug
    ):
        provider = next(
            (
                p
                for p in config.AUTH_OIDC_PROVIDERS
                if p.get_provider_name_slug() == current_user_auth.oidc_provider_slug
            ),
            None,
        )
        if provider is not None and provider.RESTRICT_USER_SEARCH_TO_OWN_GROUPS:
            return True
    return False


NEEDS_ADMIN_API_INFO = "Needs Admin role"
NEEDS_USERMAN_API_INFO = "Need usermanager role"
api_token_security = HTTPBearer(auto_error=False)
not_authenticated_exception = HTTPException(status_code=401, detail="Not authenticated")


# Serialize concurrent OIDC token refreshes per credential. A single-page app
# fires several API calls (and the /api/sync reconnect) in parallel; when the
# access token expires they would each independently POST the *same* refresh
# token to the IdP. IdPs rotate refresh tokens (single-use), so only the first
# grant succeeds and the rest get `invalid_grant` — which used to wipe the
# session and bounce the user to /login every few minutes. The whole app runs on
# one uvicorn worker / event loop (see main.py), so an asyncio.Lock keyed per
# user_auth.id gives real mutual exclusion: the winner refreshes, the others wait
# and then observe the already-refreshed credential (double-checked below).
# One lock per OIDC credential; the dict grows by at most one entry per fresh
# interactive OIDC login over the server's lifetime — negligible for a
# self-hosted deployment, and reset on restart.
_oidc_refresh_locks: dict[uuid.UUID, asyncio.Lock] = {}


def _get_oidc_refresh_lock(user_auth_id: uuid.UUID) -> asyncio.Lock:
    lock = _oidc_refresh_locks.get(user_auth_id)
    if lock is None:
        lock = asyncio.Lock()
        _oidc_refresh_locks[user_auth_id] = lock
    return lock


async def get_current_user_auth(
    request: Request,
    user_session_crud: UserSessionCRUD = Depends(UserSessionCRUD.get_crud),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
    api_token: Optional[HTTPAuthorizationCredentials] = Depends(api_token_security),
) -> UserAuth | None:
    user_auth: UserAuth | None = None
    user_session: UserSession | None = None
    if api_token:
        # api tokens are not session based
        token = api_token.credentials
        user_auth_token: UserAuth = await validate_api_token(
            token=token,
            not_authenticated_exception=not_authenticated_exception,
            user_auth_crud=user_auth_crud,
        )
        # santiy check
        assert user_auth_token.auth_source_type == AllowedAuthSchemeType.api_token
        user_auth = user_auth_token
        if user_auth_token.api_token_source_user_auth_id:
            user_auth = await user_auth_crud.get(
                user_auth_token.api_token_source_user_auth_id
            )

    else:
        # if not token based auth then it must be a session
        session_id = request.cookies.get(SESSION_COOKIE_NAME, None)
        if not session_id:
            raise not_authenticated_exception
        session_id = uuid.UUID(session_id)
        user_session: UserSession = await user_session_crud.get(session_id)
        if not user_session:
            raise not_authenticated_exception
        user_auth = await user_auth_crud.get(user_session.user_auth_id)
    if (
        user_session is not None and user_session.is_expired()
    ) or user_auth.is_expired():
        if user_auth.revoked:
            raise not_authenticated_exception
        if user_auth.auth_source_type == AllowedAuthSchemeType.basic:
            await wipe_expired_user_session_or_user_auth(
                user_auth=user_auth,
                user_auth_crud=user_auth_crud,
                user_session=user_session,
                user_session_crud=user_session_crud,
            )
            raise not_authenticated_exception
        elif user_auth.auth_source_type == AllowedAuthSchemeType.oidc:
            # For API-token path user_session is None; look it up by user_auth_id
            if user_session is None:
                user_session = await user_session_crud.get_by_user_auth_id(user_auth.id)
            # Serialize the refresh so parallel requests don't each burn the
            # (rotating, single-use) refresh token against the IdP.
            async with _get_oidc_refresh_lock(user_auth.id):
                # Double-checked: a concurrent request may have already refreshed
                # while we waited for the lock. Re-read from the DB and only refresh
                # if it is *still* expired — this is what stops every request but
                # the first from failing (and being wiped) under a rotating IdP.
                fresh_user_auth = await user_auth_crud.get(user_auth.id)
                if fresh_user_auth is None:
                    raise not_authenticated_exception
                fresh_session = user_session
                if user_session is not None:
                    fresh_session = await user_session_crud.get(user_session.id)
                still_expired = (
                    fresh_session is not None and fresh_session.is_expired()
                ) or fresh_user_auth.is_expired()
                if not still_expired:
                    # Already refreshed by another request in this window.
                    user_auth = fresh_user_auth
                else:
                    try:
                        user_auth = await oidc_refresh_access_token(
                            oauth_client=oauth_clients[fresh_user_auth.oidc_provider_slug],
                            user_auth_crud=user_auth_crud,
                            user_auth=fresh_user_auth,
                            user_session_crud=user_session_crud,
                            user_session=fresh_session,
                            raise_custom_expection_if_fails=not_authenticated_exception,
                        )
                    except HTTPException as e:
                        # The refresh genuinely failed. Before destroying the
                        # credential, re-read once more: a parallel/earlier refresh
                        # may have succeeded (e.g. a transient IdP blip on this
                        # request only), in which case this request is still valid
                        # and must NOT be logged out. Only wipe when it is still
                        # definitively expired.
                        recheck = await user_auth_crud.get(fresh_user_auth.id)
                        if recheck is not None and not recheck.is_expired():
                            user_auth = recheck
                        else:
                            await wipe_expired_user_session_or_user_auth(
                                user_auth=fresh_user_auth,
                                user_auth_crud=user_auth_crud,
                                user_session=fresh_session,
                                user_session_crud=user_session_crud,
                            )
                            raise e
    return user_auth


async def get_current_user(
    request: Request,
    user_session_crud: UserSessionCRUD = Depends(UserSessionCRUD.get_crud),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
    api_token: Optional[HTTPAuthorizationCredentials] = Depends(api_token_security),
) -> User:

    user_auth: UserAuth = await get_current_user_auth(
        request=request,
        user_session_crud=user_session_crud,
        user_auth_crud=user_auth_crud,
        api_token=api_token,
    )
    if user_auth is None:
        raise not_authenticated_exception

    user = await user_crud.get(user_auth.user_id)
    if user is None:
        raise not_authenticated_exception
    return user


async def user_is_admin(
    user: Annotated[User, Security(get_current_user)],
) -> bool:
    if not config.ADMIN_ROLE_NAME in user.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User is not admin",
        )
    return True


async def user_is_usermanager(
    user: Annotated[User, Security(get_current_user)],
) -> bool:
    if not (
        config.USERMANAGER_ROLE_NAME in user.roles
        or config.ADMIN_ROLE_NAME in user.roles
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User is not user manager",
        )
    return True
