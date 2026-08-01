<!-- GENERATED FILE - do not edit by hand.
     Regenerate with `./gen_config_docs.sh` after changing config.py.
     A readable introduction to configuration lives in docs/configuration.md. -->

# Configuration Reference - `Config`

This document is auto-generated from the pydantic-settings model. All settings can be provided via the YAML config file or overridden with environment variables.

---

## `APP_NAME`

*Application name*

Display name of the instance. Shown in the UI title and used in some log lines.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"CheckCheck"` |
| Environment variable | `APP_NAME` |

---

## `LOG_LEVEL`

*Log level*

How much the server logs. `DEBUG` is noisy but the quickest way to see why something misbehaves; `INFO` is the sensible default for production.

| Property | Value |
|---|---|
| Type | Enum |
| Required | No |
| Default | `"INFO"` |
| Allowed values | `CRITICAL` · `ERROR` · `WARNING` · `INFO` · `DEBUG` |
| Environment variable | `LOG_LEVEL` |

---

## `SERVER_BIND_HOST`

*Bind host*

The network interface the server process binds to (internal). Use `0.0.0.0` to accept connections from outside the machine - this is the default inside the Docker image, and the right value when a reverse proxy on another host or container connects to it. `localhost` accepts local connections only.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"localhost"` |
| Environment variable | `SERVER_BIND_HOST` |

**Examples:**

*Example 1:*

```yaml
SERVER_BIND_HOST: 0.0.0.0
```

*Example 2:*

```yaml
SERVER_BIND_HOST: localhost
```

*Example 3:*

```yaml
SERVER_BIND_HOST: 127.0.0.1
```

---

## `SERVER_BIND_PORT`

*Bind port*

The TCP port the server process binds to (internal).

| Property | Value |
|---|---|
| Type | int |
| Required | No |
| Default | `8181` |
| Environment variable | `SERVER_BIND_PORT` |

**Examples:**

*Example 1:*

```yaml
SERVER_BIND_PORT: 8181
```

*Example 2:*

```yaml
SERVER_BIND_PORT: 8080
```

*Example 3:*

```yaml
SERVER_BIND_PORT: 80
```

---

## `SERVER_PUBLIC_URL`

*Public base URL*

The full external base URL where users reach the app, scheme included, e.g. `https://checklists.example.com`. This is the single source of truth for every absolute URL the server builds (OIDC redirect URIs, the allowed CORS origin) and for whether the session cookie is marked Secure. Set it explicitly in production - behind a reverse proxy the app cannot reliably infer its own external scheme, host or port from forwarded headers. Include a port only if the app is reached on a non-standard one (`https://host:8443`); do not include a path. When left unset it falls back to a URL built from the bind host/port, which is fine for local development only.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `null` |
| Environment variable | `SERVER_PUBLIC_URL` |

**Examples:**

*Example 1:*

```yaml
SERVER_PUBLIC_URL: https://checklists.example.com
```

*Example 2:*

```yaml
SERVER_PUBLIC_URL: http://localhost:8181
```

---

## `SERVER_TRUSTED_PROXIES`

*Trusted proxy IPs for forwarded headers*

Comma-separated list of upstream IPs allowed to set `X-Forwarded-*` headers (proto/host/for), or `*` to trust every upstream. Controls whose forwarded headers uvicorn honours for the client IP and request scheme. The app is designed to run behind a reverse proxy (e.g. Traefik) reachable only on an internal network, so this defaults to `*`. Security note: the security-critical absolute URLs (OIDC redirect) are built from SERVER_PUBLIC_URL, not from these headers, so a spoofed header cannot redirect a login elsewhere. Narrow this to your proxy's IP if the container is ever reachable directly.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"*"` |
| Environment variable | `SERVER_TRUSTED_PROXIES` |

---

## `SQL_DATABASE_URL`

*Database URL*

SQLAlchemy async connection URL. PostgreSQL is the supported production backend; SQLite is fine for local development and single-process use only. See `docs/deployment.md` for why (SSE fan-out is single-process on SQLite).

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"sqlite+aiosqlite:///./local.sqlite"` |
| Environment variable | `SQL_DATABASE_URL` |

**Examples:**

*Example 1:*

```yaml
SQL_DATABASE_URL: sqlite+aiosqlite:///./local.sqlite
```

*Example 2:*

```yaml
SQL_DATABASE_URL: postgresql+asyncpg://checkcheck:secret@localhost:5432/checkcheck
```

---

## `ADMIN_USER_NAME`

*Admin username*

Username of the built-in administrator account created on first start.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"admin"` |
| Environment variable | `ADMIN_USER_NAME` |

