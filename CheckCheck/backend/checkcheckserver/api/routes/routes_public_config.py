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

from checkcheckserver import __version__ as server_version
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.notify import vapid

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
    sharing_public_link_email_enabled: bool = Field(
        description=(
            "Whether an owner may mail an existing public link to an address of their "
            "choosing. When false, hide that field inside the public-links section. True "
            "only when the instance can also send mail at all."
        ),
    )
    api_token_default_expiry_days: Optional[int] = Field(
        default=None,
        description="Default API-key validity in whole days, surfaced so the token manager can pre-select it. Null when the server default is no expiry.",
    )
    api_token_allow_never_expire: bool = Field(
        description="Whether users may create never-expiring API keys. When false, the token manager hides the 'Never' option.",
    )
    server_version: str = Field(
        description="The running server's version string (from checkcheckserver.__version__), surfaced so the web client can display it.",
    )
    email_enabled: bool = Field(
        description="Whether the instance can send email at all. When false, the notification settings hide the email column entirely (there is nothing to configure).",
    )
    webhook_enabled: bool = Field(
        description="Whether per-user notification webhooks are allowed. When false, the notification settings hide the webhook column.",
    )
    push_enabled: bool = Field(
        description=(
            "Whether the instance can send push notifications at all. When false, the "
            "notification settings hide the push column and no subscription can be created."
        ),
    )
    vapid_public_key: Optional[str] = Field(
        default=None,
        description=(
            "The instance's VAPID public key, base64url-encoded. Not a secret; the client "
            "needs it as the applicationServerKey when subscribing. Either the configured "
            "key or the one this instance generated for itself on first boot. Null when "
            "push_enabled is false, and on the rare instance where the key could not be "
            "resolved at startup (the server log says why)."
        ),
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
    keys = vapid.get_keys()
    vapid_key = keys.public_key if keys is not None else None
    return PublicConfig(
        sharing_enabled=config.SHARING_ENABLED,
        sharing_public_links_enabled=config.SHARING_PUBLIC_LINKS_ENABLED,
        sharing_user_search_enabled=config.SHARING_USER_SEARCH_ENABLED,
        sharing_require_invite_accept=config.SHARING_REQUIRE_INVITE_ACCEPT,
        # Reported as the *endpoint* behaves: it needs the sharing switches, its
        # own switch and a working mailer, so a client that only saw its own flag
        # would render a field that 404s.
        sharing_public_link_email_enabled=(
            config.SHARING_ENABLED
            and config.SHARING_PUBLIC_LINKS_ENABLED
            and config.SHARING_PUBLIC_LINK_EMAIL_ENABLED
            and config.EMAIL_ENABLED
        ),
        api_token_default_expiry_days=_default_api_token_expiry_days(),
        api_token_allow_never_expire=config.API_TOKEN_ALLOW_NEVER_EXPIRE,
        server_version=server_version,
        email_enabled=config.EMAIL_ENABLED,
        webhook_enabled=config.NOTIFY_WEBHOOK_ENABLED,
        push_enabled=config.NOTIFY_PUSH_ENABLED,
        # From the holder the dispatcher's lifespan filled at startup, never
        # from `config` directly: since chunk K1 the key an instance signs with
        # is usually one it generated for itself, which only the database knows.
        # `resolve_at_startup` returns nothing at all when push is off, so this
        # needs no second flag check.
        vapid_public_key=vapid_key,
    )
