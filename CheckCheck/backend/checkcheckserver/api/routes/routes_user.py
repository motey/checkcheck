from typing import Annotated, Sequence, List, Type, Optional
from datetime import datetime, timedelta, timezone
import uuid

from fastapi import Depends, Security, FastAPI, HTTPException, Request, status, Query, Body, Form
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from typing import Annotated

from fastapi import Depends, APIRouter
from checkcheckserver.api.paginator import (
    PaginatedResponse,
    create_query_params_class,
    QueryParamsInterface,
)

from checkcheckserver.db.user import (
    User,
    UserCRUD,
    UserCreate,
    UserUpdate,
    UserUpdateByUser,
    UserUpdateByAdmin,
)
from checkcheckserver.db.user_auth import (
    UserAuth,
    UserAuthCreate,
    UserAuthUpdate,
    UserAuthCRUD,
    AllowedAuthSchemeType,
)
from checkcheckserver.db.user_session import UserSession, UserSessionCRUD
from checkcheckserver.model.user_auth import UserAuthPublic
from checkcheckserver.api.auth.security import (
    SESSION_COOKIE_NAME,
    user_is_admin,
    user_is_usermanager,
    get_current_user,
    get_current_user_auth,
    caller_restricted_to_own_groups,
)


from checkcheckserver.config import Config

config = Config()

from checkcheckserver.log import get_logger

log = get_logger()


fast_api_user_self_service_router: APIRouter = APIRouter()


@fast_api_user_self_service_router.get(
    "/user/me",
    response_model=User,
    description="Get account data from the current user",
)
async def get_myself(
    current_user: User = Depends(get_current_user),
) -> User:
    return current_user


class UserSearchResult(BaseModel):
    """Minimal, safe-to-expose user info for picking a share target.
    Deliberately excludes email and any auth details."""

    id: uuid.UUID
    user_name: Optional[str] = None
    display_name: Optional[str] = None


@fast_api_user_self_service_router.get(
    "/user/search",
    response_model=List[UserSearchResult],
    description=(
        "Search for other users by name to share a card with them. Returns only "
        "id / user_name / display_name (never email). Available to any authenticated "
        "user when SHARING_USER_SEARCH_ENABLED is on. If the caller logged in via an "
        "OIDC provider configured with RESTRICT_USER_SEARCH_TO_OWN_GROUPS, results are "
        "limited to users that share at least one OIDC group with the caller."
    ),
)
async def search_users(
    q: str = Query(min_length=2, description="Search term matched against user_name and display_name."),
    limit: int = Query(default=20, ge=1, le=50),
    current_user: User = Security(get_current_user),
    current_user_auth: UserAuth = Depends(get_current_user_auth),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
) -> List[UserSearchResult]:
    if not (config.SHARING_ENABLED and config.SHARING_USER_SEARCH_ENABLED):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User search is disabled on this server.",
        )

    # Determine whether this caller's OIDC provider restricts search to own groups.
    restrict_to_groups = caller_restricted_to_own_groups(current_user_auth)

    # When restricting to shared groups we filter in Python *after* the query, so
    # the DB-level limit must not pre-truncate matches that survive the filter —
    # otherwise non-group users in the first ``limit`` rows can crowd out (or even
    # zero out) the valid results. Over-fetch, then filter, then truncate.
    # (The proper fix is a SQL-side JSON intersection, but that is dialect-specific
    # for the JSON ``oidc_groups`` column; this bounded over-fetch is portable and
    # is plenty for a search box gated by a 2-char minimum query.)
    fetch_limit = min(limit * 10, 500) if restrict_to_groups else limit
    candidates = await user_crud.search(
        query_str=q, exclude_user_id=current_user.id, limit=fetch_limit
    )

    if restrict_to_groups:
        caller_groups = set(current_user.oidc_groups or [])
        candidates = [
            u for u in candidates if caller_groups & set(u.oidc_groups or [])
        ][:limit]

    return [
        UserSearchResult(
            id=u.id, user_name=u.user_name, display_name=u.display_name
        )
        for u in candidates
    ]


@fast_api_user_self_service_router.patch(
    "/user/me",
    response_model=User,
    description="Update my user account data.",
)
async def update_myself(
    patched_user: UserUpdateByUser,
    current_user: User = Security(get_current_user),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
) -> User:
    return await user_crud.update(user_update=patched_user, user_id=current_user.id)


@fast_api_user_self_service_router.put(
    "/user/me/password",
    response_model=bool,
    description="Set my password if i am a 'local' user. If my account was provisioned via an external OpenID Connect provider this does nothing except the return value will be `false`.",
)
async def set_my_password(
    old_password: str = Form(default=None),
    new_password: str = Form(default=None),
    new_password_repeated: str = Form(
        default=None,
        description="For good measure we require the password twice to mitiage typos.",
    ),
    current_user: User = Security(get_current_user),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
) -> bool:
    if new_password != new_password_repeated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="new password and repeated new password do not match",
        )

    old_user_auth: UserAuth = await user_auth_crud.get_basic_auth_source_by_user_id(
        current_user.id
    )
    if old_user_auth is None:
        return False
    old_user_auth.verify_password(
        old_password,
        raise_exception_if_wrong=HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not verify authorization",
        ),
    )
    updated_user_auth = UserAuthUpdate(basic_password=new_password)
    await user_auth_crud.update(updated_user_auth, id_=old_user_auth.id)
    return True


