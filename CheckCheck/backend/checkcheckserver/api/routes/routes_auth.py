import uuid
from urllib.parse import urlsplit
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
from fastapi.security import HTTPAuthorizationCredentials, OAuth2PasswordBearer, OAuth2PasswordRequestForm
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
    api_token_security,
    oauth_clients,
    get_current_user,
    get_current_user_auth,
)
from checkcheckserver.model.user import User
from checkcheckserver.db.checklist import CheckListCRUD
from checkcheckserver.db.checklist_group_share import CheckListGroupShareCRUD
from checkcheckserver.db.checklist_collaborator import CheckListCollaboratorCRUD
from checkcheckserver.db.checklist_position import CheckListPositionCRUD
from checkcheckserver.db.sync_notification import SyncNotifiationCRUD
from checkcheckserver.db.notification import NotificationCRUD
from checkcheckserver.api.group_share_reconcile import reconcile_user
from checkcheckserver.model.user_session import UserSession, UserSessionCreate
from checkcheckserver.api.auth.utils import (
    get_userinfo_from_token_or_endpoint,
    get_access_token_expires_at_value_from_token,
    generate_client_session_id,
    revoke_oidc_token,
    create_new_user_default_labels,
    validate_api_token,
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
                auto_login=bool(oidc_conf.AUTO_LOGIN),
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
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Basic password login is disabled or registration is disabled.",
        )
    if password != password_repeat:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
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
        session_name=new_session_name,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "unknown"),
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
    # Build the callback URL from the configured public URL rather than from the
    # incoming request's scheme/host. Those come from X-Forwarded-* headers, which
    # are only as trustworthy as the proxy chain; pinning to SERVER_PUBLIC_URL keeps
    # the redirect target stable and unspoofable, and guarantees it matches the URI
    # registered with the provider.
    callback_path = urlsplit(
        str(request.url_for("auth_oidc_callback", provider_slug=provider_slug))
    ).path
    redirect_uri = config.get_server_url() + callback_path
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
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    checklist_group_share_crud: CheckListGroupShareCRUD = Depends(
        CheckListGroupShareCRUD.get_crud
    ),
    checklist_collaborator_crud: CheckListCollaboratorCRUD = Depends(
        CheckListCollaboratorCRUD.get_crud
    ),
    checklist_position_crud: CheckListPositionCRUD = Depends(
        CheckListPositionCRUD.get_crud
    ),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
    notification_crud: NotificationCRUD = Depends(NotificationCRUD.get_crud),
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
        # A stale or duplicate session cookie can leave authlib's stored state out
        # of sync with the callback (`mismatching_state`). For the browser (session)
        # flow that must not dead-end on a 500 that only a manual cookie-clear
        # escapes — bounce back to the login page so a fresh attempt self-heals. The
        # `?oidc_error` marker tells the page to show the form (not auto-loop).
        if login_type == "session":
            return RedirectResponse(url="/login?oidc_error=auth")
        raise HTTPException(
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
        # A brand-new account: any group they arrived with is "new", so reconcile
        # so cards already shared with those groups appear on their first login.
        groups_changed = bool(user.oidc_groups)
    elif user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    else:
        # Sync display_name, email and roles from the OIDC provider on every login
        from checkcheckserver.db.user import UserUpdateByAdmin

        old_groups = set(user.oidc_groups or [])
        synced = UserCreate.from_oidc_userinfo(userinfo)
        update = UserUpdateByAdmin(
            display_name=synced.display_name,
            email=synced.email,
            roles=synced.roles,
            oidc_groups=synced.oidc_groups,
        )
        user = await user_crud.update(user_update=update, user_id=user.id)
        # Living group shares: only reconcile when the OIDC group set actually
        # changed (joined/left a group) — the common no-change login stays cheap.
        groups_changed = set(user.oidc_groups or []) != old_groups

    if groups_changed:
        # Grant cards shared with groups the user is now in, and drop group-derived
        # access to groups they left. Never let a reconcile hiccup block the login
        # itself — access self-heals on the next reconcile.
        try:
            await reconcile_user(
                user=user,
                checklist_crud=checklist_crud,
                checklist_group_share_crud=checklist_group_share_crud,
                checklist_collaborator_crud=checklist_collaborator_crud,
                checklist_position_crud=checklist_position_crud,
                sync_crud=sync_crud,
                notification_crud=notification_crud,
                user_crud=user_crud,
            )
        except Exception as e:  # noqa: BLE001
            log.error(f"Group-share reconcile on login failed for {user.id}: {e}", exc_info=True)
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
            ip=request.client.host if request.client else None,
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

    elif login_type == "session":
        # Set a session cookie (here, just user ID as session token)
        response = RedirectResponse(url="/" if not target_path else target_path)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=str(user_session.id),
            httponly=True,
            secure=config.SET_SESSION_COOKIE_SECURE,
            samesite="Lax",
        )
        return response


@fast_api_auth_base_router.post("/auth/logout")
async def logout(
    request: Request,
    current_user_auth: UserAuth = Depends(get_current_user_auth),
    api_token: Optional[HTTPAuthorizationCredentials] = Depends(api_token_security),
    user_session_crud: UserSessionCRUD = Depends(UserSessionCRUD.get_crud),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
):
    response = JSONResponse(content={"message": "Logged out successfully"})
    if api_token:
        # Delete the actual api_token UserAuth (get_current_user_auth resolves to the
        # parent basic UserAuth, so we must look up the token record directly).
        token_user_auth = await validate_api_token(
            token=api_token.credentials,
            not_authenticated_exception=HTTPException(status_code=401, detail="Not authenticated"),
            user_auth_crud=user_auth_crud,
        )
        await user_auth_crud.delete(id=token_user_auth.id)
    elif current_user_auth.auth_source_type in [
        AllowedAuthSchemeType.oidc,
        AllowedAuthSchemeType.basic,
    ]:
        # Delete the specific session identified by the request cookie, not all
        # sessions for this user_auth_id.  Using get_by_user_auth_id would crash
        # when the same user has multiple active sessions (e.g. two browser tabs).
        session_id_str = request.cookies.get(SESSION_COOKIE_NAME)
        if session_id_str:
            try:
                user_session = await user_session_crud.get(uuid.UUID(session_id_str))
            except Exception:
                user_session = None
            if user_session:
                await user_session_crud.delete(user_session.id)
        response.delete_cookie(key=SESSION_COOKIE_NAME)
        log.debug(f"current_user_auth: {current_user_auth}")
        if current_user_auth.auth_source_type == AllowedAuthSchemeType.oidc:
            refresh_token = current_user_auth.get_decrypted_oidc_token().get(
                "refresh_token"
            )
            await user_auth_crud.delete(id=current_user_auth.id)
            oauth_client = oauth_clients[current_user_auth.oidc_provider_slug]
            await revoke_oidc_token(oauth_client=oauth_client, token=refresh_token)

    return response
