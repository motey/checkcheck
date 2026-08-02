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

## Notifications, email and webhooks

Notifications appear in the app's bell by default and go no further. Sending them
as email is opt-in per instance and takes a mail server: see
[configuration.md](configuration.md#email). Things to know when supporting users:

- **Users choose their own modes**, per notification type and per channel, under
  "Notifications" in the avatar menu. You set the instance defaults with
  `NOTIFY_DEFAULT_MODES`, and you can take a type away from everybody with
  `NOTIFY_DISABLED_TYPES`, which the settings dialog then shows as locked by the
  administrator.
- **"No mail arrived" is usually one of three things**: `EMAIL_ENABLED` is off,
  `NOTIFY_EMAIL_REQUIRE_VERIFIED` is on (there is no verification flow yet, so it
  blocks everything), or the recipient hit `NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR`.
  The user's own "Send test email" button separates a mail-server problem from a
  preference problem, and failed messages stay in the `notification_outbox` table
  with the reason in `last_error`.
- **Sending happens in a background task inside the server**
  (`NOTIFY_DISPATCH_IN_PROCESS`), not in the request, so a dead mail server slows
  nothing down. Digests are batched per user and sent in that user's timezone.
- **Webhooks are off by default** and let a signed-in user make the server issue
  outbound HTTP requests, which is why the target is checked against private and
  loopback ranges on every attempt. Requests are not signed, so the URL itself is
  the credential: a user whose receiver needs to trust the request should put an
  unguessable token in the path. See
  [configuration.md](configuration.md#webhooks).
- **Date reminders are a notification type like any other** (`reminder_due`), so
  a user's own choices in that dialog decide whether a due reminder reaches them
  in the app, by mail, or by webhook. Two things about it differ from the rest:
  it is the one type whose instance default sends mail immediately (a reminder
  the user only sees next time they open the app is not a reminder), and it
  refuses the digest modes, since a reminder held back until tomorrow morning is
  not one either. Adding `reminder_due` to `NOTIFY_DISABLED_TYPES` is the whole
  off switch: it locks the type in the dialog, stops the loop that watches for
  due rows, and makes the app refuse to store new reminders. Existing rows are
  kept and resume if you switch it back on.
- **"My reminder never arrived" is usually one of four things**: the card was
  deleted or the share was revoked (a reminder is dropped silently at fire time
  when the user can no longer open the card), the type is in
  `NOTIFY_DISABLED_TYPES`, `NOTIFY_DISPATCH_IN_PROCESS` is off with nothing else
  draining the outbox, or the user set the reminder in a timezone they have since
  changed (the reminder keeps the timezone it was created with, on purpose, so
  that moving does not shift every existing repeat). Reminders are personal: only
  the user who set one is ever notified, so "my collaborator did not get it" is
  the feature working.
- **Mailing a public link** to someone without an account is a separate switch
  (`SHARING_PUBLIC_LINK_EMAIL_ENABLED`, off by default) and is rate-limited per
  sender, since it lets an account holder make your server mail an address of
  their choosing. See
  [configuration.md](configuration.md#sending-a-public-link-by-email).

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