---

## `ADMIN_USER_PW`

*Admin password*

Password for the built-in administrator account. Required. This is your first way into a fresh instance; change it after logging in and never commit it. The value is only used to create or reset the admin account on start.

| Property | Value |
|---|---|
| Type | Object |
| Required | **Yes** |
| Environment variable | `ADMIN_USER_PW` |

---

## `ADMIN_USER_EMAIL`

*Admin email*

Optional email address for the built-in administrator account.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `null` |
| Environment variable | `ADMIN_USER_EMAIL` |

**Examples:**

```yaml
ADMIN_USER_EMAIL: admin@example.com
```

---

## `SERVER_SESSION_SECRET`

*Session secret*

Secret used to sign the browser session cookie. Provide a long random string and keep it stable: changing it logs everyone out. Required, minimum 64 characters. Generate one with `openssl rand -hex 32`.

| Property | Value |
|---|---|
| Type | Object |
| Required | **Yes** |
| Constraints | MinLen(min_length=64) |
| Environment variable | `SERVER_SESSION_SECRET` |

---

## `AUTH_JWT_SECRET`

*JWT signing secret*

Secret used to sign API access tokens (JWT). Provide a long random string, keep it stable, and keep it different from SERVER_SESSION_SECRET. Required, minimum 64 characters. Generate one with `openssl rand -hex 32`.

| Property | Value |
|---|---|
| Type | Object |
| Required | **Yes** |
| Constraints | MinLen(min_length=64) |
| Environment variable | `AUTH_JWT_SECRET` |

---

## `AUTH_BASIC_SESSION_LIFETIME_MINUTES`

*Local session lifetime (minutes)*

How long a browser session stays valid before the user must log in again. Default is two weeks.

| Property | Value |
|---|---|
| Type | int |
| Required | No |
| Default | `20160` |
| Environment variable | `AUTH_BASIC_SESSION_LIFETIME_MINUTES` |

**Examples:**

*Example 1:*

```yaml
AUTH_BASIC_SESSION_LIFETIME_MINUTES: 20160
```

*Example 2:*

```yaml
AUTH_BASIC_SESSION_LIFETIME_MINUTES: 1440
```

---

## `API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES`

*Default API token lifetime (minutes)*

How long a newly created API token stays valid. Applies to the token minted on login and to tokens created in the token manager. Set to null for no default expiry.

| Property | Value |
|---|---|
| Type | int |
| Required | No |
| Default | `10080` |
| Environment variable | `API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES` |

**Examples:**

*Example 1:*

```yaml
API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES: 10080
```

*Example 2:*

```yaml
API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES: 43200
```

---

## `API_TOKEN_ALLOW_NEVER_EXPIRE`

*Allow never-expiring API tokens*

Whether users may create API tokens that never expire. When false, every token must carry an expiry: the 'Never' option is hidden in the UI and rejected by the server.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `true` |
| Environment variable | `API_TOKEN_ALLOW_NEVER_EXPIRE` |

---

## `SHARING_ENABLED`

*Enable sharing*

Master switch for the sharing feature (collaborators and public links). When false, all sharing endpoints are disabled.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `true` |
| Environment variable | `SHARING_ENABLED` |

---

## `SHARING_USER_SEARCH_ENABLED`

*Enable user search when sharing*

Allow signed-in users to search for other users by name when choosing who to share with. When false the search endpoint is disabled and a sharer must type an exact identifier.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `true` |
| Environment variable | `SHARING_USER_SEARCH_ENABLED` |

---

## `SHARING_PUBLIC_LINKS_ENABLED`

*Enable public share links*

Allow owners to create public URLs that let anyone with the link open a card without an account. When false, public-link endpoints are disabled.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `true` |
| Environment variable | `SHARING_PUBLIC_LINKS_ENABLED` |

---

## `SHARING_REQUIRE_INVITE_ACCEPT`

*Require invite acceptance*

When true, sharing a card creates a pending invite that the recipient must accept before the card appears for them. When false, sharing adds the collaborator immediately.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `false` |
| Environment variable | `SHARING_REQUIRE_INVITE_ACCEPT` |

---

## `SHARING_PUBLIC_LINK_EMAIL_ENABLED`

*Allow mailing a public link*

Allow a card owner to send an existing public share link to an arbitrary email address from inside the app. Off by default: it lets signed-in users make the server send mail to addresses of their choosing. Requires EMAIL_ENABLED and SHARING_PUBLIC_LINKS_ENABLED as well.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `false` |
| Environment variable | `SHARING_PUBLIC_LINK_EMAIL_ENABLED` |

