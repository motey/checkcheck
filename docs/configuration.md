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

> These names are new in 2.1. `SERVER_PUBLIC_URL` replaces the old
> `SERVER_PROTOCOL` + `SERVER_HOSTNAME` pair, `SERVER_BIND_HOST`/`SERVER_BIND_PORT`
> replace `SERVER_LISTENING_HOST`/`SERVER_LISTENING_PORT`, and
> `SERVER_TRUSTED_PROXIES` replaces `SERVER_FORWARDED_ALLOW_IPS`. The old names
> have been removed — update any config that still uses them.

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

Sharing is on by default. A few switches let an operator narrow it:

- `SHARING_ENABLED` turns the whole feature off.
- `SHARING_PUBLIC_LINKS_ENABLED` controls anonymous public share links.
- `SHARING_USER_SEARCH_ENABLED` controls whether users can search for each other
  by name when picking who to share with.
- `SHARING_REQUIRE_INVITE_ACCEPT` makes a shared card wait for the recipient to
  accept before it appears for them.
- `SHARING_PUBLIC_LINK_EMAIL_ENABLED` allows sending an existing public link to
  an email address from inside the app. Off by default, and it needs email
  configured (see below).

### Sending a public link by email

With `SHARING_PUBLIC_LINK_EMAIL_ENABLED` on, the owner of a card can have the
server mail one of its **existing** public links to any address. The recipient
needs no account: the link is a capability and grants exactly what it was created
at, so an `edit` link really does let somebody work on the card straight from the
message. The link is never created, enabled or upgraded by that call, and a
passphrase-protected link is announced as protected without the passphrase ever
being in the message.

This is the only place where a signed-in user decides who the server writes to,
so leave it off unless you want it, and keep an eye on two settings when you turn
it on:

- `SHARING_PUBLIC_LINK_EMAIL_MAX_PER_HOUR` (10 by default) is how many links one
  user may send per hour. It is what keeps an account from being a mail relay.
- `SHARING_INTERNAL_EMAIL_DOMAINS` (empty by default) lists the email domains of
  your own organisation. When somebody types an address on one of them, the app
  points out that the person probably has an account here and that adding them as
  a collaborator is what was meant. It never blocks the send: a colleague's
  private address, or a device they are not signed in on, is a real case. This is
  the one thing that stops the mistake people make with this kind of field, which
  is handing out an anonymous link when they meant a per-user grant.

Nothing here tells anybody whether an address has an account on this server. No
response repeats the address that was entered, and the hint is driven purely by
the domain list you declared.

## Email

Email is off by default and nothing is sent while `EMAIL_ENABLED` is false. To
switch it on, set the master switch, a sender address, and a mail server:

```yaml
EMAIL_ENABLED: true
EMAIL_FROM_ADDRESS: checkcheck@example.com
EMAIL_SMTP_HOST: smtp.example.com
EMAIL_SMTP_PORT: 587
EMAIL_SMTP_SECURITY: starttls   # starttls (587), ssl (465), none (localhost)
EMAIL_SMTP_USER: checkcheck
EMAIL_SMTP_PASSWORD: the-smtp-password
```

The settings are checked when the server starts: with `EMAIL_ENABLED` on, a
missing `EMAIL_FROM_ADDRESS`, or a missing `EMAIL_SMTP_HOST` for the `smtp`
transport, stops the boot with an explicit message. That is deliberate, a
half-configured mailer would otherwise fail once per message inside a background
task where nobody notices.

