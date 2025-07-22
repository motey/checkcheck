from typing import Optional, Union
from pydantic import BaseModel, Field
from fastapi import FastAPI, Request, Depends, Response, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from sqlmodel import SQLModel, Session, create_engine, select
from authlib.integrations.starlette_client import OAuth

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Annotated, NoReturn
from typing_extensions import Self
from jose import JWTError, jwt
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
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import httpx

from authlib.integrations.base_client.errors import OAuthError


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.auth_scheme_info import AuthSchemeInfo
from checkcheckserver.db.label import Label, LabelCRUD

#

from checkcheckserver.db.user import UserCRUD, UserCreate
from checkcheckserver.model.user import UserCreate, UserRegisterAPI
from checkcheckserver.db.user_auth import UserAuthCRUD
from checkcheckserver.db.user_session import UserSessionCRUD
from checkcheckserver.model.user_auth import (
    UserAuth,
    UserAuthCreate,
    AllowedAuthSchemeType,
)
from checkcheckserver.api.auth.security import (
    SESSION_COOKIE_NAME,
    oauth_clients,
    get_current_user,
    get_current_user_auth,
)
from checkcheckserver.model.user import User
from checkcheckserver.model.user_session import UserSession, UserSessionCreate
from checkcheckserver.api.auth.utils import (
    get_userinfo_from_token_or_endpoint,
    get_access_token_expires_at_value_from_token,
    generate_client_session_id,
    revoke_token,
    create_new_user_default_labels,
)

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

log = get_logger()
config = Config()

fast_api_auth_base_router: APIRouter = APIRouter()


class APIToken(BaseModel):
    access_token: str
    token_type: str = "Bearer"


@fast_api_auth_base_router.get("/auth/list", response_model=List[AuthSchemeInfo])
async def list_available_login_schemes(request: Request):
    log.debug(f"request.headers: {request.headers}")
    schemes: List[AuthSchemeInfo] = []
    if config.AUTH_BASIC_LOGIN_IS_ENABLED:
        schemes.append(
            AuthSchemeInfo(
                display_name="Local Login",
                provider_slug=None,
                auth_type=AllowedAuthSchemeType.basic,
                login_endpoint=str(request.url_for("auth_basic_login_session_based")),
                registration_endpoint=str(request.url_for("auth_basic_register")),
            )
        )
    for oidc_conf in config.AUTH_OIDC_PROVIDERS:
        provider_slug = oidc_conf.get_provider_name_slug()
        schemes.append(
            AuthSchemeInfo(
                display_name=oidc_conf.PROVIDER_DISPLAY_NAME,
                provider_slug=provider_slug,
                auth_type=AllowedAuthSchemeType.oidc,
                login_endpoint=str(
                    request.url_for(
                        "auth_oidc_login_session_based", provider_slug=provider_slug
                    )
                ),
            )
        )
    return schemes


@fast_api_auth_base_router.post("/auth/basic/register")
async def auth_basic_register(
    request: Request,
    password: str,
    password_repeat: str,
    user_data: UserRegisterAPI = Depends(),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
    label_crud: LabelCRUD = Depends(LabelCRUD.get_crud),
):
    if (
        not config.AUTH_BASIC_LOGIN_IS_ENABLED
        or not config.AUTH_BASIC_USER_DB_REGISTER_ENABLED
    ):
        raise HTTPException(
            status=status.HTTP_403_FORBIDDEN,
            detail="Basic password login is disabled or registration is disabled.",
        )
    if password != password_repeat:
        HTTPException(
            status=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match.",
        )
    user = await user_crud.create(
        UserCreate(**user_data.model_dump(), is_email_verified=False)
    )
    await create_new_user_default_labels(user.id, label_crud=label_crud)
    await user_auth_crud.create(
        UserAuthCreate(
            user_id=user.id,
            basic_password=password,
            auth_source_type=AllowedAuthSchemeType.basic,
        )
    )


class BasicLoginBody(BaseModel):
    username: str
    password: str