---

## `EMAIL_ENABLED`

*Enable email sending*

Master switch for outgoing email. When false the server never sends a message and the email column of the notification settings is hidden in the UI. When true, EMAIL_FROM_ADDRESS is required (and EMAIL_SMTP_HOST for the `smtp` transport), checked at startup.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `false` |
| Environment variable | `EMAIL_ENABLED` |

---

## `EMAIL_TRANSPORT`

*Email transport*

How messages leave the server. `smtp` talks to a real mail server and is the only production choice. `console` writes the whole message to the log, `file` drops it as an `.eml` file into EMAIL_FILE_TRANSPORT_DIR (open it with any mail client), and `null` discards it. The last three exist so local development and automated tests never need a mail server.

| Property | Value |
|---|---|
| Type | Enum |
| Required | No |
| Default | `"smtp"` |
| Allowed values | `smtp` · `console` · `file` · `null` |
| Environment variable | `EMAIL_TRANSPORT` |

**Examples:**

*Example 1:*

```yaml
EMAIL_TRANSPORT: smtp
```

*Example 2:*

```yaml
EMAIL_TRANSPORT: console
```

*Example 3:*

```yaml
EMAIL_TRANSPORT: file
```

*Example 4:*

```yaml
EMAIL_TRANSPORT: 'null'
```

---

## `EMAIL_SMTP_HOST`

*SMTP host*

Hostname of the mail server to hand messages to. Required when EMAIL_ENABLED is true and EMAIL_TRANSPORT is `smtp`.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `null` |
| Environment variable | `EMAIL_SMTP_HOST` |

**Examples:**

*Example 1:*

```yaml
EMAIL_SMTP_HOST: smtp.example.com
```

*Example 2:*

```yaml
EMAIL_SMTP_HOST: localhost
```

---

## `EMAIL_SMTP_PORT`

*SMTP port*

Port of the mail server. 587 is the usual submission port (with STARTTLS), 465 the implicit-TLS one (use EMAIL_SMTP_SECURITY `ssl`), 25 plain relay on a trusted network.

| Property | Value |
|---|---|
| Type | int |
| Required | No |
| Default | `587` |
| Environment variable | `EMAIL_SMTP_PORT` |

**Examples:**

*Example 1:*

```yaml
EMAIL_SMTP_PORT: 587
```

*Example 2:*

```yaml
EMAIL_SMTP_PORT: 465
```

*Example 3:*

```yaml
EMAIL_SMTP_PORT: 25
```

---

## `EMAIL_SMTP_USER`

*SMTP username*

Username for SMTP authentication. Leave unset for a relay that needs no login.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `null` |
| Environment variable | `EMAIL_SMTP_USER` |

---

## `EMAIL_SMTP_PASSWORD`

*SMTP password*

Password for SMTP authentication. Only used together with EMAIL_SMTP_USER. Supply it through the environment rather than committing it to a config file.

| Property | Value |
|---|---|
| Type | Object |
| Required | No |
| Default | `null` |
| Environment variable | `EMAIL_SMTP_PASSWORD` |

---

## `EMAIL_SMTP_SECURITY`

*SMTP connection security*

How the connection to the mail server is encrypted. `starttls` connects in plain text and upgrades (the normal choice for port 587), `ssl` is TLS from the first byte (port 465), `none` is unencrypted and only acceptable for a mail server on localhost or a trusted private network.

| Property | Value |
|---|---|
| Type | Enum |
| Required | No |
| Default | `"starttls"` |
| Allowed values | `starttls` · `ssl` · `none` |
| Environment variable | `EMAIL_SMTP_SECURITY` |

**Examples:**

*Example 1:*

```yaml
EMAIL_SMTP_SECURITY: starttls
```

*Example 2:*

```yaml
EMAIL_SMTP_SECURITY: ssl
```

*Example 3:*

```yaml
EMAIL_SMTP_SECURITY: none
```

---

## `EMAIL_FROM_ADDRESS`

*Sender address*

The address every message is sent from. Required when EMAIL_ENABLED is true, checked at startup. Use an address the mail server is actually allowed to send as, otherwise messages get rejected or land in spam.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `null` |
| Environment variable | `EMAIL_FROM_ADDRESS` |

**Examples:**

*Example 1:*

```yaml
EMAIL_FROM_ADDRESS: checkcheck@example.com
```

