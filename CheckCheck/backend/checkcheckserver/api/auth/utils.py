from typing import Dict, Optional, Any
from authlib.integrations.starlette_client.apps import StarletteOAuth2App
import httpx

from fastapi import Request
import uuid
import datetime

import base64
import os
import hashlib
import json
from dataclasses import dataclass
from authlib.oauth2.rfc6749 import OAuth2Token
from authlib.integrations.starlette_client import OAuth
from authlib.integrations.starlette_client.apps import StarletteOAuth2App
from authlib.integrations.starlette_client.integration import StarletteIntegration
from checkcheckserver.model.user_info_oidc import UserInfoOidc
from checkcheckserver.model.user_auth import (
    UserAuth,
    UserAuthCreate,
    AllowedAuthSchemeType,
)
from checkcheckserver.db.user_auth import (
    UserAuthCRUD,
    UserAuth,
    AllowedAuthSchemeType,
    UserAuthUpdate,
)

from checkcheckserver.model.label import Label
from checkcheckserver.db.label import LabelCRUD
from checkcheckserver.model.checklist_label import CheckListLabel
from checkcheckserver.db.checklist_label import ChecklistLabelCRUD

from checkcheckserver.db.user_session import UserSessionCRUD, UserSession
from checkcheckserver.model.user import User
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


def generate_client_session_id(request: Request) -> str:
    ip = request.client.host
    user_agent = request.headers.get("user-agent", "unknown")
    timestamp = datetime.datetime.now(tz=datetime.UTC).isoformat()
    salt = str(uuid.uuid4())

    raw_string = f"{timestamp}-{ip}-{user_agent}-{salt}"
    session_id = hashlib.sha256(raw_string.encode()).hexdigest()

    return session_id


log = get_logger()
config = Config()


@dataclass
class OAuthContainer:
    client: StarletteOAuth2App
    config: Config.OpenIDConnectProvider


def register_and_create_oauth_clients():
    oauth_clients_: dict[str, OAuthContainer] = {}
    for oauth_config in config.AUTH_OIDC_PROVIDERS:
        oauth = OAuth()
        provider_slug_ = oauth_config.get_provider_name_slug()
        oauth.register(
            name=oauth_config.get_provider_name_slug(),
            client_id=oauth_config.CLIENT_ID,
            client_secret=oauth_config.CLIENT_SECRET.get_secret_value(),
            server_metadata_url=oauth_config.CONFIGURATION_ENDPOINT,
            client_kwargs={
                "scope": oauth_config.get_scopes_as_string(),
            },
        )
        oa_client: StarletteOAuth2App = oauth.create_client(
            oauth_config.get_provider_name_slug()
        )
        oauth_clients_[provider_slug_] = OAuthContainer(
            client=oa_client, config=oauth_config
        )
    return oauth_clients_


async def oidc_refresh_access_token(
    oauth_client: OAuthContainer,
    user_auth_crud: UserAuthCRUD,
    user_auth: UserAuth,
    user_session_crud: UserSessionCRUD,
    user_session: UserSession,
    raise_custom_expection_if_fails: Exception = None,
) -> UserAuth:
    log.debug("REFRESH OIDC TOKEN")
    # sanity check
    assert user_session.user_auth_id == user_auth.id
    old_token = user_auth.get_decrypted_oidc_token()
    try:
        oidc_server_metadata = await oauth_client.client.load_server_metadata()
        token_endpoint = oidc_server_metadata.get("token_endpoint", None)
        refresh_token = old_token.get("refresh_token")
        log.debug(f"refresh_token: {refresh_token}")
        new_access_token = await oauth_client.client.fetch_access_token(
            token_endpoint,
            refresh_token=refresh_token,
            grant_type="refresh_token",
        )
        user_auth.update_oidc_token(new_access_token)
    except Exception as e:
        log.debug(f"REFRESH OIDC TOKEN FAILED. Error: {e}")
        # log.error(e)
        if raise_custom_expection_if_fails:
            raise raise_custom_expection_if_fails
        raise e
    user_session.expires_at_epoch_time = new_access_token.get("expires_at")
    user_session = await user_session_crud.update(user_session)
    user_auth_update = UserAuthUpdate(
        oidc_token=user_auth.get_decrypted_oidc_token(),
        expires_at_epoch_time=new_access_token.get("expires_at"),
    )
    user_auth = await user_auth_crud.update(user_auth_update, user_auth.id)
    return user_auth


