# Administration

Day-to-day operation of a CheckCheck instance: the first admin, roles, how users
get in, sharing, API tokens, and the offline kill switch. For settings syntax
see [configuration.md](configuration.md); for running the server see
[deployment.md](deployment.md).

## The first administrator

On first start the server creates a single administrator account from config:

- `ADMIN_USER_NAME` (default `admin`)
- `ADMIN_USER_PW` (required, no default)
- `ADMIN_USER_EMAIL` (optional)

This is your way into a fresh instance. Log in, then change the password in the
app. The config value is only used to create or reset the admin account on
start, so leaving it set is how you recover a lost admin password: change
`ADMIN_USER_PW` and restart.

## Roles

There are two roles:

- **admin** (`ADMIN_ROLE_NAME`, default `admin`) has full administrative access.
- **usermanager** (`USERMANAGER_ROLE_NAME`, default `usermanager`) may manage
  other users without full admin rights.

Ordinary users have neither role. You rarely need to rename these.

## How users get accounts

CheckCheck delegates user management to an external identity provider. Onboarding
through OpenID Connect is the intended model; a built-in screen for managing
local users is not a goal right now, though it may come later. Today accounts
arrive one of two ways:

1. **An external OpenID Connect provider (the intended path).** With
   `AUTO_CREATE_AUTHORIZED_USER` set (the default) a local account is created the
   first time someone logs in through the provider, and `ROLE_MAPPING` can grant
   roles from provider groups. This is the way to onboard and manage a group. See
   the OIDC walkthrough in [configuration.md](configuration.md#logging-in-with-an-external-provider-oidc).
2. **Self-registration.** Set `AUTH_BASIC_USER_DB_REGISTER_ENABLED=true` to let
   people register a local account themselves. It is off by default and there is
   no email verification, so only enable it on a trusted network or behind other
   anti-abuse controls.

### Granting roles from your identity provider

With OIDC you can hand out roles automatically from provider groups using
`ROLE_MAPPING`, for example mapping an `sso-admins` group to the `admin` role.
Members of a mapped group get the roles on each login.

### Turning off local login

If you authenticate exclusively through OIDC, set
`AUTH_BASIC_LOGIN_IS_ENABLED=false` to remove the username/password form. Combine
with `AUTO_LOGIN: true` on a single provider to send users straight to it.

## Sharing

Sharing lets a user add collaborators to an individual card or publish a
read-only public link. It is on by default; the switches to narrow or disable it
are in [configuration.md](configuration.md#sharing-switches).

A few behaviours worth knowing when you support users:

- **Labels are per-user, even on a shared card.** The labels you put on a shared
  card are yours alone; collaborators do not see them. Pin and archive are also
  per-user, not properties of the card.
- **Some actions require connectivity by design.** Sharing, invitations,
  notifications, and label create/rename/delete do not work offline. This is not
  a bug; those surfaces queue nothing while disconnected.

## API tokens

Users can mint API tokens in the token manager. Two settings govern them:

- `API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES` sets the default lifetime (one week).
- `API_TOKEN_ALLOW_NEVER_EXPIRE` (default `true`) allows never-expiring tokens.
  Set it to `false` to force every token to carry an expiry.

## The offline (local-first) kill switch

CheckCheck is local-first by default: the app keeps a local copy, queues writes
while offline, and syncs on reconnect. Two controls exist if you need to change
that:

- **Per deployment:** set `NUXT_PUBLIC_LOCAL_FIRST=false` to ship the older
  online-only behaviour to everyone.
- **Per browser (support kill switch):** appending `?localFirst=0` to the URL
  disables local-first for that one browser. It **persists** in that browser's
  local storage, so it is not undone by removing the query parameter. Undo it
  explicitly with `?localFirst=1`. Use this to isolate whether a user's problem
  is in the offline layer.

## Backups and restores

Back up the database regularly. Note that restoring a backup is not neutral for
connected clients: it can drop writes they had queued offline. The details and
the safe procedure are in [deployment.md](deployment.md#backups).