*Example 2:*

```yaml
EMAIL_FROM_ADDRESS: no-reply@example.com
```

---

## `EMAIL_FROM_NAME`

*Sender display name*

The human-readable name shown next to the sender address. Falls back to APP_NAME when unset.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `null` |
| Environment variable | `EMAIL_FROM_NAME` |

**Examples:**

*Example 1:*

```yaml
EMAIL_FROM_NAME: CheckCheck
```

*Example 2:*

```yaml
EMAIL_FROM_NAME: My Checklists
```

---

## `EMAIL_REPLY_TO`

*Reply-To address*

Optional address replies should go to. Set it to a monitored mailbox when EMAIL_FROM_ADDRESS is a no-reply one; leave unset to omit the header.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `null` |
| Environment variable | `EMAIL_REPLY_TO` |

**Examples:**

```yaml
EMAIL_REPLY_TO: support@example.com
```

---

## `EMAIL_FILE_TRANSPORT_DIR`

*Directory for the file transport*

Where EMAIL_TRANSPORT `file` writes messages as `.eml` files. Created on first use and must be writable by the server process. Ignored by every other transport.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"./dev_mail"` |
| Environment variable | `EMAIL_FILE_TRANSPORT_DIR` |

---

## `EMAIL_TIMEOUT_SECONDS`

*SMTP timeout (seconds)*

How long to wait for the mail server before giving up on a message. The attempt is retried later, so a short timeout is safer than a long stall.

| Property | Value |
|---|---|
| Type | int |
| Required | No |
| Default | `20` |
| Environment variable | `EMAIL_TIMEOUT_SECONDS` |

---

## `NOTIFY_EMAIL_REQUIRE_VERIFIED`

*Only mail verified addresses*

When true, a user whose address is not marked verified receives no mail. Off by default because in a self-hosted instance addresses come from the identity provider or an administrator and are already trusted. There is no verification flow yet, so turning this on currently stops all mail.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `false` |
| Environment variable | `NOTIFY_EMAIL_REQUIRE_VERIFIED` |

---

## `NOTIFY_EMAIL_CONTENT_MODE`

*How much email messages reveal*

`full` names the card and the person who acted, which makes the message useful on its own. `minimal` only says that something happened and links back to the app. Mail leaves the instance and is stored on someone else's server, so pick `minimal` when card names are sensitive.

| Property | Value |
|---|---|
| Type | Enum |
| Required | No |
| Default | `"full"` |
| Allowed values | `full` · `minimal` |
| Environment variable | `NOTIFY_EMAIL_CONTENT_MODE` |

**Examples:**

*Example 1:*

```yaml
NOTIFY_EMAIL_CONTENT_MODE: full
```

*Example 2:*

```yaml
NOTIFY_EMAIL_CONTENT_MODE: minimal
```

---

## `NOTIFY_EMAIL_SUPPRESS_WINDOW_SECONDS`

*Delay before an immediate mail goes out (seconds)*

How long a message waits before being sent. If the user reads the notification in the app within that window, no mail is sent at all. Keeps people who are looking at the app right now out of their own inbox. Set to 0 to send without delay.

| Property | Value |
|---|---|
| Type | int |
| Required | No |
| Default | `120` |
| Environment variable | `NOTIFY_EMAIL_SUPPRESS_WINDOW_SECONDS` |

---

## `NOTIFY_PUBLIC_LINK_THROTTLE_MINUTES`

*Throttle for public-link-opened mail (minutes)*

At most one `public_link_opened` mail per card per recipient in this window. Anyone holding a public link can trigger that event, so without a throttle a reload loop would flood the owner's inbox.

| Property | Value |
|---|---|
| Type | int |
| Required | No |
| Default | `60` |
| Environment variable | `NOTIFY_PUBLIC_LINK_THROTTLE_MINUTES` |

---

## `NOTIFY_DEFAULT_MODES`

*Instance default notification modes*

The delivery mode used for a notification type and channel when the user has not chosen one. Keyed by notification type (`card_shared`, `card_invited`, `public_link_opened`), then by channel (`in_app`, `email`, `webhook`). Modes are `off`, `immediate`, `hourly` and `daily`; `in_app` and `webhook` accept only `off` and `immediate`. Users can override every entry unless it is listed in NOTIFY_DISABLED_TYPES.

| Property | Value |
|---|---|
| Type | Dictionary of (str, Dictionary of (str, str)) |
| Required | No |
| Environment variable | `NOTIFY_DEFAULT_MODES` |

**Examples:**

```yaml
NOTIFY_DEFAULT_MODES:
  card_shared:
    in_app: immediate
    email: immediate
  card_invited:
    in_app: immediate
    email: immediate
  public_link_opened:
    in_app: immediate
    email: 'off'
