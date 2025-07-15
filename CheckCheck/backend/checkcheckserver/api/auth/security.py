from typing import Optional


from typing import List, Literal, Annotated, NoReturn
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
NEEDS_ADMIN_API_INFO = "Needs Admin role"
NEEDS_USERMAN_API_INFO = "Need usermanager role"
api_token_security = HTTPBearer(auto_error=False)
not_authenticated_exception = HTTPException(status_code=401, detail="Not authenticated")


async def get_current_user_auth(
    request: Request,
    user_session_crud: UserSessionCRUD = Depends(UserSessionCRUD.get_crud),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
    api_token: Optional[HTTPAuthorizationCredentials] = Depends(api_token_security),
) -> UserAuth:
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
            wipe_expired_user_session_or_user_auth(
                user_session=user_session,
                user_session_crud=user_session_crud,
            )
            raise not_authenticated_exception
        elif user_auth.auth_source_type == AllowedAuthSchemeType.oidc:
            # we try the oidc refresh token
            session_id = request.cookies.get(SESSION_COOKIE_NAME, None)
            user_session: UserSession = await user_session_crud.get(str(session_id))
            try:
                user_auth = await oidc_refresh_access_token(
                    oauth_client=oauth_clients[user_auth.oidc_provider_slug],
                    user_auth_crud=user_auth_crud,
                    user_auth=user_auth,
                    user_session_crud=user_session_crud,
                    user_session=user_session,
                    raise_custom_expection_if_fails=not_authenticated_exception,
                )
            except HTTPException as e:
                await wipe_expired_user_session_or_user_auth(
                    user_auth=user_auth,
                    user_auth_crud=user_auth_crud,
                    user_session=user_session,
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

    return await user_crud.get(user_auth.user_id)


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
