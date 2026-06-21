"""Unauthenticated client bootstrap config (P0.2 of the sharing frontend).

The sharing endpoints return **404** when a feature is switched off server-side,
so the frontend needs to know which features are enabled *before* it renders the
corresponding UI (rather than showing a button that 404s). This single
unauthenticated endpoint exposes only the public, non-sensitive feature switches
the client gates UI on — no secrets, so it is safe to serve without a session.
"""

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
    )