@fast_api_auth_base_router.post(
    "/auth/basic/login/session",
    description="Cookie based session login.",
    response_class=RedirectResponse,
    response_description="Set session cookie and redirect to `target_path` page or start page.",
)
async def auth_basic_login_session_based(
    request: Request,
    login: BasicLoginBody,
    target_path: Optional[str] = None,
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
    user_session_crud: UserSessionCRUD = Depends(UserSessionCRUD.get_crud),
) -> RedirectResponse:
    login_wrong_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Login failed",
    )
    if not config.AUTH_BASIC_LOGIN_IS_ENABLED:
        raise login_wrong_exception

    user_auth: UserAuth = await user_auth_crud.get_basic_auth_source_by_user_name(
        user_name=login.username, raise_exception_if_none=login_wrong_exception
    )
    user_auth.verify_password(
        login.password, raise_exception_if_wrong=login_wrong_exception
    )
    new_session_name = generate_client_session_id(request)
    new_session = UserSessionCreate(
        user_id=user_auth.user_id,
        user_auth_id=user_auth.id,
        display_name=new_session_name,
    )
    user_session: UserSession = await user_session_crud.create(new_session)
    response = RedirectResponse(
        url="/" if not target_path else target_path,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=str(user_session.id),
        httponly=True,
        secure=config.SET_SESSION_COOKIE_SECURE,
        samesite="Lax",
    )
    return response


@fast_api_auth_base_router.post("/auth/basic/login/token", response_model=APIToken)
async def auth_basic_login_token_based(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
):
    login_failed_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Login failed"
    )
    user = await user_crud.get_by_user_name_or_email(
        form_data.username, raise_exception_if_none=login_failed_exception
    )
    user_auth = await user_auth_crud.get_basic_auth_source_by_user_id(
        user_id=user.id, raise_exception_if_none=login_failed_exception
    )
    user_auth.verify_password(
        form_data.password, raise_exception_if_wrong=login_failed_exception
    )
    token_expires_at_epoch_time = None
    if config.API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES:
        token_expires_at_epoch_time = int(
            datetime.now().timestamp()
            + config.API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES * 60
        )
    user_auth_token = UserAuthCreate(
        user_id=user.id,
        auth_source_type=AllowedAuthSchemeType.api_token,
        expires_at_epoch_time=token_expires_at_epoch_time,
        api_token_source_user_auth_id=user_auth.id,
    )
    user_auth_token.generate_api_token()
    api_token_response = APIToken(access_token=user_auth_token.get_api_token())

    # the token get hashed when writen into the DB and will never be seen in plaintext in the backend after this function
    await user_auth_crud.create(user_auth_token)
    return api_token_response


@fast_api_auth_base_router.get(
    "/auth/oidc/login/{provider_slug}/session",
    response_class=RedirectResponse,
    response_description="Redirect to external OpenIDConnect provider authentikation endpoint. . After coming back the callback endpoint will set a session cookie and redirect to the webapp.",
)
async def auth_oidc_login_session_based(
    request: Request,
    provider_slug: str,
    target_path: Optional[str] = None,
):
    return await auth_oidc_login(
        request=request,
        provider_slug=provider_slug,
        target_path=target_path,
        method="session",
    )


@fast_api_auth_base_router.get(
    "/auth/oidc/login/{provider_slug}/token",
    response_class=RedirectResponse,
    response_description="Redirect to external OpenIDConnect provider authentikation endpoint. After coming back the callback endpoint will return an API access token.",
)
async def auth_oidc_login_token_based(
    request: Request,
    provider_slug: str,
    target_path: Optional[str] = None,
):
    return await auth_oidc_login(
        request=request,
        provider_slug=provider_slug,
        target_path=target_path,
        method="token",
    )


async def auth_oidc_login(
    request: Request,
    provider_slug: str,
    target_path: Optional[str] = None,
    method: Literal["session", "token"] = "session",
):
    oauth_client = oauth_clients[provider_slug].client
    redirect_uri = request.url_for("auth_oidc_callback", provider_slug=provider_slug)
    log.debug(f"redirect_uri: {redirect_uri}")
    # Retrieve the original path from session
    request.session["target_path"] = target_path or "/"
    if method == "session":
        request.session["login_type"] = "session"
    return await oauth_client.authorize_redirect(
        request, redirect_uri, target_path=target_path
    )


callback_description = """
Callback endpoint triggered after redirection from the OpenID Connect (OIDC) provider.<br><br>
Depending on the login method used:<br>
- **Session login** (`/auth/oidc/login/{provider_slug}/session`) returns a **307 Redirect** to the `target_path`.<br>
- **Token login** (`/auth/oidc/login/{provider_slug}/token`) returns a **200 OK** with a JSON access token.<br><br>
Supports both browser-based and API-based flows.
"""


