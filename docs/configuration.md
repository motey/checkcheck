# Configuration

This is the readable introduction to configuring a CheckCheck instance: how
config is loaded, the few things you must set, and the common scenarios. For the
exhaustive list of every setting see the generated
[CONFIG_REFERENCE.md](CONFIG_REFERENCE.md); for a fillable template see
[config.example.yml](../config.example.yml).

Configuration is defined in one place, the pydantic-settings model in
[`CheckCheck/backend/checkcheckserver/config.py`](../CheckCheck/backend/checkcheckserver/config.py).
The reference and the example file are generated from it, so they never drift.

## Where settings come from

Every setting can be provided three ways. Highest priority wins:

1. **Environment variables** (and a `.env` file). Names match the field names,
   for example `SERVER_BIND_PORT=8080`.
2. **A YAML config file**, `config.yml`. Point at it with `CHECKCHECK_CONFIG_FILE`
   (the Docker image defaults this to `/config/config.yml`). A missing file is
   simply ignored.
3. **The built-in default** shown for each field in the reference.

Because environment variables win over the file, you can keep the non-secret
shape in `config.yml` and inject secrets from your orchestrator's secret store
as env vars, or skip the file entirely and set everything from the environment.

> Never commit a real config. `config.yml` (and `config.*.yml`) is gitignored;
> only `config.example.yml` is tracked.

### Nested settings in environment variables

Nested settings join with a double underscore `__`, and list entries use their
index. For example, the client secret of the first OIDC provider is
`AUTH_OIDC_PROVIDERS__0__CLIENT_SECRET`. Lists of objects get awkward fast this
way, so if you use OIDC, prefer the YAML file (see below).

## The three things you must set

A fresh instance has three required settings with no default. Without them the
server refuses to start:

| Setting | What it is |
|---|---|
| `SERVER_SESSION_SECRET` | Signs the browser session cookie. Long random string, minimum 64 characters. |
| `AUTH_JWT_SECRET` | Signs API access tokens. Long random string, minimum 64 characters, different from the session secret. |
| `ADMIN_USER_PW` | Password for the built-in `admin` account created on first start. |

Generate the two secrets with:

```bash
openssl rand -hex 32   # 64 hex characters, run once per secret
```

Keep the secrets stable. Changing `SERVER_SESSION_SECRET` logs everyone out;
changing `AUTH_JWT_SECRET` invalidates existing API tokens.

## Public URL, binding, and the session cookie

The web-server settings split cleanly into two concerns:

- **Where the process binds** (internal): `SERVER_BIND_HOST` (default
  `localhost`; `0.0.0.0` in the Docker image) and `SERVER_BIND_PORT` (default
  `8181`). This is the address your reverse proxy connects to.
- **Where users reach the app** (external): `SERVER_PUBLIC_URL`, the full base
  URL including scheme, e.g. `https://checklists.example.com`. It is the single
  source of truth for every absolute URL the app builds (OIDC redirect URIs, the
  allowed CORS origin) and for whether the session cookie is Secure. Behind a
  reverse proxy the app cannot reliably infer its own external scheme/host/port,
  so **set this explicitly in production.** Include a port only when the app is
  reached on a non-standard one (`https://host:8443`); never include a path. When
  unset it is derived from the bind host/port — fine for local development only.