```

---

## `NOTIFY_DISABLED_TYPES`

*Notification types disabled instance-wide*

Notification types nobody may receive, whatever their personal settings say. The settings UI shows those entries as locked by the administrator. Empty by default.

| Property | Value |
|---|---|
| Type | List of str |
| Required | No |
| Environment variable | `NOTIFY_DISABLED_TYPES` |

**Examples:**

```yaml
NOTIFY_DISABLED_TYPES:
- public_link_opened
```

---

## `NOTIFY_DISPATCH_IN_PROCESS`

*Send queued messages from the server process*

When true the server itself drains the queue of pending messages in a background task, which is what a normal single-container deployment wants. Turn it off only if something else drains the queue, so that queued messages are not delivered twice.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `true` |
| Environment variable | `NOTIFY_DISPATCH_IN_PROCESS` |

---

## `NOTIFY_DISPATCH_TICK_SECONDS`

*Dispatcher tick (seconds)*

How often the background sender looks for due messages. It also wakes up immediately when something is queued, so this is only the fallback interval.

| Property | Value |
|---|---|
| Type | int |
| Required | No |
| Default | `30` |
| Environment variable | `NOTIFY_DISPATCH_TICK_SECONDS` |

---

## `NOTIFY_MAX_ATTEMPTS`

*Delivery attempts before giving up*

How often a message is retried after a temporary failure (with growing backoff) before it is marked failed and left for inspection. Permanent failures, such as a rejected address, are never retried.

| Property | Value |
|---|---|
| Type | int |
| Required | No |
| Default | `6` |
| Environment variable | `NOTIFY_MAX_ATTEMPTS` |

---

## `NOTIFY_OUTBOX_RETENTION_DAYS`

*Keep sent messages for (days)*

How long successfully sent rows stay in the outbox table before they are pruned. Failed rows are kept longer so an operator can still see what broke.

| Property | Value |
|---|---|
| Type | int |
| Required | No |
| Default | `30` |
| Environment variable | `NOTIFY_OUTBOX_RETENTION_DAYS` |

---

## `NOTIFY_FEED_RETENTION_DAYS`

*Keep in-app notifications for (days)*

How long read notifications stay in the in-app feed before they are pruned. The feed grows forever otherwise. Set to 0 to keep everything.

| Property | Value |
|---|---|
| Type | int |
| Required | No |
| Default | `180` |
| Environment variable | `NOTIFY_FEED_RETENTION_DAYS` |

---

## `NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR`

*Maximum emails per user per hour*

A blunt backstop against flooding a single recipient. Messages over the limit are dropped rather than queued forever.

| Property | Value |
|---|---|
| Type | int |
| Required | No |
| Default | `20` |
| Environment variable | `NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR` |

---

## `NOTIFY_WEBHOOK_ENABLED`

*Enable per-user webhooks*

Master switch for the webhook channel, which POSTs a small JSON body to a URL each user configures for themselves. Off by default: it lets signed-in users make the server issue outbound HTTP requests.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `false` |
| Environment variable | `NOTIFY_WEBHOOK_ENABLED` |

---

## `NOTIFY_WEBHOOK_ALLOW_PRIVATE_IPS`

*Allow webhooks to private addresses*

When false, webhook URLs resolving to loopback, link-local or private network ranges are refused, which is what stops a user pointing a webhook at services reachable only from the server. Turn it on only for a trusted, single-user instance on a private network.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `false` |
| Environment variable | `NOTIFY_WEBHOOK_ALLOW_PRIVATE_IPS` |

---

## `AUTH_BASIC_LOGIN_IS_ENABLED`

*Enable local login*

Allow users stored in the local database to log in with a username and password. You can disable this when authenticating exclusively through an external OIDC provider.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `true` |
| Environment variable | `AUTH_BASIC_LOGIN_IS_ENABLED` |

---

## `AUTH_BASIC_USER_DB_REGISTER_ENABLED`

*Allow self-registration*

Allow anyone to create a local account through the public registration endpoint. Off by default. Enable only when you have anti-abuse controls in place, since there is no email verification step yet.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `false` |
| Environment variable | `AUTH_BASIC_USER_DB_REGISTER_ENABLED` |

---

## `AUTH_OIDC_PROVIDERS`

*OpenID Connect providers*

List of external OpenID Connect providers users may log in with. Empty by default (local login only). One entry per provider; see the example below and the walkthrough in `docs/configuration.md`. When any provider is set, also set AUTH_OIDC_TOKEN_STORAGE_SECRET.

| Property | Value |
|---|---|
| Type | List of Object (OpenIDConnectProvider) |
| Required | No |
| Environment variable | `AUTH_OIDC_PROVIDERS` |

**Examples:**

```yaml
AUTH_OIDC_PROVIDERS:
- ENABLED: true
  PROVIDER_DISPLAY_NAME: Company SSO
  CONFIGURATION_ENDPOINT: https://sso.example.com/application/o/checkcheck/.well-known/openid-configuration
  CLIENT_ID: checkcheck
  CLIENT_SECRET: the-client-secret
  SCOPES:
  - openid
  - profile
  - email
  - offline_access
  USER_NAME_ATTRIBUTE: preferred_username
  USER_MAIL_ATTRIBUTE: email
  AUTO_CREATE_AUTHORIZED_USER: true
  ROLE_MAPPING:
    sso-admins:
    - admin
