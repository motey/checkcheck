import os
from enum import Enum
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple, Type
from urllib.parse import urlparse

from pydantic import (
    Field,
    SecretStr,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from checkcheckserver.utils import slugify_string


class DbBackend(Enum):
    POSTGRES = "postgres"
    SQLITE = "sqlite"


def get_db_backend(url: str) -> DbBackend:
    if url.startswith("postgresql") or url.startswith("asyncpg"):
        return DbBackend.POSTGRES
    return DbBackend.SQLITE


checkcheckserver_folder = Path(__file__).parent
repo_root_folder = checkcheckserver_folder.parent.parent.parent

# The .env file is optional. Override its location with CHECKCHECK_DOT_ENV_FILE.
env_file_path = os.environ.get(
    "CHECKCHECK_DOT_ENV_FILE", str(Path(__file__).parent / ".env")
)

# The YAML config file is optional and layered *under* the environment (see
# settings_customise_sources below). Override its location with
# CHECKCHECK_CONFIG_FILE. In the Docker image this defaults to
# /config/config.yml.
config_file_path = os.environ.get("CHECKCHECK_CONFIG_FILE", "config.yml")


class Config(BaseSettings):
    """Runtime configuration for the CheckCheck server.

    Every setting can come from three places, highest priority first:

    1. an environment variable (nested settings join with a double underscore,
       e.g. ``AUTH_OIDC_PROVIDERS__0__CLIENT_ID``),
    2. a ``config.yml`` file (path from ``CHECKCHECK_CONFIG_FILE``),
    3. the default shown for each field below.

    ``docs/CONFIG_REFERENCE.md`` and ``config.example.yml`` are generated from
    this model. After editing a field, regenerate them with
    ``./gen_config_docs.sh`` so the docs never drift.
    """

    # ── General ───────────────────────────────────────────────────────────────
    APP_NAME: str = Field(
        default="CheckCheck",
        title="Application name",
        description="Display name of the instance. Shown in the UI title and used in some log lines.",
    )
    LOG_LEVEL: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = Field(
        default="INFO",
        title="Log level",
        description=(
            "How much the server logs. `DEBUG` is noisy but the quickest way to see why "
            "something misbehaves; `INFO` is the sensible default for production."
        ),
    )

    # ── Web server ────────────────────────────────────────────────────────────
    # Two distinct concerns, kept deliberately separate:
    #   * where the process *binds* (SERVER_BIND_*) — internal, what the reverse
    #     proxy connects to;
    #   * where the app is *publicly reached* (SERVER_PUBLIC_URL) — external, the
    #     single source of truth for every absolute URL, CORS origin and cookie.
    SERVER_BIND_HOST: str = Field(
        default="localhost",
        title="Bind host",
        description=(
            "The network interface the server process binds to (internal). Use `0.0.0.0` to "
            "accept connections from outside the machine — this is the default inside the "
            "Docker image, and the right value when a reverse proxy on another host or "
            "container connects to it. `localhost` accepts local connections only."
        ),
        examples=["0.0.0.0", "localhost", "127.0.0.1"],
    )
    SERVER_BIND_PORT: int = Field(
        default=8181,
        title="Bind port",
        description="The TCP port the server process binds to (internal).",
        examples=[8181, 8080, 80],
    )
    SERVER_PUBLIC_URL: Optional[str] = Field(
        default=None,
        title="Public base URL",
        description=(
            "The full external base URL where users reach the app, scheme included, e.g. "
            "`https://checklists.example.com`. This is the single source of truth for every "
            "absolute URL the server builds (OIDC redirect URIs, the allowed CORS origin) and "
            "for whether the session cookie is marked Secure. Set it explicitly in production "
            "— behind a reverse proxy the app cannot reliably infer its own external scheme, "
            "host or port from forwarded headers. Include a port only if the app is reached on "
            "a non-standard one (`https://host:8443`); do not include a path. When left unset "
            "it falls back to a URL built from the bind host/port, which is fine for local "
            "development only."
        ),
        examples=[
            "https://checklists.example.com",
            "http://localhost:8181",
        ],
    )
    SERVER_TRUSTED_PROXIES: str = Field(
        default="*",
        title="Trusted proxy IPs for forwarded headers",
        description=(
            "Comma-separated list of upstream IPs allowed to set `X-Forwarded-*` headers "
            "(proto/host/for), or `*` to trust every upstream. Controls whose forwarded "
            "headers uvicorn honours for the client IP and request scheme. The app is designed "
            "to run behind a reverse proxy (e.g. Traefik) reachable only on an internal "
            "network, so this defaults to `*`. Security note: the security-critical absolute "
            "URLs (OIDC redirect) are built from SERVER_PUBLIC_URL, not from these headers, so "
            "a spoofed header cannot redirect a login elsewhere. Narrow this to your proxy's "
            "IP if the container is ever reachable directly."
        ),
    )

    # ── Database ──────────────────────────────────────────────────────────────
    SQL_DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./local.sqlite",
        title="Database URL",
        description=(
            "SQLAlchemy async connection URL. PostgreSQL is the supported production backend; "
            "SQLite is fine for local development and single-process use only. See "
            "`docs/deployment.md` for why (SSE fan-out is single-process on SQLite)."
        ),
        examples=[
            "sqlite+aiosqlite:///./local.sqlite",
            "postgresql+asyncpg://checkcheck:secret@localhost:5432/checkcheck",
        ],
    )

    # ── First administrator ───────────────────────────────────────────────────
    ADMIN_USER_NAME: str = Field(
        default="admin",
        title="Admin username",
        description="Username of the built-in administrator account created on first start.",
    )
    ADMIN_USER_PW: SecretStr = Field(
        title="Admin password",
        description=(
            "Password for the built-in administrator account. Required. This is your first "
            "way into a fresh instance; change it after logging in and never commit it. The "
            "value is only used to create or reset the admin account on start."
        ),
    )
    ADMIN_USER_EMAIL: Optional[str] = Field(
        default=None,
        title="Admin email",
        description="Optional email address for the built-in administrator account.",
        examples=["admin@example.com"],
    )

    # ── Secrets (required) ────────────────────────────────────────────────────
    SERVER_SESSION_SECRET: SecretStr = Field(
        title="Session secret",
        description=(
            "Secret used to sign the browser session cookie. Provide a long random string "
            "and keep it stable: changing it logs everyone out. Required, minimum 64 "
            "characters. Generate one with `openssl rand -hex 32`."
        ),
        min_length=64,
    )
    AUTH_JWT_SECRET: SecretStr = Field(
        title="JWT signing secret",
        description=(
            "Secret used to sign API access tokens (JWT). Provide a long random string, keep "
            "it stable, and keep it different from SERVER_SESSION_SECRET. Required, minimum 64 "
            "characters. Generate one with `openssl rand -hex 32`."
        ),
        min_length=64,
    )

    # ── Sessions & API tokens ─────────────────────────────────────────────────
    AUTH_BASIC_SESSION_LIFETIME_MINUTES: Optional[int] = Field(
        default=60 * 24 * 14,  # two weeks
        title="Local session lifetime (minutes)",
        description="How long a browser session stays valid before the user must log in again. Default is two weeks.",
        examples=[60 * 24 * 14, 60 * 24],
    )
    API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES: Optional[int] = Field(
        default=60 * 24 * 7,  # one week
        title="Default API token lifetime (minutes)",
        description=(
            "How long a newly created API token stays valid. Applies to the token minted on "
            "login and to tokens created in the token manager. Set to null for no default "
            "expiry."
        ),
        examples=[60 * 24 * 7, 60 * 24 * 30],
    )
    API_TOKEN_ALLOW_NEVER_EXPIRE: bool = Field(
        default=True,
        title="Allow never-expiring API tokens",
        description=(
            "Whether users may create API tokens that never expire. When false, every token "
            "must carry an expiry: the 'Never' option is hidden in the UI and rejected by the "
            "server."
        ),
    )

    # ── Sharing ───────────────────────────────────────────────────────────────
    SHARING_ENABLED: bool = Field(
        default=True,
        title="Enable sharing",
        description="Master switch for the sharing feature (collaborators and public links). When false, all sharing endpoints are disabled.",
    )
    SHARING_USER_SEARCH_ENABLED: bool = Field(
        default=True,
        title="Enable user search when sharing",
        description=(
            "Allow signed-in users to search for other users by name when choosing who to "
            "share with. When false the search endpoint is disabled and a sharer must type an "
            "exact identifier."
        ),
    )
    SHARING_PUBLIC_LINKS_ENABLED: bool = Field(
        default=True,
        title="Enable public share links",
        description="Allow owners to create public URLs that let anyone with the link open a card without an account. When false, public-link endpoints are disabled.",
    )
    SHARING_REQUIRE_INVITE_ACCEPT: bool = Field(
        default=False,
        title="Require invite acceptance",
        description=(
            "When true, sharing a card creates a pending invite that the recipient must "
            "accept before the card appears for them. When false, sharing adds the "
            "collaborator immediately."
        ),
    )
    SHARING_PUBLIC_LINK_EMAIL_ENABLED: bool = Field(
        default=False,
        title="Allow mailing a public link",
        description=(
            "Allow a card owner to send an existing public share link to an arbitrary email "
            "address from inside the app. Off by default: it lets signed-in users make the "
            "server send mail to addresses of their choosing. Requires EMAIL_ENABLED and "
            "SHARING_PUBLIC_LINKS_ENABLED as well."
        ),
    )
    SHARING_PUBLIC_LINK_EMAIL_MAX_PER_HOUR: int = Field(
        default=10,
        title="Maximum mailed links per sender per hour",
        description=(
            "How many public links one signed-in user may mail out per hour. This is the "
            "anti-abuse limit on SHARING_PUBLIC_LINK_EMAIL_ENABLED: without it, an account "
            "on this server is a mail relay. Set to 0 to allow an unlimited number, which is "
            "only sensible on a single-user instance."
        ),
    )
    SHARING_INTERNAL_EMAIL_DOMAINS: List[str] = Field(
        default_factory=list,
        title="Email domains that belong to your organisation",
        description=(
            "Domains whose addresses probably belong to people who already have an account "
            "here. When someone types such an address into 'send this link to someone without "
            "an account', the client points out that adding them as a collaborator is "
            "probably what was meant. It is only a hint: sending the link anyway is always "
            "allowed, since a colleague's private address or a device they are not signed in "
            "on is a real case. Compared bare and case-insensitively, subdomains included, so "
            "`example.com` also matches `alice@mail.example.com`. Empty by default, which "
            "means the hint never appears."
        ),
        examples=[["example.com", "example.org"]],
    )

    # ── Email delivery ────────────────────────────────────────────────────────
    # How the server sends mail. Nothing is ever sent while EMAIL_ENABLED is
    # false. When it is true the settings are validated at startup, so a
    # misconfigured instance fails to boot instead of silently swallowing mail.
    EMAIL_ENABLED: bool = Field(
        default=False,
        title="Enable email sending",
        description=(
            "Master switch for outgoing email. When false the server never sends a message "
            "and the email column of the notification settings is hidden in the UI. When "
            "true, EMAIL_FROM_ADDRESS is required (and EMAIL_SMTP_HOST for the `smtp` "
            "transport), checked at startup."
        ),
    )
    EMAIL_TRANSPORT: Literal["smtp", "console", "file", "null"] = Field(
        default="smtp",
        title="Email transport",
        description=(
            "How messages leave the server. `smtp` talks to a real mail server and is the "
            "only production choice. `console` writes the whole message to the log, `file` "
            "drops it as an `.eml` file into EMAIL_FILE_TRANSPORT_DIR (open it with any mail "
            "client), and `null` discards it. The last three exist so local development and "
            "automated tests never need a mail server."
        ),
        examples=["smtp", "console", "file", "null"],
    )
    EMAIL_SMTP_HOST: Optional[str] = Field(
        default=None,
        title="SMTP host",
        description=(
            "Hostname of the mail server to hand messages to. Required when EMAIL_ENABLED is "
            "true and EMAIL_TRANSPORT is `smtp`."
        ),
        examples=["smtp.example.com", "localhost"],
    )
    EMAIL_SMTP_PORT: int = Field(
        default=587,
        title="SMTP port",
        description=(
            "Port of the mail server. 587 is the usual submission port (with STARTTLS), 465 "
            "the implicit-TLS one (use EMAIL_SMTP_SECURITY `ssl`), 25 plain relay on a "
            "trusted network."
        ),
        examples=[587, 465, 25],
    )
    EMAIL_SMTP_USER: Optional[str] = Field(
        default=None,
        title="SMTP username",
        description="Username for SMTP authentication. Leave unset for a relay that needs no login.",
    )
    EMAIL_SMTP_PASSWORD: Optional[SecretStr] = Field(
        default=None,
        title="SMTP password",
        description=(
            "Password for SMTP authentication. Only used together with EMAIL_SMTP_USER. "
            "Supply it through the environment rather than committing it to a config file."
        ),
    )
    EMAIL_SMTP_SECURITY: Literal["starttls", "ssl", "none"] = Field(
        default="starttls",
        title="SMTP connection security",
        description=(
            "How the connection to the mail server is encrypted. `starttls` connects in plain "
            "text and upgrades (the normal choice for port 587), `ssl` is TLS from the first "
            "byte (port 465), `none` is unencrypted and only acceptable for a mail server on "
            "localhost or a trusted private network."
        ),
        examples=["starttls", "ssl", "none"],
    )
    EMAIL_FROM_ADDRESS: Optional[str] = Field(
        default=None,
        title="Sender address",
        description=(
            "The address every message is sent from. Required when EMAIL_ENABLED is true, "
            "checked at startup. Use an address the mail server is actually allowed to send "
            "as, otherwise messages get rejected or land in spam."
        ),
        examples=["checkcheck@example.com", "no-reply@example.com"],
    )
    EMAIL_FROM_NAME: Optional[str] = Field(
        default=None,
        title="Sender display name",
        description=(
            "The human-readable name shown next to the sender address. Falls back to APP_NAME "
            "when unset."
        ),
        examples=["CheckCheck", "My Checklists"],
    )
    EMAIL_REPLY_TO: Optional[str] = Field(
        default=None,
        title="Reply-To address",
        description=(
            "Optional address replies should go to. Set it to a monitored mailbox when "
            "EMAIL_FROM_ADDRESS is a no-reply one; leave unset to omit the header."
        ),
        examples=["support@example.com"],
    )
    EMAIL_FILE_TRANSPORT_DIR: str = Field(
        default="./dev_mail",
        title="Directory for the file transport",
        description=(
            "Where EMAIL_TRANSPORT `file` writes messages as `.eml` files. Created on first "
            "use and must be writable by the server process. Ignored by every other transport."
        ),
    )
    EMAIL_TIMEOUT_SECONDS: int = Field(
        default=20,
        title="SMTP timeout (seconds)",
        description=(
            "How long to wait for the mail server before giving up on a message. The attempt "
            "is retried later, so a short timeout is safer than a long stall."
        ),
    )

    # ── Email design and branding (chunk E7) ──────────────────────────────────
    EMAIL_TEMPLATE_DIR: Optional[str] = Field(
        default=None,
        title="Email template override directory",
        description=(
            "Directory whose templates take priority over the bundled ones, matched by file "
            "name (for example base.html). Any template you do not provide keeps using the "
            "bundled version, so overriding just base.html is enough to rebrand every "
            "message. Every bundled template name is rendered against a dummy context at "
            "startup, from this directory if set, so a broken override fails loudly here "
            "rather than at delivery time; an override that only fails on real data is "
            "logged and the bundled template is used for that one message instead."
        ),
    )
    EMAIL_BRAND_COLOR: str = Field(
        default="#059669",
        title="Brand colour",
        description=(
            "Header and button colour for outgoing email and the unsubscribe page. A hex "
            "triplet such as #059669, validated at startup because it is interpolated "
            "straight into the message markup. The header text and button label colour "
            "(black or white) is chosen automatically for contrast against it."
        ),
        examples=["#059669", "#1d4ed8"],
    )
    EMAIL_LOGO_URL: Optional[str] = Field(
        default=None,
        title="Logo URL",
        description=(
            "Absolute http(s) URL of a logo shown in the header of outgoing email and the "
            "unsubscribe page. Unset shows a text wordmark instead, which is the default for "
            "two reasons: most mail clients block remote images until the reader allows "
            "them, so the design has to work without one anyway, and a remote logo is "
            "fetched by the recipient's own mail client, which tells this instance when a "
            "message was opened. That is your own choice to make about your own users, but "
            "the public-link invitation goes to people who never signed up here, so weigh "
            "that before setting this."
        ),
        examples=["https://example.com/logo.png"],
    )

    # ── Notification delivery ─────────────────────────────────────────────────
    # Which notifications turn into mail, how much they may say, and how the
    # background dispatcher behaves. See docs/plans/EMAIL_NOTIFICATIONS.md.
    NOTIFY_EMAIL_REQUIRE_VERIFIED: bool = Field(
        default=False,
        title="Only mail verified addresses",
        description=(
            "When true, a user whose address is not marked verified receives no mail. Off by "
            "default because in a self-hosted instance addresses come from the identity "
            "provider or an administrator and are already trusted. There is no verification "
            "flow yet, so turning this on currently stops all mail."
        ),
    )
    NOTIFY_EMAIL_CONTENT_MODE: Literal["full", "minimal"] = Field(
        default="full",
        title="How much email messages reveal",
        description=(
            "`full` names the card and the person who acted, which makes the message useful "
            "on its own. `minimal` only says that something happened and links back to the "
            "app. Mail leaves the instance and is stored on someone else's server, so pick "
            "`minimal` when card names are sensitive."
        ),
        examples=["full", "minimal"],
    )
    NOTIFY_EMAIL_SUPPRESS_WINDOW_SECONDS: int = Field(
        default=120,
        title="Delay before an immediate mail goes out (seconds)",
        description=(
            "How long a message waits before being sent. If the user reads the notification in "
            "the app within that window, no mail is sent at all. Keeps people who are looking "
            "at the app right now out of their own inbox. Set to 0 to send without delay."
        ),
    )
    NOTIFY_DEFAULT_MODES: Dict[str, Dict[str, str]] = Field(
        default_factory=lambda: {
            "card_shared": {"in_app": "immediate", "email": "immediate"},
            "card_invited": {"in_app": "immediate", "email": "immediate"},
            "public_link_opened": {"in_app": "immediate", "email": "immediate"},
            "reminder_due": {"in_app": "immediate", "email": "immediate"},
        },
        title="Instance default notification modes",
        description=(
            "The delivery mode used for a notification type and channel when the user has not "
            "chosen one. Keyed by notification type (`card_shared`, `card_invited`, "
            "`public_link_opened`, `reminder_due`), then by channel (`in_app`, `email`, "
            "`webhook`). Modes are `off`, `immediate`, `hourly` and `daily`; `in_app` and "
            "`webhook` accept only `off` and `immediate`. Users can override every entry "
            "unless it is listed in NOTIFY_DISABLED_TYPES."
        ),
        examples=[
            {
                "card_shared": {"in_app": "immediate", "email": "immediate"},
                "card_invited": {"in_app": "immediate", "email": "immediate"},
                "public_link_opened": {"in_app": "immediate", "email": "off"},
                "reminder_due": {"in_app": "immediate", "email": "immediate"},
            }
        ],
    )
    NOTIFY_DISABLED_TYPES: List[str] = Field(
        default_factory=list,
        title="Notification types disabled instance-wide",
        description=(
            "Notification types nobody may receive, whatever their personal settings say. Known "
            "types are `card_shared`, `card_invited`, `public_link_opened` and `reminder_due`. "
            "The settings UI shows those entries as locked by the administrator. Listing "
            "`reminder_due` also stops the reminder scan, so due reminders are not delivered on "
            "any channel. Empty by default."
        ),
        examples=[["public_link_opened"]],
    )
    NOTIFY_DISPATCH_IN_PROCESS: bool = Field(
        default=True,
        title="Send queued messages from the server process",
        description=(
            "When true the server itself drains the queue of pending messages in a "
            "background task, which is what a normal single-container deployment wants. "
            "Turn it off only if something else drains the queue, so that queued messages "
            "are not delivered twice."
        ),
    )
    NOTIFY_DISPATCH_TICK_SECONDS: int = Field(
        default=30,
        title="Dispatcher tick (seconds)",
        description=(
            "How often the background sender looks for due messages. It also wakes up "
            "immediately when something is queued, so this is only the fallback interval."
        ),
    )
    NOTIFY_MAX_ATTEMPTS: int = Field(
        default=6,
        title="Delivery attempts before giving up",
        description=(
            "How often a message is retried after a temporary failure (with growing backoff) "
            "before it is marked failed and left for inspection. Permanent failures, such as "
            "a rejected address, are never retried."
        ),
    )
    NOTIFY_OUTBOX_RETENTION_DAYS: int = Field(
        default=30,
        title="Keep sent messages for (days)",
        description=(
            "How long successfully sent rows stay in the outbox table before they are pruned. "
            "Failed rows are kept longer so an operator can still see what broke."
        ),
    )
    NOTIFY_FEED_RETENTION_DAYS: int = Field(
        default=180,
        title="Keep in-app notifications for (days)",
        description=(
            "How long read notifications stay in the in-app feed before they are pruned. The "
            "feed grows forever otherwise. Set to 0 to keep everything."
        ),
    )
    NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR: int = Field(
        default=20,
        title="Maximum emails per user per hour",
        description=(
            "A blunt backstop against flooding a single recipient. Messages over the limit are "
            "dropped rather than queued forever."
        ),
    )
    NOTIFY_WEBHOOK_ENABLED: bool = Field(
        default=False,
        title="Enable per-user webhooks",
        description=(
            "Master switch for the webhook channel, which POSTs a small JSON body to a URL "
            "each user configures for themselves. Off by default: it lets signed-in users make "
            "the server issue outbound HTTP requests."
        ),
    )
    NOTIFY_WEBHOOK_ALLOW_PRIVATE_IPS: bool = Field(
        default=False,
        title="Allow webhooks to private addresses",
        description=(
            "When false, webhook URLs resolving to loopback, link-local or private network "
            "ranges are refused, which is what stops a user pointing a webhook at services "
            "reachable only from the server. Turn it on only for a trusted, single-user "
            "instance on a private network."
        ),
    )

    # ── Push notifications (browser and installed-PWA, chunk P1) ─────────────
    # OS-level notifications delivered through the Web Push standard. See
    # docs/plans/SYSTEM_NOTIFICATIONS.md.
    NOTIFY_PUSH_ENABLED: bool = Field(
        default=True,
        title="Enable push notifications",
        description=(
            "Master switch for the push channel. When false the server never sends a push "
            "message, no subscription can be created, and the push column of the "
            "notification settings is hidden in the UI. On by default and needs no "
            "configuration: an instance with no VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY set "
            "generates its own key pair on first boot and keeps it in the database. Whether "
            "a browser will actually subscribe is decided by the browser, which requires the "
            "page to be a secure context: https, or http on localhost."
        ),
    )
    VAPID_PUBLIC_KEY: Optional[str] = Field(
        default=None,
        title="VAPID public key",
        description=(
            "The application server's public key, base64url-encoded. Optional: leave it "
            "unset and the instance generates a key pair for itself on first boot. Set it "
            "only to pin a pair of your own, in which case VAPID_PRIVATE_KEY has to be set "
            "too (generate both with ./gen_vapid_keys.sh) and nothing is generated. Not a "
            "secret: served to the client through /api/public-config, which is what lets a "
            "browser subscribe."
        ),
    )
    VAPID_PRIVATE_KEY: Optional[SecretStr] = Field(
        default=None,
        title="VAPID private key",
        description=(
            "The application server's private key, base64url-encoded. Optional, and set "
            "together with VAPID_PUBLIC_KEY or not at all: an instance with neither "
            "generates its own pair on first boot. Never leaves the server; signs the VAPID "
            "JWT that proves a push request came from this instance. Supply it through the "
            "environment rather than committing it to a config file. Generate with "
            "./gen_vapid_keys.sh."
        ),
    )
    VAPID_CONTACT_EMAIL: Optional[str] = Field(
        default=None,
        title="VAPID contact address",
        description=(
            "Contact address for the push services this instance calls, in case one needs "
            "to reach an operator about abuse. Becomes the VAPID JWT's `sub` claim as "
            "`mailto:<address>`. Optional: without it the claim falls back to "
            "ADMIN_USER_EMAIL, and then to SERVER_PUBLIC_URL, both of which a push service "
            "accepts."
        ),
        examples=["admin@example.com"],
    )
    NOTIFY_PUSH_CONTENT_MODE: Literal["full", "minimal"] = Field(
        default="minimal",
        title="How much push notifications reveal",
        description=(
            "`full` names the card and the person who acted, `minimal` only says that "
            "something happened and links back to the app. Independent of "
            "NOTIFY_EMAIL_CONTENT_MODE and defaults to `minimal`: a push notification sits "
            "on a lock screen, which can be visible to anyone near the device, a more "
            "exposed surface than an email behind an inbox app's own unlock."
        ),
        examples=["full", "minimal"],
    )
    NOTIFY_PUSH_TTL_SECONDS: int = Field(
        default=86400,
        title="Push message time-to-live (seconds)",
        description=(
            "How long a push service should hold a message for a device that is offline, "
            "before giving up on delivering it. Maps to the Web Push `TTL` header."
        ),
    )
    NOTIFY_PUSH_MAX_PER_USER_PER_HOUR: int = Field(
        default=20,
        title="Maximum push notifications per user per hour",
        description=(
            "A blunt backstop against flooding a single recipient's devices. Messages over "
            "the limit are dropped rather than queued forever. A phone buzzing repeatedly is "
            "a worse experience than a full inbox, so this exists even though the equivalent "
            "email limit is rarely hit."
        ),
    )

    # ── Local (username + password) authentication ────────────────────────────
    AUTH_BASIC_LOGIN_IS_ENABLED: bool = Field(
        default=True,
        title="Enable local login",
        description="Allow users stored in the local database to log in with a username and password. You can disable this when authenticating exclusively through an external OIDC provider.",
    )
    AUTH_BASIC_USER_DB_REGISTER_ENABLED: bool = Field(
        default=False,
        title="Allow self-registration",
        description=(
            "Allow anyone to create a local account through the public registration endpoint. "
            "Off by default. Enable only when you have anti-abuse controls in place, since "
            "there is no email verification step yet."
        ),
    )

    # ── OpenID Connect (external login) ───────────────────────────────────────
    class OpenIDConnectProvider(BaseSettings):
        """A single external OpenID Connect provider.

        Configure one entry per provider under AUTH_OIDC_PROVIDERS. See
        `docs/configuration.md` for a full worked example.
        """

        ENABLED: bool = Field(
            default=False,
            title="Enabled",
            description="Whether this provider is active. Disabled providers are ignored.",
        )
        PROVIDER_DISPLAY_NAME: str = Field(
            default="My OpenID Connect Login",
            title="Display name",
            description="Human-readable name shown on the login button. Also slugified into the provider's callback path, so keep it unique across providers.",
            examples=["Company SSO", "Authentik"],
        )
        AUTO_LOGIN: Optional[bool] = Field(
            default=False,
            title="Auto-redirect to this provider",
            description="When true the login page immediately redirects to this provider instead of showing the local login form. Use only with a single provider.",
        )
        CONFIGURATION_ENDPOINT: str = Field(
            title="Discovery endpoint",
            description="The provider's OpenID Connect discovery URL (usually ends in `/.well-known/openid-configuration`). Required.",
            examples=[
                "https://sso.example.com/application/o/checkcheck/.well-known/openid-configuration"
            ],
        )
        CLIENT_ID: str = Field(
            title="Client ID",
            description="OAuth2 client ID issued by the provider for this application. Required.",
        )
        CLIENT_SECRET: SecretStr = Field(
            title="Client secret",
            description="OAuth2 client secret belonging to CLIENT_ID. Required. Keep it out of version control; supply it through the environment where possible.",
        )
        SCOPES: List[str] = Field(
            default=["openid", "profile", "email", "offline_access"],
            title="Requested scopes",
            description="Scopes requested from the provider. `offline_access` is needed to receive a refresh token so the session can be kept alive without re-login. The provider must also be configured to grant this scope (in Authentik, add the built-in OpenID 'offline_access' scope mapping to the provider's selected scopes) — if it is not granted, no refresh token is issued and the session bounces to the login screen every access-token lifetime.",
        )
        USER_NAME_ATTRIBUTE: str = Field(
            default="preferred_username",
            title="Username claim",
            description="The token claim that holds a stable, unique username for the user.",
        )
        USER_DISPLAY_NAME_ATTRIBUTE: str = Field(
            default="name",
            title="Display-name claim",
            description=(
                "The token claim that holds the user's human-readable display name. "
                "Defaults to `name`, the standard OpenID Connect `profile`-scope claim "
                "emitted by virtually every provider (Keycloak, Google, Okta, Auth0, "
                "Azure AD, ...). Authentik also serves the user's name in the `name` "
                "claim once the `profile` scope is requested, so the default works "
                "there unchanged. If the configured claim is absent the display name "
                "falls back to the username."
            ),
        )
        USER_MAIL_ATTRIBUTE: str = Field(
            default="email",
            title="Email claim",
            description="The token claim that holds the user's email address.",
        )
        USER_GROUPS_ATTRIBUTE: str = Field(
            default="groups",
            title="Groups claim",
            description=(
                "The token claim that holds the user's group memberships. Only consulted "
                "when ROLE_MAPPING or RESTRICT_USER_SEARCH_TO_OWN_GROUPS is in use. Unlike "
                "the username/email/name claims there is no standard OIDC claim for groups; "
                "`groups` is the most common convention (Authentik, Okta, Keycloak with a "
                "groups mapper) and is the default here. Some providers differ: Azure AD / "
                "Entra emits group object-IDs (not names) under `groups`, Auth0 uses a "
                "namespaced custom claim (e.g. `https://<app>/groups`), and Google emits no "
                "groups at all — set this to match your provider, and make sure the claim is "
                "actually released (often a dedicated scope or claim mapping)."
            ),
        )
        AUTO_CREATE_AUTHORIZED_USER: bool = Field(
            default=True,
            title="Auto-create users on first login",
            description="Create a local account the first time a user authenticates through this provider, instead of requiring the account to exist beforehand.",
        )
        PREFIX_USERNAME_WITH_PROVIDER_SLUG: bool = Field(
            default=False,
            title="Prefix usernames with provider slug",
            description="Prefix usernames coming from this provider with its slug to avoid collisions when several providers can produce the same username.",
        )
        ROLE_MAPPING: Dict[str, List[str]] = Field(
            default_factory=dict,
            title="Group-to-role mapping",
            description=(
                "Map provider group names to CheckCheck roles. Members of a listed group are "
                "granted the mapped roles on login. Example: "
                '`{"sso-admins": ["admin"], "sso-usermanagers": ["usermanager"]}`.'
            ),
            examples=[{"sso-admins": ["admin"]}],
        )
        RESTRICT_USER_SEARCH_TO_OWN_GROUPS: bool = Field(
            default=False,
            title="Restrict user search to shared groups",
            description=(
                "When true, a user authenticated through this provider can only find other "
                "users who share at least one group with them when picking share targets. "
                "Requires USER_GROUPS_ATTRIBUTE to be delivered by the provider."
            ),
        )

        def get_provider_name_slug(self) -> str:
            return slugify_string(self.PROVIDER_DISPLAY_NAME)

        def get_scopes_as_string(self) -> str:
            return " ".join(self.SCOPES)

    AUTH_OIDC_PROVIDERS: Optional[List[OpenIDConnectProvider]] = Field(
        default_factory=list,
        title="OpenID Connect providers",
        description=(
            "List of external OpenID Connect providers users may log in with. Empty by "
            "default (local login only). One entry per provider; see the example below and "
            "the walkthrough in `docs/configuration.md`. When any provider is set, also set "
            "AUTH_OIDC_TOKEN_STORAGE_SECRET."
        ),
        examples=[
            [
                {
                    "ENABLED": True,
                    "PROVIDER_DISPLAY_NAME": "Company SSO",
                    "CONFIGURATION_ENDPOINT": "https://sso.example.com/application/o/checkcheck/.well-known/openid-configuration",
                    "CLIENT_ID": "checkcheck",
                    "CLIENT_SECRET": "the-client-secret",
                    "SCOPES": ["openid", "profile", "email", "offline_access"],
                    "USER_NAME_ATTRIBUTE": "preferred_username",
                    "USER_MAIL_ATTRIBUTE": "email",
                    "AUTO_CREATE_AUTHORIZED_USER": True,
                    "ROLE_MAPPING": {"sso-admins": ["admin"]},
                }
            ]
        ],
    )
    AUTH_OIDC_TOKEN_STORAGE_SECRET: SecretStr = Field(
        default="change_me_only_relevant_when_an_oidc_provider_is_configured",
        title="OIDC token storage secret",
        description=(
            "Secret used to encrypt OIDC access and refresh tokens before storing them in the "
            "database. Only used when at least one OIDC provider is configured; set a long "
            "random string in that case. Generate one with `openssl rand -hex 32`."
        ),
    )

    # ── Provisioning & defaults ───────────────────────────────────────────────
    NEW_USER_DEFAULT_LABELS: Optional[List[str]] = Field(
        default=["Work", "Private", "Inspiration"],
        title="Default labels for new users",
        description="Labels created automatically for every new account. Set to an empty list to start users with no labels.",
        examples=[["Work", "Private", "Inspiration"], []],
    )
    APP_PROVISIONING_DATA_YAML_FILES: Optional[List[str]] = Field(
        default_factory=list,
        title="Extra provisioning data files",
        description=(
            "Optional list of YAML files whose contents are loaded into the database on "
            "start. Use this to seed an instance with predefined data. Most deployments leave "
            "it empty."
        ),
        examples=[["/config/seed_data.yaml"]],
    )

    # ── Export ────────────────────────────────────────────────────────────────
    EXPORT_CACHE_DIR: str = Field(
        default="./export_cache",
        title="Export cache directory",
        description="Directory where the results of export jobs (CSV, JSON) are written. Must be writable by the server process.",
    )

    # ── Development & advanced switches ────────────────────────────────────────
    # Everything below has a sensible default that most deployments never touch.
    # These are debugging aids, local-development conveniences, deprecated
    # settings, and internal paths the Docker image manages for you.
    SET_SESSION_COOKIE_SECURE: Optional[bool] = Field(
        default=None,
        title="Secure session cookie",
        description=(
            "When true the session cookie is only sent over HTTPS. Leave unset (the default) "
            "to derive it from SERVER_PUBLIC_URL: Secure on an `https` URL, not Secure on "
            "`http` (so login works over plain-HTTP localhost without a manual override, and "
            "is Secure in production). Set it explicitly only to override that."
        ),
    )
    CLIENT_URL: Optional[str] = Field(
        default=None,
        title="Extra allowed origin",
        description=(
            "An additional browser origin allowed to call the API (CORS), on top of the "
            "server's own URL. Only needed when the frontend is served from a different "
            "origin than the backend, for example during frontend development."
        ),
        examples=["http://localhost:3000"],
    )
    DEBUG_SQL: bool = Field(
        default=False,
        title="Log SQL statements",
        description="When true, the database engine prints every SQL query to the log. Very verbose; leave off unless debugging queries.",
    )
    SERVER_UVICORN_LOG_LEVEL: Optional[str] = Field(
        default=None,
        title="Uvicorn log level",
        description="Log level for the uvicorn web server. Falls back to LOG_LEVEL when unset.",
        examples=["info", "warning"],
    )
    AUTH_JWT_ALGORITHM: Literal["HS256"] = Field(
        default="HS256",
        title="JWT algorithm",
        description="Algorithm used to sign JWT tokens. Only HS256 is supported at the moment.",
    )
    AUTH_ACCESS_TOKEN_EXPIRES_MINUTES: Optional[int] = Field(
        default=60 * 24 * 14,
        title="Access token lifetime (minutes, deprecated)",
        description="Deprecated. Use API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES instead.",
        deprecated=True,
    )
    ADMIN_ROLE_NAME: str = Field(
        default="admin",
        title="Admin role name",
        description="Name of the role that grants full administrative access. Rarely needs changing.",
    )
    USERMANAGER_ROLE_NAME: str = Field(
        default="usermanager",
        title="User-manager role name",
        description="Name of the role that may manage other users without full admin rights. Rarely needs changing.",
    )
    APP_PROVISIONING_DEFAULT_DATA_YAML_FILE: str = Field(
        default=str(Path(checkcheckserver_folder, "default_data.yaml")),
        title="Default provisioning data file",
        description=(
            "Baseline data (roles and similar) always loaded on start. This ships with the "
            "app and normally should not be changed. To seed your own data use "
            "APP_PROVISIONING_DATA_YAML_FILES instead."
        ),
    )
    DOCKER_MODE: bool = Field(
        default=False,
        title="Running inside the official Docker image",
        description="Set automatically by the Docker image. Only affects a few path defaults; do not set it by hand outside a container.",
    )
    FRONTEND_FILES_DIR: str = Field(
        default="CheckCheck/frontend/.output/public",
        title="Frontend files directory",
        description="Directory of the built frontend (the generated Nuxt output containing index.html). The Docker image sets this for you.",
    )
    DB_MIGRATION_ALEMBIC_CONFIG_FILE: str = Field(
        default=str(Path(repo_root_folder, "alembic.ini")),
        title="Alembic config file",
        description="Path to the Alembic configuration used to run database migrations on start. The default resolves next to the source tree; rarely changed.",
    )

    @model_validator(mode="after")
    def _resolve_public_address(self) -> "Config":
        """Resolve SERVER_PUBLIC_URL and the session-cookie Secure flag.

        When SERVER_PUBLIC_URL is not set explicitly it falls back to a URL built
        from the bind host/port — local development only. The cookie Secure flag,
        when left unset, follows the resolved scheme.
        """
        if self.SERVER_PUBLIC_URL is None:
            # Local-dev fallback: reachable on the bound address.
            host = (
                "localhost"
                if self.SERVER_BIND_HOST in ("0.0.0.0", "")
                else self.SERVER_BIND_HOST
            )
            port = (
                "" if self.SERVER_BIND_PORT in (80, 443) else f":{self.SERVER_BIND_PORT}"
            )
            proto = "https" if self.SERVER_BIND_PORT == 443 else "http"
            self.SERVER_PUBLIC_URL = f"{proto}://{host}{port}"

        self.SERVER_PUBLIC_URL = self.SERVER_PUBLIC_URL.rstrip("/")

        if self.SET_SESSION_COOKIE_SECURE is None:
            self.SET_SESSION_COOKIE_SECURE = self.SERVER_PUBLIC_URL.startswith("https://")

        return self

    @model_validator(mode="after")
    def _resolve_and_validate_email(self) -> "Config":
        """Fill EMAIL_FROM_NAME from APP_NAME and reject a half-configured mailer.

        A mail setup that is switched on but missing a sender address (or an SMTP
        host) can only fail later, once per message, inside a background task
        where nobody looks. Fail at boot instead: the instance does not start
        until the operator finishes the configuration.
        """
        if self.EMAIL_FROM_NAME is None:
            self.EMAIL_FROM_NAME = self.APP_NAME

        if not self.EMAIL_ENABLED:
            return self

        if not self.EMAIL_FROM_ADDRESS:
            raise ValueError(
                "EMAIL_ENABLED is true but EMAIL_FROM_ADDRESS is not set. "
                "Set the address messages are sent from, or set EMAIL_ENABLED to false."
            )
        if self.EMAIL_TRANSPORT == "smtp" and not self.EMAIL_SMTP_HOST:
            raise ValueError(
                "EMAIL_ENABLED is true and EMAIL_TRANSPORT is 'smtp', but EMAIL_SMTP_HOST is "
                "not set. Point it at your mail server, or pick another EMAIL_TRANSPORT "
                "('console' or 'file' for local development)."
            )
        return self

    @model_validator(mode="after")
    def _validate_and_check_email_branding(self) -> "Config":
        """Validate the chunk E7 branding settings and render every template.

        EMAIL_BRAND_COLOR and EMAIL_LOGO_URL land straight in message markup, so a
        malformed value must not reach the mailer. Rendering every bundled
        template name here (through the override directory when one is set) is
        section 0.5's "fail loudly at startup" rule applied to templates: a
        broken operator override, or a bug in a bundled template, must stop the
        instance from starting rather than dead-lettering mail the first time
        somebody's card gets shared.
        """
        from checkcheckserver.notify import branding as _branding

        try:
            _branding.validate_hex_color(self.EMAIL_BRAND_COLOR)
        except ValueError as exc:
            raise ValueError(
                f"EMAIL_BRAND_COLOR must be a hex color in the form #rrggbb, got "
                f"{self.EMAIL_BRAND_COLOR!r}."
            ) from exc

        if self.EMAIL_LOGO_URL:
            parsed = urlparse(self.EMAIL_LOGO_URL)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError(
                    f"EMAIL_LOGO_URL must be an absolute http(s) URL, got "
                    f"{self.EMAIL_LOGO_URL!r}."
                )

        if self.EMAIL_TEMPLATE_DIR and not Path(self.EMAIL_TEMPLATE_DIR).is_dir():
            raise ValueError(
                f"EMAIL_TEMPLATE_DIR {self.EMAIL_TEMPLATE_DIR!r} is not a directory."
            )

        from checkcheckserver.notify import templating as _templating

        _templating.check_all_templates(self)
        return self

    @model_validator(mode="after")
    def _validate_push(self) -> "Config":
        """Reject a half-configured push setup at startup, not on first delivery.

        Same reasoning as ``_resolve_and_validate_email``: a queued push message
        can only fail once per row, in a background task where nobody is looking,
        so a malformed VAPID key must stop the instance from starting instead.

        What is *not* an error since chunk K1 is setting neither half. That used
        to be the common case and it refused to boot; it now means "generate one
        for me", which ``notify/vapid.py`` does on first boot (decision 3 of
        ``docs/plans/PUSH_KEYS_AND_SHARE_SETTING.md``). Setting exactly one half
        is still an error, because an operator who set one meant to set both, and
        silently generating a pair over the top of a half-configured one would
        make a typo look like it worked.

        The contact address is no longer required either: the JWT's ``sub``
        claim has a fallback (``notify/vapid.resolve_subject``). Nothing here can
        touch the database or the event loop, so generation itself belongs in the
        lifespan, not in this validator.
        """
        if not self.NOTIFY_PUSH_ENABLED:
            return self

        if not self.VAPID_PUBLIC_KEY and not self.VAPID_PRIVATE_KEY:
            return self
        if not self.VAPID_PUBLIC_KEY or not self.VAPID_PRIVATE_KEY:
            missing, given = (
                ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY")
                if not self.VAPID_PUBLIC_KEY
                else ("VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY")
            )
            raise ValueError(
                f"{given} is set but {missing} is not. Both halves of a VAPID key pair "
                "have to come from the same ./gen_vapid_keys.sh run. Set both, or unset "
                "both and let this instance generate a pair for itself."
            )

        from checkcheckserver.notify import push as _push

        try:
            _push.validate_vapid_keys(
                public_key=self.VAPID_PUBLIC_KEY,
                private_key=self.VAPID_PRIVATE_KEY.get_secret_value(),
            )
        except ValueError as exc:
            raise ValueError(
                f"VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY are not a usable key pair: {exc}"
            ) from exc
        return self

    def get_server_url(self) -> str:
        return self.SERVER_PUBLIC_URL

    @property
    def db_backend(self) -> DbBackend:
        return get_db_backend(self.SQL_DATABASE_URL)

    @property
    def POSTGRES_DSN(self) -> str:
        return self.SQL_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=env_file_path,
        env_file_encoding="utf-8",
        yaml_file=config_file_path,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        # Priority high -> low. The YAML file sits underneath the environment so
        # secrets can stay in env vars while the non-secret shape lives in
        # config.yml. A missing config file is ignored.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
        )