`SET_SESSION_COOKIE_SECURE` (the session cookie's `Secure` flag) is **derived
from `SERVER_PUBLIC_URL` by default**: Secure on an `https` URL, not Secure on
`http`. That means it is correct in production and login still works over
plain-HTTP localhost without any override — set it explicitly only to force a
value.

> `SERVER_PUBLIC_URL` replaces the older `SERVER_PROTOCOL` + `SERVER_HOSTNAME`
> pair, and `SERVER_BIND_HOST`/`SERVER_BIND_PORT` replace
> `SERVER_LISTENING_HOST`/`SERVER_LISTENING_PORT` (as `SERVER_TRUSTED_PROXIES`
> replaces `SERVER_FORWARDED_ALLOW_IPS`). The old names still work — the legacy
> port/host names are accepted as aliases, and `SERVER_PROTOCOL`/`SERVER_HOSTNAME`
> synthesize the public URL — but they are deprecated and log a warning. Prefer
> the new names.

## Database

`SQL_DATABASE_URL` selects the backend. Point it at PostgreSQL for any real
deployment:

```yaml
SQL_DATABASE_URL: postgresql+asyncpg://checkcheck:secret@db:5432/checkcheck
```

The image can also boot on a bundled SQLite file with no setup, but that is for
local development only and is on track to be removed; do not run a real instance
on it. See [deployment.md](deployment.md).

## Sharing switches

Sharing is on by default. Four switches let an operator narrow it:

- `SHARING_ENABLED` turns the whole feature off.
- `SHARING_PUBLIC_LINKS_ENABLED` controls anonymous public share links.
- `SHARING_USER_SEARCH_ENABLED` controls whether users can search for each other
  by name when picking who to share with.
- `SHARING_REQUIRE_INVITE_ACCEPT` makes a shared card wait for the recipient to
  accept before it appears for them.

## Logging in with an external provider (OIDC)

CheckCheck can delegate login to any OpenID Connect provider (Authentik,
Keycloak, and so on). Because a provider is a list of objects, configure it in
`config.yml` rather than through environment variables. A worked single-provider
example:

```yaml
# Encrypts the stored OIDC tokens. Required once any provider is configured.
AUTH_OIDC_TOKEN_STORAGE_SECRET: "generate-with-openssl-rand-hex-32"

AUTH_OIDC_PROVIDERS:
  - ENABLED: true
    PROVIDER_DISPLAY_NAME: "Company SSO"     # shown on the login button; keep it unique
    CONFIGURATION_ENDPOINT: "https://sso.example.com/application/o/checkcheck/.well-known/openid-configuration"
    CLIENT_ID: "checkcheck"
    CLIENT_SECRET: "the-client-secret"
    SCOPES: ["openid", "profile", "email", "offline_access"]
    USER_NAME_ATTRIBUTE: "preferred_username"
    USER_MAIL_ATTRIBUTE: "email"
    AUTO_CREATE_AUTHORIZED_USER: true        # create the local account on first login
    ROLE_MAPPING:                            # optional: grant roles from provider groups
      sso-admins: ["admin"]
      sso-usermanagers: ["usermanager"]
```

### Redirect URI to register with the provider

Register this callback URL as an allowed redirect URI in the provider (in
Authentik it is the application's *Redirect URIs/Origins*):

```
<SERVER_PUBLIC_URL>/api/auth/oidc/callback/<provider-slug>
```

- `<provider-slug>` is `PROVIDER_DISPLAY_NAME` lowercased with spaces and other
  non-alphanumeric characters replaced by hyphens. `"Company SSO"` →
  `company-sso`, so with `SERVER_PUBLIC_URL=https://checkcheck.example.com` the
  full URI is
  `https://checkcheck.example.com/api/auth/oidc/callback/company-sso`.
- The app builds this redirect URI from `SERVER_PUBLIC_URL` directly (not from
  the request's forwarded headers), so it is stable and unspoofable — but that
  also means `SERVER_PUBLIC_URL` **must** match the URL registered with the
  provider, scheme included. A mismatch (registering `https` while
  `SERVER_PUBLIC_URL` is `http://…`) is rejected by the provider as a
  redirect-URI mismatch.

Notes:

- `offline_access` in `SCOPES` is what gets you a refresh token, so sessions can
  be renewed without forcing the user to log in again. **Requesting the scope is
  not enough — the provider must also be configured to grant it**, otherwise it
  silently issues no refresh token. In Authentik: open the application's
  *OAuth2/OpenID Provider* and add the built-in *"authentik default OAuth Mapping:
  OpenID 'offline_access'"* scope mapping to its **Selected Scopes** (Keycloak and
  most others grant `offline_access` out of the box). Symptom of a missing
  refresh token: the app works, then bounces to the login screen roughly every
  access-token lifetime (Authentik's default is 5 minutes) and again whenever a
  backgrounded tab is reopened — because with no refresh token the session cannot
  survive the access token expiring.
- Set `AUTO_LOGIN: true` on a single provider to skip the local login form and
  redirect straight to it. Only do this when you also want to disable local
  login (`AUTH_BASIC_LOGIN_IS_ENABLED: false`). After an explicit logout the app
  lands you back on the login form (rather than looping straight back into the
  provider) so you can switch accounts.
- `ROLE_MAPPING` and `RESTRICT_USER_SEARCH_TO_OWN_GROUPS` rely on the provider
  delivering the groups claim named by `USER_GROUPS_ATTRIBUTE`.

See the per-field details under `AUTH_OIDC_PROVIDERS` in
[CONFIG_REFERENCE.md](CONFIG_REFERENCE.md).

## Regenerating the reference

`CONFIG_REFERENCE.md` and `config.example.yml` are generated. After changing a
field in `config.py`, regenerate them so they stay in sync:

```bash
./gen_config_docs.sh          # rewrite both files
./gen_config_docs.sh --check  # verify they match the model (exit 1 on drift)
```

psyplus is a docs-only dependency (the `docs` group in the backend
`pyproject.toml`); it is not part of the runtime image.