# ── API Key Management ────────────────────────────────────────────────────────


class APIKeyCreateRequest(BaseModel):
    display_name: str = Field(
        min_length=1,
        max_length=128,
        description="Human-readable label for this key (e.g. 'CI pipeline', 'Home laptop').",
    )
    expires_in_days: Optional[int] = Field(
        default=None,
        ge=1,
        le=3650,
        description="Validity in days from now. Omit to use the server default.",
    )


class APIKeyCreatedResponse(UserAuthPublic):
    token: str = Field(
        description="The plaintext API token. This is shown exactly once — store it safely."
    )


@fast_api_user_self_service_router.get(
    "/user/me/api-keys",
    response_model=List[UserAuthPublic],
    description="List all active API keys belonging to the current user.",
)
async def list_my_api_keys(
    include_revoked: bool = Query(default=False),
    current_user: User = Security(get_current_user),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
) -> List[UserAuthPublic]:
    tokens = await user_auth_crud.list_api_tokens_by_user_id(
        user_id=current_user.id,
        include_revoked=include_revoked,
    )
    return [UserAuthPublic.model_validate(t) for t in tokens]


@fast_api_user_self_service_router.post(
    "/user/me/api-keys",
    response_model=APIKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    description="Create a new named API key. The plaintext token is returned **once** — save it.",
)
async def create_my_api_key(
    body: APIKeyCreateRequest,
    current_user: User = Security(get_current_user),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
) -> APIKeyCreatedResponse:
    expires_at: Optional[int] = None
    if body.expires_in_days is not None:
        expires_at = int(
            (datetime.now(tz=timezone.utc) + timedelta(days=body.expires_in_days)).timestamp()
        )
    elif config.API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES is not None:
        expires_at = int(
            datetime.now(tz=timezone.utc).timestamp()
            + config.API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES * 60
        )

    new_auth = UserAuthCreate(
        user_id=current_user.id,
        auth_source_type=AllowedAuthSchemeType.api_token,
        display_name=body.display_name,
        expires_at_epoch_time=expires_at,
    )
    new_auth.generate_api_token()
    plaintext_token = new_auth.get_api_token()
    saved = await user_auth_crud.create(new_auth)
    return APIKeyCreatedResponse(
        **UserAuthPublic.model_validate(saved).model_dump(),
        token=plaintext_token,
    )


@fast_api_user_self_service_router.delete(
    "/user/me/api-keys/{api_token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    description="Revoke an API key by its identifier prefix. Only the owner can revoke their own keys.",
)
async def delete_my_api_key(
    api_token_id: str,
    current_user: User = Security(get_current_user),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
):
    token_auth = await user_auth_crud.get_api_token_by_id(api_token_id)
    if token_auth is None or token_auth.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    await user_auth_crud.delete(token_auth.id)


# ── Session Management ────────────────────────────────────────────────────────


@fast_api_user_self_service_router.get(
    "/user/me/sessions",
    response_model=List[UserSession],
    description="List all active sessions for the current user.",
)
async def list_my_sessions(
    current_user: User = Security(get_current_user),
    user_session_crud: UserSessionCRUD = Depends(UserSessionCRUD.get_crud),
) -> List[UserSession]:
    return await user_session_crud.list_by_user_id(
        user_id=current_user.id,
        include_expired=False,
    )


@fast_api_user_self_service_router.delete(
    "/user/me/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    description="Revoke a specific session. Useful to log out from another device.",
)
async def delete_my_session(
    session_id: uuid.UUID,
    current_user: User = Security(get_current_user),
    current_user_auth: UserAuth = Depends(get_current_user_auth),
    user_session_crud: UserSessionCRUD = Depends(UserSessionCRUD.get_crud),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
):
    session = await user_session_crud.get(session_id)
    if session is None or session.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await user_session_crud.delete(session.id)
    # If this session's linked auth is OIDC, also delete the OIDC user_auth entry
    session_auth = await user_auth_crud.get(session.user_auth_id)
    if session_auth is not None and session_auth.auth_source_type == AllowedAuthSchemeType.oidc:
        await user_auth_crud.delete(session_auth.id)


@fast_api_user_self_service_router.delete(
    "/user/me/sessions",
    status_code=status.HTTP_204_NO_CONTENT,
    description="Revoke all sessions except the current one.",
)
async def delete_all_my_sessions_except_current(
    request: Request,
    current_user: User = Security(get_current_user),
    user_session_crud: UserSessionCRUD = Depends(UserSessionCRUD.get_crud),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
):
    # Identify the current session from the cookie (None when using a Bearer token).
    current_session_id: uuid.UUID | None = None
    raw_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_cookie:
        try:
            current_session_id = uuid.UUID(raw_cookie)
        except ValueError:
            pass

    sessions = await user_session_crud.list_by_user_id(
        user_id=current_user.id, include_expired=True
    )
    for session in sessions:
        if current_session_id is not None and session.id == current_session_id:
            continue  # keep the session the caller is currently using
        session_auth = await user_auth_crud.get(session.user_auth_id)
        await user_session_crud.delete(session.id)
        if session_auth is not None and session_auth.auth_source_type == AllowedAuthSchemeType.oidc:
            await user_auth_crud.delete(session_auth.id)