```

---

### `AUTH_OIDC_PROVIDERS[*]` - `OpenIDConnectProvider` schema

---

### `AUTH_OIDC_PROVIDERS[*].ENABLED`

*Enabled*

Whether this provider is active. Disabled providers are ignored.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `false` |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__ENABLED` |

---

### `AUTH_OIDC_PROVIDERS[*].PROVIDER_DISPLAY_NAME`

*Display name*

Human-readable name shown on the login button. Also slugified into the provider's callback path, so keep it unique across providers.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"My OpenID Connect Login"` |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__PROVIDER_DISPLAY_NAME` |

**Examples:**

*Example 1:*

```yaml
PROVIDER_DISPLAY_NAME: Company SSO
```

*Example 2:*

```yaml
PROVIDER_DISPLAY_NAME: Authentik
```

---

### `AUTH_OIDC_PROVIDERS[*].AUTO_LOGIN`

*Auto-redirect to this provider*

When true the login page immediately redirects to this provider instead of showing the local login form. Use only with a single provider.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `false` |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__AUTO_LOGIN` |

---

### `AUTH_OIDC_PROVIDERS[*].CONFIGURATION_ENDPOINT`

*Discovery endpoint*

The provider's OpenID Connect discovery URL (usually ends in `/.well-known/openid-configuration`). Required.

| Property | Value |
|---|---|
| Type | str |
| Required | **Yes** |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__CONFIGURATION_ENDPOINT` |

**Examples:**

```yaml
CONFIGURATION_ENDPOINT: https://sso.example.com/application/o/checkcheck/.well-known/openid-configuration
```

---

### `AUTH_OIDC_PROVIDERS[*].CLIENT_ID`

*Client ID*

OAuth2 client ID issued by the provider for this application. Required.

| Property | Value |
|---|---|
| Type | str |
| Required | **Yes** |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__CLIENT_ID` |

---

### `AUTH_OIDC_PROVIDERS[*].CLIENT_SECRET`

*Client secret*

OAuth2 client secret belonging to CLIENT_ID. Required. Keep it out of version control; supply it through the environment where possible.

| Property | Value |
|---|---|
| Type | Object |
| Required | **Yes** |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__CLIENT_SECRET` |

---

### `AUTH_OIDC_PROVIDERS[*].SCOPES`

*Requested scopes*

