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
   for example `SERVER_LISTENING_PORT=8080`.
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

## HTTPS, hostname, and the session cookie

Three settings decide how the app sees its own address. They matter as soon as
you put it behind a reverse proxy:

- `SET_SESSION_COOKIE_SECURE` (default `true`) sends the session cookie only
  over HTTPS. This is correct in production. On plain HTTP (local testing) the
  browser silently drops the cookie and login appears to do nothing, so set it
  to `false` there.
- `SERVER_PROTOCOL` and `SERVER_HOSTNAME` are used to build absolute URLs and
  the allowed CORS origin. Automatic detection cannot see the original scheme
  and host through every proxy, so set them explicitly, for example
  `SERVER_PROTOCOL=https` and `SERVER_HOSTNAME=checklists.example.com`.

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

Notes:

- `offline_access` in `SCOPES` is what gets you a refresh token, so sessions can
  be renewed without forcing the user to log in again.
- Set `AUTO_LOGIN: true` on a single provider to skip the local login form and
  redirect straight to it. Only do this when you also want to disable local
  login (`AUTH_BASIC_LOGIN_IS_ENABLED: false`).
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