async def revoke_oidc_token(oauth_client: OAuthContainer, token: str):

    # Nothing to do if no token
    if token is None:
        return None

    oauth_client_metadata = await oauth_client.client.load_server_metadata()

    revocation_endpoint = oauth_client_metadata.get("revocation_endpoint")
    if revocation_endpoint is None:
        # no revocation endpoint. nothing to revoke
        log.debug(
            f"Can not revoke token because there is no revocation_endpoint declared by the oauth provider"
        )
        return None
    resp = await oauth_client.client.post(
        revocation_endpoint,
        data={"token": token, "token_type_hint": "access_token"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        auth=(oauth_client.client.client_id, oauth_client.client.client_secret),
    )
    if resp.status_code != 200:
        log.error(resp)
    return


async def _get_api_token_user_auth(
    user_auth_crud: UserAuthCRUD, token: str
) -> UserAuth:
    try:
        token_identifier, token_payload = token.split(".", maxsplit=1)
    except ValueError:
        return None
    token: UserAuth = await user_auth_crud.get_api_token_by_id(
        token_id=token_identifier
    )
    return token


async def validate_api_token(
    token: str,
    not_authenticated_exception: Exception,
    user_auth_crud: Optional[UserAuthCRUD] = None,
    user_auth: Optional[UserAuth] = None,
) -> UserAuth:
    if user_auth_crud is None and user_auth is None:
        raise ValueError(
            "Expected either user_auth_crud or user_auth have to be provided. Both None."
        )
    token_user_auth = user_auth
    if token_user_auth is None:
        token_user_auth: UserAuth = await _get_api_token_user_auth(
            user_auth_crud=user_auth_crud, token=token
        )
    if token_user_auth is None:
        log.debug("Token not existent")
        raise not_authenticated_exception
    if token_user_auth.is_expired():
        log.debug("Token expired")
        raise not_authenticated_exception
    token_user_auth.verify_api_token(
        token,
        raise_exception_if_wrong=not_authenticated_exception,
    )
    return token_user_auth


async def wipe_expired_user_session_or_user_auth(
    user_auth: UserAuth,
    user_auth_crud: UserAuthCRUD,
    user_session: Optional[UserSession] = None,
    user_session_crud: Optional[UserSessionCRUD] = None,
):
    log.warning("TODO: Session wiping is not implemeted yet")


def get_access_token_expires_at_value_from_token(token: dict) -> int:
    raw_userinfo: Dict | None = None
    log.debug(f"get_access_token_expires_at_value_from_token token {token}")
    if "userinfo" in token and "exp" in token["userinfo"]:
        return token["userinfo"]["exp"]
    if "expire_at" in token:
        return token["expires_at"]
    if "expires_in" in token and "userinfo" in token and "iat" in token["userinfo"]:
        return token["userinfo"]["iat"] - token["expires_in"]
    if "expires_in" in token:
        # this should never happen. not sure if we should do this anyway...
        # Todo: review this
        log.warning("ARE WE SURE THAT WE WANT TO CALCULATE THE expire time like this?")
        return int(datetime.datetime.now().timestamp()) - token["expires_in"]
    raise ValueError("Can not determine expires_at time for oidc token.")


async def get_userinfo_from_token_or_endpoint(
    token: dict,
    oidc_client: OAuthContainer,
    oidc_config: Config.OpenIDConnectProvider,
    force_userinfo_endpoint: bool = False,
) -> UserInfoOidc:
    raw_userinfo: Dict | None = None
    if "userinfo" in token and not force_userinfo_endpoint:
        raw_userinfo = token["userinfo"]
    else:
        # Fall back to UserInfo endpoint
        access_token = token.get("access_token")
        if not access_token:
            raise ValueError("No access_token provided to fetch userinfo.")
        server_metadata = await oidc_client.client.load_server_metadata()
        userinfo_endpoint = server_metadata.get("userinfo_endpoint")
        if not userinfo_endpoint:
            raise ValueError(
                "The OIDC provider does not have a userinfo endpoint configured."
            )

        headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient() as client:
            resp = await client.get(userinfo_endpoint, headers=headers)
            resp.raise_for_status()
            raw_userinfo = resp.json()
    if raw_userinfo is None:
        raise ValueError("Could not extract userinfo.")
    log.debug(f"raw_userinfo {raw_userinfo}")

    return UserInfoOidc.from_raw_userinfo(raw_userinfo, oidc_config)


async def create_new_user_default_labels(
    user_id: uuid.UUID,
    label_crud: LabelCRUD,
):
    labels = []
    i = 10
    for label_name in config.NEW_USER_DEFAULT_LABELS:
        labels.append(Label(display_name=label_name, sort_order=i, owner_id=user_id))
        i = i + 10
    await label_crud.create_bulk(labels)
