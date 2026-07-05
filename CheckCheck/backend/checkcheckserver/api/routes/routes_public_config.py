"""Unauthenticated client bootstrap config (P0.2 of the sharing frontend).

The sharing endpoints return **404** when a feature is switched off server-side,
so the frontend needs to know which features are enabled *before* it renders the
corresponding UI (rather than showing a button that 404s). This single
unauthenticated endpoint exposes only the public, non-sensitive feature switches
the client gates UI on — no secrets, so it is safe to serve without a session.
"""

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

config = Config()
log = get_logger()

fast_api_public_config_router: APIRouter = APIRouter()


class PublicConfig(BaseModel):
    """The subset of server feature flags the web client needs to decide which
    sharing UI to render. Mirrors the like-named ``Config`` switches."""

    sharing_enabled: bool = Field(
        description="Master switch for card sharing (collaborators + public links). When false, the whole share UI is hidden.",
    )
    sharing_public_links_enabled: bool = Field(
        description="Whether owners may create anonymous public share links. When false, hide the public-links section.",
    )
    sharing_user_search_enabled: bool = Field(
        description="Whether users may search for other users when picking a share target. When false, hide the user-search field.",
    )
    sharing_require_invite_accept: bool = Field(
        description="Whether a share creates a pending invite the target must accept before gaining access (invite mode).",
    )
    api_token_default_expiry_days: Optional[int] = Field(
        default=None,
        description="Default API-key validity in whole days, surfaced so the token manager can pre-select it. Null when the server default is no expiry.",
    )
    api_token_allow_never_expire: bool = Field(
        description="Whether users may create never-expiring API keys. When false, the token manager hides the 'Never' option.",
    )


def _default_api_token_expiry_days() -> Optional[int]:
    """The server's default API-key validity expressed in whole days (rounded,
    min 1), or None when the server default is no expiry — used to pre-select the
    matching option in the token manager instead of an abstract 'server default'."""
    minutes = config.API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES
    if minutes is None:
        return None
    return max(1, round(minutes / (60 * 24)))


@fast_api_public_config_router.get(
    "/public-config",
    response_model=PublicConfig,
    description="Public, unauthenticated feature flags the web client gates its sharing UI on.",
)
async def get_public_config() -> PublicConfig:
    return PublicConfig(
        sharing_enabled=config.SHARING_ENABLED,
        sharing_public_links_enabled=config.SHARING_PUBLIC_LINKS_ENABLED,
        sharing_user_search_enabled=config.SHARING_USER_SEARCH_ENABLED,
        sharing_require_invite_accept=config.SHARING_REQUIRE_INVITE_ACCEPT,
        api_token_default_expiry_days=_default_api_token_expiry_days(),
        api_token_allow_never_expire=config.API_TOKEN_ALLOW_NEVER_EXPIRE,
    )