For local development `EMAIL_TRANSPORT` can avoid a mail server entirely:
`console` logs each message, `file` writes it as an `.eml` file into
`EMAIL_FILE_TRANSPORT_DIR` (open it in any mail client), and `null` discards it.
The dev scripts wire all of this up for you behind a `--mail` flag, including a
throwaway Mailpit inbox: see
[development.md](development.md#reading-the-notification-email-you-just-triggered).

The `NOTIFY_*` settings decide which notifications turn into mail, how much a
message may reveal (`NOTIFY_EMAIL_CONTENT_MODE`) and how the background sender
behaves. See the full list in [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md).

Queued messages are sent by a background task inside the server process, so
there is nothing extra to deploy. It looks for due messages every
`NOTIFY_DISPATCH_TICK_SECONDS` and immediately when something is queued, retries
a mail server that is temporarily unreachable with a growing delay, gives up
after `NOTIFY_MAX_ATTEMPTS` and keeps those failures for
inspection. `NOTIFY_DISPATCH_IN_PROCESS: false` switches the task off, for the
rare case that something else drains the queue.

To check a fresh setup, open the avatar menu, pick **Notifications** and use
**Send test email**. It mails the signed-in user's own address, at most once a
minute, and nobody else gets a copy. The same thing over the API is
`POST /api/user/me/notification-settings/test-email`.

Every user can decide, per notification type, whether it reaches them in the app,
by email, or not at all, in that same dialog. `NOTIFY_DEFAULT_MODES` sets what a
user gets before they choose anything, and `NOTIFY_DISABLED_TYPES` lists the types
nobody on this instance may receive, whatever their personal settings say. A type
in that list, and every channel whose master switch is off, shows in the dialog as
a disabled control with the reason next to it. A user who never opens the dialog is
stored nowhere and simply follows the instance defaults, which is also why adding a
notification type in a later release needs no migration. On an instance with
`EMAIL_ENABLED: false` the dialog shows the in-app column only: there is nothing
to configure about mail that is never sent.

The dialog also carries the user's time zone, which is what a daily summary is
timed against (08:00 local). It is empty until somebody sets it, and an unset zone
means UTC.

A user's choice per type is one of four modes. `off` sends nothing. `immediate`
sends as it happens, but not instantly: a message waits
`NOTIFY_EMAIL_SUPPRESS_WINDOW_SECONDS` first, and if the user reads the
notification in the app inside that window no mail is sent at all, so somebody
working in the app does not get mail about what they are looking at. `hourly` and
`daily` collect everything in one window into a single summary; a daily summary
goes out at 08:00 in the user's own time zone, and a window in which nothing
happened sends nothing. Messages that would otherwise arrive together are merged
anyway: one person sharing thirty cards produces one mail, not thirty.
`NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR` is a last backstop on top of all that.

Every message links to the card it is about. Following that link opens the card
in the app and marks that one notification read, so the bell does not keep an
item the recipient has already dealt with in their inbox. The marking happens in
the app once the card is on screen, never on the link itself, so a mail scanner
that fetches every URL in a message cannot clear anybody's notifications.

Every message carries an unsubscribe link (and the `List-Unsubscribe` headers
that let a mail client offer its own unsubscribe button). Following it switches
off email for exactly that one notification type, for that one recipient, and
nothing else; the in-app notification is untouched.

Recipients without an email address are skipped silently, which is normal on an
instance where accounts come from an identity provider that does not send an
email claim.

### Branding your email

Every message (a notification, an invitation to a shared card, the test mail)
and the unsubscribe confirmation page share one design: a branded header, the
message in a card on a tinted background, and a footer with the settings and
unsubscribe links. Two settings are enough to put your own colour and logo on
it, no code or template editing required:

```yaml
EMAIL_BRAND_COLOR: "#1d4ed8"
EMAIL_LOGO_URL: https://example.com/logo.png
```

`EMAIL_BRAND_COLOR` colours the header and the call-to-action buttons. It must
be a hex triplet such as `#1d4ed8`; a malformed value stops the server from
starting, since it is interpolated straight into the message markup. The
header text and button label switch between black and white automatically, so
a pale brand colour never produces white text on a white button.

`EMAIL_LOGO_URL` must be an absolute `http://` or `https://` address. Leaving
it unset shows a plain text wordmark instead, which is the default for two
reasons. Most mail clients block remote images until the reader explicitly
allows them, so the design has to look right without one anyway. More
importantly, a remote image is fetched by the recipient's own mail client the
moment the message is opened, which tells this instance when that happened.
That is a reasonable trade-off for your own users' notification mail, since it
is exactly the same kind of thing an app icon or a "seen" receipt already
reveals inside the app. It is a different trade-off for the public-link
invitation (see above): that message goes to somebody who never signed up
here, so weigh it once more before enabling a logo on an instance that sends
those.

For a deeper rebrand, `EMAIL_TEMPLATE_DIR` points at a directory of your own
Jinja2 templates that take priority over the bundled ones, matched by file
name. You do not need to provide all of them: anything you leave out keeps
using the bundled version, so a directory containing only your own
`base.html` (the shared header, card and footer every other template extends)
is already enough to rebrand every message consistently. Copy the bundled
templates in
`CheckCheck/backend/checkcheckserver/notify/templates/` as a starting point.

Every bundled template name is rendered once at startup, through your override
directory if one is set, against made-up data. A template that fails to
compile (a typo in the Jinja syntax) stops the server from starting, with the
template's name in the error, rather than surfacing the first time somebody's
card gets shared. A template that compiles but raises while rendering a real
message is logged and the bundled template is used for that one message
instead, so a mistake in a footer cannot stop a reminder from arriving. Neither
of those two failure modes can make a message say more than
`NOTIFY_EMAIL_CONTENT_MODE` allows: the fields a template would need for that
(a card's name, who did something, a reminder's note) are not present in the
data at all when the mode says they may not leave the instance, so a template
that tries to use them fails the same way a typo would, rather than printing
them.

To see the result of a change without sending anything, run
`python render_email_previews.py` from `CheckCheck/backend` (with the backend
venv active). It writes one `.html` file per message kind into
`CheckCheck/backend/email_preview/` (gitignored), using whatever
`EMAIL_BRAND_COLOR` / `EMAIL_LOGO_URL` / `EMAIL_TEMPLATE_DIR` your environment
currently has set, so it also doubles as a quick check of an override before
pointing a real instance at it.

### Webhooks

`NOTIFY_WEBHOOK_ENABLED` adds a third channel next to the bell and email: each
user can save a URL of their own, and every notification type they switch on for
it is POSTed there as a small JSON body (the type, the card, the actor, a link
and a ready-made sentence). It is off by default, because it lets signed-in users
make the server issue outbound HTTP requests.

That is also why the server refuses a URL that resolves into a private, loopback
or link-local range: without it, an account here would be a probe into networks
only the server can reach, including cloud metadata services. The check happens
when the request is made, not when the URL is saved, and the request then goes to
the address that was checked, so a name that changes its answer in between gains
nothing. Redirects are not followed for the same reason. On a single-user
instance on a private network, `NOTIFY_WEBHOOK_ALLOW_PRIVATE_IPS: true` turns
that guard off.

The notification settings dialog has a **Send test webhook** button, the webhook
twin of the test message, and the same limit of one a minute. A refused URL fails
in the queue with the reason in the server log; there is no delivery receipt in
the API.

Requests carry no signature, so **the URL is the credential**. A receiver that
needs to know a request genuinely came from this server should sit behind a path
or query token nobody can guess, which is why the server never writes a webhook
URL to the log. That trade is deliberate rather than a gap: the body carries
nothing the recipient does not already have in their inbox and their feed, and
`NOTIFY_EMAIL_CONTENT_MODE: minimal` strips card titles and actor names from the
webhook body as well. Tell your users to treat their URL the way they would treat
a password, and to replace it rather than reuse it if it leaks.

### Keeping the in-app feed from growing forever

`NOTIFY_FEED_RETENTION_DAYS` (180 by default) is how long a notification the user
has already read stays in the feed. Unread ones are always kept, whatever their
age: an unread notification is still somebody's inbox and its badge is the only
sign the event happened. `0` keeps everything. The tidy-up runs about once an
hour in the same background task that sends messages, together with
`NOTIFY_OUTBOX_RETENTION_DAYS` for the delivery queue.

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