@fast_api_auth_base_router.get(
    "/auth/oidc/callback/{provider_slug}",
    description=callback_description,
    responses={
        200: {
            "description": "Return an API access token.",
            "model": APIToken,
        },
        307: {
            "description": "Set a session cookie and redirect to the `target_path` given to the login endpoint.",
            "content": {
                "application/json": {
                    "example": None  # Swagger UI requires a content key to render this, even if empty
                }
            },
        },
    },
)
async def auth_oidc_callback(
    request: Request,
    provider_slug: str,
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
    user_session_crud: UserSessionCRUD = Depends(UserSessionCRUD.get_crud),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
):
    # Retrieve the original path from session
    target_path = request.session.pop("target_path", "/")
    login_type: Literal["token", "session"] = request.session.pop("login_type", "token")
    oauth_client = oauth_clients[provider_slug].client
    oauth_config = oauth_clients[provider_slug].config
    try:
        token = await oauth_client.authorize_access_token(request)
    except OAuthError as e:
        log.error(e, exc_info=True)
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Auth error can not fetch access token from {oauth_client.access_token_url}. Error: {e}",
        )
    log.debug(f"Token {token}")
    userinfo = await get_userinfo_from_token_or_endpoint(
        token, oauth_client, oauth_config
    )
    user = await user_crud.get_by_user_name(user_name=userinfo.preferred_username)
    if user is None and oauth_config.AUTO_CREATE_AUTHORIZED_USER:
        from pydantic import ValidationError

        try:
            user_create = UserCreate.from_oidc_userinfo(userinfo)
        except ValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Can not validate user data: {e}",
            )
        user: User = await user_crud.create(user_create)
    elif user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    user_auth = await user_auth_crud.create(
        UserAuthCreate(
            user_id=user.id,
            expires_at_epoch_time=get_access_token_expires_at_value_from_token(token),
            auth_source_type=AllowedAuthSchemeType.oidc,
            oidc_provider_slug=oauth_config.get_provider_name_slug(),
            oidc_token=token,
        )
    )
    user_session: UserSession = await user_session_crud.create(
        UserSessionCreate(
            user_id=user.id,
            user_auth_id=user_auth.id,
            session_name=generate_client_session_id(request=request),
            ip=request.client.host,
            user_agent=request.headers.get("user-agent", "unknown"),
            expires_at_epoch_time=user_auth.expires_at_epoch_time,
        )
    )
    if login_type == "token":
        # we do not need to set a `expires_at_epoch_time` because the OIDC will be checked on every auth call whith OIDC login connected api token
        user_auth_token = UserAuthCreate(
            user_id=user.id,
            auth_source_type=AllowedAuthSchemeType.api_token,
            api_token_source_user_auth_id=user_auth.id,
            expires_at_epoch_time=None,
        )
        user_auth_token.generate_api_token()
        api_token_response = APIToken(access_token=user_auth_token.get_api_token())
        await user_auth_crud.create(user_auth_token)
        return api_token_response

        # the token get hashed when writen into the DB and will never be seen in plaintext in the backend after this function
        await user_auth_crud.create(user_auth_token)

    elif login_type == "session":
        # Set a session cookie (here, just user ID as session token)
        response = RedirectResponse(url="/" if not target_path else target_path)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=str(user_session.id),
            httponly=True,
            secure=False,  # True in prod
            samesite="Lax",
        )
        return response


@fast_api_auth_base_router.post("/auth/logout")
async def logout(
    request: Request,
    current_user_auth: UserAuth = Depends(get_current_user_auth),
    user_session_crud: UserSessionCRUD = Depends(UserSessionCRUD.get_crud),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
):
    if current_user_auth.auth_source_type in [
        AllowedAuthSchemeType.oidc,
        AllowedAuthSchemeType.basic,
    ]:
        user_session = await user_session_crud.get_by_user_auth_id(current_user_auth.id)
        await user_session_crud.delete(user_session.id)
        response = JSONResponse(content={"message": "Logged out successfully"})
        log.debug(f"current_user_auth: {current_user_auth}")
        if current_user_auth.auth_source_type == AllowedAuthSchemeType.oidc:
            # delete local oidc token storage

            refresh_token = current_user_auth.get_decrypted_oidc_token().get(
                "refresh_token"
            )
            await user_auth_crud.delete(id=current_user_auth.id)

            # revoke token

            oauth_client = oauth_clients[current_user_auth.oidc_provider_slug]
            await revoke_token(oauth_client=oauth_client, token=refresh_token)

    elif current_user_auth.auth_source_type == AllowedAuthSchemeType.api_token:
        await user_auth_crud.delete(id=current_user_auth.id)