Scopes requested from the provider. `offline_access` is needed to receive a refresh token so the session can be kept alive without re-login. The provider must also be configured to grant this scope (in Authentik, add the built-in OpenID 'offline_access' scope mapping to the provider's selected scopes) - if it is not granted, no refresh token is issued and the session bounces to the login screen every access-token lifetime.

| Property | Value |
|---|---|
| Type | List of str |
| Required | No |
| Default | `["openid", "profile", "email", "offline_access"]` |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__SCOPES` |

---

### `AUTH_OIDC_PROVIDERS[*].USER_NAME_ATTRIBUTE`

*Username claim*

The token claim that holds a stable, unique username for the user.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"preferred_username"` |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__USER_NAME_ATTRIBUTE` |

---

### `AUTH_OIDC_PROVIDERS[*].USER_DISPLAY_NAME_ATTRIBUTE`

*Display-name claim*

The token claim that holds the user's human-readable display name. Defaults to `name`, the standard OpenID Connect `profile`-scope claim emitted by virtually every provider (Keycloak, Google, Okta, Auth0, Azure AD, ...). Authentik also serves the user's name in the `name` claim once the `profile` scope is requested, so the default works there unchanged. If the configured claim is absent the display name falls back to the username.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"name"` |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__USER_DISPLAY_NAME_ATTRIBUTE` |

---

### `AUTH_OIDC_PROVIDERS[*].USER_MAIL_ATTRIBUTE`

*Email claim*

The token claim that holds the user's email address.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"email"` |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__USER_MAIL_ATTRIBUTE` |

---

### `AUTH_OIDC_PROVIDERS[*].USER_GROUPS_ATTRIBUTE`

*Groups claim*

The token claim that holds the user's group memberships. Only consulted when ROLE_MAPPING or RESTRICT_USER_SEARCH_TO_OWN_GROUPS is in use. Unlike the username/email/name claims there is no standard OIDC claim for groups; `groups` is the most common convention (Authentik, Okta, Keycloak with a groups mapper) and is the default here. Some providers differ: Azure AD / Entra emits group object-IDs (not names) under `groups`, Auth0 uses a namespaced custom claim (e.g. `https://<app>/groups`), and Google emits no groups at all - set this to match your provider, and make sure the claim is actually released (often a dedicated scope or claim mapping).

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"groups"` |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__USER_GROUPS_ATTRIBUTE` |

---

### `AUTH_OIDC_PROVIDERS[*].AUTO_CREATE_AUTHORIZED_USER`

*Auto-create users on first login*

Create a local account the first time a user authenticates through this provider, instead of requiring the account to exist beforehand.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `true` |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__AUTO_CREATE_AUTHORIZED_USER` |

---

### `AUTH_OIDC_PROVIDERS[*].PREFIX_USERNAME_WITH_PROVIDER_SLUG`

*Prefix usernames with provider slug*

Prefix usernames coming from this provider with its slug to avoid collisions when several providers can produce the same username.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `false` |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__PREFIX_USERNAME_WITH_PROVIDER_SLUG` |

---

### `AUTH_OIDC_PROVIDERS[*].ROLE_MAPPING`

*Group-to-role mapping*

Map provider group names to CheckCheck roles. Members of a listed group are granted the mapped roles on login. Example: `{"sso-admins": ["admin"], "sso-usermanagers": ["usermanager"]}`.

| Property | Value |
|---|---|
| Type | Dictionary of (str, List of str) |
| Required | No |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__ROLE_MAPPING` |

**Examples:**

```yaml
ROLE_MAPPING:
  sso-admins:
  - admin
```

---

### `AUTH_OIDC_PROVIDERS[*].RESTRICT_USER_SEARCH_TO_OWN_GROUPS`

*Restrict user search to shared groups*

When true, a user authenticated through this provider can only find other users who share at least one group with them when picking share targets. Requires USER_GROUPS_ATTRIBUTE to be delivered by the provider.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `false` |
| Environment variable | `AUTH_OIDC_PROVIDERS[*]__RESTRICT_USER_SEARCH_TO_OWN_GROUPS` |

---

## `AUTH_OIDC_TOKEN_STORAGE_SECRET`

*OIDC token storage secret*

Secret used to encrypt OIDC access and refresh tokens before storing them in the database. Only used when at least one OIDC provider is configured; set a long random string in that case. Generate one with `openssl rand -hex 32`.

| Property | Value |
|---|---|
| Type | Object |
| Required | No |
| Default | `"change_me_only_relevant_when_an_oidc_provider_is_configured"` |
| Environment variable | `AUTH_OIDC_TOKEN_STORAGE_SECRET` |

---

## `NEW_USER_DEFAULT_LABELS`

*Default labels for new users*

Labels created automatically for every new account. Set to an empty list to start users with no labels.

| Property | Value |
|---|---|
| Type | List of str |
| Required | No |
| Default | `["Work", "Private", "Inspiration"]` |
| Environment variable | `NEW_USER_DEFAULT_LABELS` |

**Examples:**

*Example 1:*

```yaml
NEW_USER_DEFAULT_LABELS:
- Work
- Private
- Inspiration
```

*Example 2:*

```yaml
NEW_USER_DEFAULT_LABELS: []
```

---

## `APP_PROVISIONING_DATA_YAML_FILES`

*Extra provisioning data files*

Optional list of YAML files whose contents are loaded into the database on start. Use this to seed an instance with predefined data. Most deployments leave it empty.

| Property | Value |
|---|---|
| Type | List of str |
| Required | No |
| Environment variable | `APP_PROVISIONING_DATA_YAML_FILES` |

**Examples:**

```yaml
APP_PROVISIONING_DATA_YAML_FILES:
- /config/seed_data.yaml
```

---

## `EXPORT_CACHE_DIR`

*Export cache directory*

Directory where the results of export jobs (CSV, JSON) are written. Must be writable by the server process.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"./export_cache"` |
| Environment variable | `EXPORT_CACHE_DIR` |

---

## `SET_SESSION_COOKIE_SECURE`

*Secure session cookie*

When true the session cookie is only sent over HTTPS. Leave unset (the default) to derive it from SERVER_PUBLIC_URL: Secure on an `https` URL, not Secure on `http` (so login works over plain-HTTP localhost without a manual override, and is Secure in production). Set it explicitly only to override that.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `null` |
| Environment variable | `SET_SESSION_COOKIE_SECURE` |

---

## `CLIENT_URL`

*Extra allowed origin*

An additional browser origin allowed to call the API (CORS), on top of the server's own URL. Only needed when the frontend is served from a different origin than the backend, for example during frontend development.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `null` |
| Environment variable | `CLIENT_URL` |

**Examples:**

```yaml
CLIENT_URL: http://localhost:3000
```

---

## `DEBUG_SQL`

*Log SQL statements*

When true, the database engine prints every SQL query to the log. Very verbose; leave off unless debugging queries.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `false` |
| Environment variable | `DEBUG_SQL` |

---

## `SERVER_UVICORN_LOG_LEVEL`

*Uvicorn log level*

Log level for the uvicorn web server. Falls back to LOG_LEVEL when unset.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `null` |
| Environment variable | `SERVER_UVICORN_LOG_LEVEL` |

**Examples:**

*Example 1:*

```yaml
SERVER_UVICORN_LOG_LEVEL: info
```

*Example 2:*

```yaml
SERVER_UVICORN_LOG_LEVEL: warning
```

---

## `AUTH_JWT_ALGORITHM`

*JWT algorithm*

Algorithm used to sign JWT tokens. Only HS256 is supported at the moment.

| Property | Value |
|---|---|
| Type | Enum |
| Required | No |
| Default | `"HS256"` |
| Allowed values | `HS256` |
| Environment variable | `AUTH_JWT_ALGORITHM` |

---

## `AUTH_ACCESS_TOKEN_EXPIRES_MINUTES`

*Access token lifetime (minutes, deprecated)*

Deprecated. Use API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES instead.

| Property | Value |
|---|---|
| Type | int |
| Required | No |
| Default | `20160` |
| Environment variable | `AUTH_ACCESS_TOKEN_EXPIRES_MINUTES` |

---

## `ADMIN_ROLE_NAME`

*Admin role name*

Name of the role that grants full administrative access. Rarely needs changing.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"admin"` |
| Environment variable | `ADMIN_ROLE_NAME` |

---

## `USERMANAGER_ROLE_NAME`

*User-manager role name*

Name of the role that may manage other users without full admin rights. Rarely needs changing.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"usermanager"` |
| Environment variable | `USERMANAGER_ROLE_NAME` |

---

## `APP_PROVISIONING_DEFAULT_DATA_YAML_FILE`

*Default provisioning data file*

Baseline data (roles and similar) always loaded on start. This ships with the app and normally should not be changed. To seed your own data use APP_PROVISIONING_DATA_YAML_FILES instead.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"./CheckCheck/backend/checkcheckserver/default_data.yaml"` |
| Environment variable | `APP_PROVISIONING_DEFAULT_DATA_YAML_FILE` |

---

## `DOCKER_MODE`

*Running inside the official Docker image*

Set automatically by the Docker image. Only affects a few path defaults; do not set it by hand outside a container.

| Property | Value |
|---|---|
| Type | bool |
| Required | No |
| Default | `false` |
| Environment variable | `DOCKER_MODE` |

---

## `FRONTEND_FILES_DIR`

*Frontend files directory*

Directory of the built frontend (the generated Nuxt output containing index.html). The Docker image sets this for you.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"CheckCheck/frontend/.output/public"` |
| Environment variable | `FRONTEND_FILES_DIR` |

---

## `DB_MIGRATION_ALEMBIC_CONFIG_FILE`

*Alembic config file*

Path to the Alembic configuration used to run database migrations on start. The default resolves next to the source tree; rarely changed.

| Property | Value |
|---|---|
| Type | str |
| Required | No |
| Default | `"./alembic.ini"` |
| Environment variable | `DB_MIGRATION_ALEMBIC_CONFIG_FILE` |

---
