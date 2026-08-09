# Upgrading

Self-hoster and developer upgrade notes, newest first. For the full list of
changes see [`../CHANGELOG.md`](../CHANGELOG.md).

---

## Public links have names (migration `0018`)

A public link now carries a short name, so a card with several links stops being
a list of identical rows. This ships as Alembic revision `0018`, which adds one
nullable `name` column to `checklist_public_share` and is applied automatically
on server start. Nothing else is touched and there is no operator action.

- **Your existing links are named for you.** The migration numbers each card's
  links by age and writes `Link-1`, `Link-2`, and so on, so no link shows up
  without a name after the upgrade. Rename any of them by clicking the name in
  the sharing dialog.
- **A new link names itself when you leave the field empty**, one above the
  highest `Link-<n>` already on that card. Clearing a name gives you a fresh
  automatic one rather than a nameless link.
- **The name stays with the owner.** It is never shown to anonymous visitors,
  and it is not in the message sent to someone you mail a link to: it is your own
  note about who holds the link. It does appear in your "a public link was
  opened" notification, and like a card title it is withheld there when
  `NOTIFY_EMAIL_CONTENT_MODE` is `minimal`.

---

## Push notifications work without configuration (migration `0017`)

Push used to need an operator: `NOTIFY_PUSH_ENABLED` defaulted to false, and
turning it on without `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` /
`VAPID_CONTACT_EMAIL` refused to boot. It now generates its own key pair on first
boot. This ships as Alembic revision `0017`, which adds an `instance_secret`
table and is applied automatically on server start. It adds a table only, so
nothing existing is touched.

- **`NOTIFY_PUSH_ENABLED` now defaults to true.** An instance that upgrades and
  changes no settings gains the push column in the notification settings dialog
  and can subscribe devices. Nothing is actually sent to anybody until a user
  both enables push on a device and switches a notification type to push: the
  instance defaults (`NOTIFY_DEFAULT_MODES`) still have no push entry, so every
  push cell starts at "Off". Set `NOTIFY_PUSH_ENABLED: false` to keep the channel
  hidden.
- **The generated key pair lives in the database, in `instance_secret`.** Not in
  a file: a container filesystem may be read-only or ephemeral, and two replicas
  each writing their own file would sign with different keys, which is a broken
  instance that looks healthy. Your existing database backup already covers it.
- **Losing that table unsubscribes every device.** A database restored without
  the `instance_secret` rows generates a fresh pair on the next boot, and every
  existing `push_subscription` is then signed for a key its push service no
  longer accepts. Those rows fail permanently rather than being deleted (the
  server cannot tell "wrong key" from "wrong device" and refuses to unsubscribe
  people over a server-side problem), so affected users have to press "Enable
  notifications on this device" again. If you back up selectively, include
  `instance_secret` with the rest.
- **Configured keys still win.** If `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY`
  are set, they are used exactly as before and nothing is generated or written.
  Setting only one of the two is now a startup error: both halves have to come
  from the same `./gen_vapid_keys.sh` run.
- **`VAPID_CONTACT_EMAIL` is no longer required.** Without it the VAPID JWT's
  `sub` claim falls back to `ADMIN_USER_EMAIL`, and then to `SERVER_PUBLIC_URL`.
  Set it if you want a push service to be able to reach a specific address.
- **Whether a browser will subscribe is still the browser's decision.** Web Push
  needs a secure context, so the app has to be served over https (or over
  `http://localhost` for development). The settings dialog now says so, instead
  of blaming the browser.
- **The two share rows in the notification settings became one.** "A card is
  shared with me" and "I am invited to a card" are the two halves of one event,
  picked between by `SHARING_REQUIRE_INVITE_ACCEPT`, so only one of them could
  ever fire on any given instance. The merged row writes both, which means a
  user's choice already applies if you later flip that flag. No stored preference
  is changed or lost.

---

## Date reminders (migration `0015`)

Users can set a reminder on a card ("remind me about this on Friday at 09:00",
optionally repeating). This ships as Alembic revision `0015`, which adds a single
`scheduled_notification` table and is applied automatically on server start. It
adds a table only, so nothing existing is touched, and it needs no configuration
to work.

- **Reminders send mail by default**, like every other notification type:
  `NOTIFY_DEFAULT_MODES` ships `in_app: immediate, email: immediate` for all four
  of them, because a reminder the user only sees the next time they open the app
  is not a reminder. Nothing leaves the server unless `EMAIL_ENABLED` is true,
  and any user can turn any of it off for themselves. To start your users at
  in-app only instead, set the email entries in `NOTIFY_DEFAULT_MODES` to `off`.
- **A reminder cannot go into a digest.** The email channel offers `off` and
  `immediate` for this type and nothing else, whatever `NOTIFY_DEFAULT_MODES`
  says. A digest mode configured for it (or saved by a user before an upgrade)
  falls back to the default rather than being honoured.
- **To switch the feature off entirely**, add `reminder_due` to
  `NOTIFY_DISABLED_TYPES`. That is the only switch: it locks the type in the
  settings dialog, stops the scan that looks for due rows, and makes the API
  refuse to store a new reminder rather than accepting one nothing would deliver.
  Existing rows are left in place and start working again if you switch it back
  on.
- **The dispatcher now has a reason to run on an instance with no email and no
  webhooks**, since an in-app reminder is a real reminder. If you rely on
  `NOTIFY_DISPATCH_IN_PROCESS` being effectively idle, note that it now polls for
  due reminders once per `NOTIFY_DISPATCH_TICK_SECONDS` (default 30) unless the
  type is disabled.
- **Reminders are per-user side data**, not part of a card, so they are not in
  the sync feed and are never queued offline. Setting one needs a connection.
  Fired and cancelled rows are pruned after 30 days.

---

## Notification transports: email and webhooks (migrations `0013`, `0014`)

Notifications can now leave the app as email or as a per-user webhook. This ships
as Alembic revisions `0013` (a `notification_outbox` queue table) and `0014` (a
`user_notification_settings` table holding each user's per-type preferences,
timezone, webhook URL and unsubscribe secret). Both are applied automatically on
server start and add tables only, so nothing existing is touched.

- **Nothing is sent until you configure it.** `EMAIL_ENABLED`,
  `SHARING_PUBLIC_LINK_EMAIL_ENABLED` and `NOTIFY_WEBHOOK_ENABLED` all default to
  false. An instance that upgrades and changes no settings behaves exactly as
  before, in-app notifications included.
- **A half-configured mailer refuses to boot.** With `EMAIL_ENABLED: true` the
  server requires `EMAIL_FROM_ADDRESS`, and `EMAIL_SMTP_HOST` as well when
  `EMAIL_TRANSPORT` is `smtp`. That is deliberate: a silently disabled mailer is
  worse than a startup error. To try it out without a mail server, set
  `EMAIL_TRANSPORT` to `file` (writes `.eml` files) or `console`.
- **Leave `NOTIFY_EMAIL_REQUIRE_VERIFIED` false.** There is no address
  verification flow yet, so no address is ever marked verified and turning this on
  stops every outgoing message.
- **Keep `NOTIFY_DISPATCH_IN_PROCESS` true** unless something outside the server
  drains the outbox, otherwise queued messages are delivered twice. It is what a
  single-container deployment wants.
- **Read in-app notifications are now pruned** after
  `NOTIFY_FEED_RETENTION_DAYS` (default 180). Unread ones are kept regardless.
  Set it to 0 to keep everything, as before.
- **Webhooks let signed-in users trigger outbound requests from your server.**
  That is why the channel is off by default. Targets are re-resolved and checked
  on every attempt, and loopback, link-local and private ranges are refused
  unless you set `NOTIFY_WEBHOOK_ALLOW_PRIVATE_IPS`, which is only sensible on a
  trusted private instance.

Every setting is described in [`configuration.md`](configuration.md) and listed
in [`CONFIG_REFERENCE.md`](CONFIG_REFERENCE.md).

---

## Living group shares (migration `0012`)

"Share with a group" became a first-class, living share. This ships as Alembic
revision `0012` (applied automatically on server start), which adds a
`checklist_group_share` table and a nullable `checklist_collaborator.via_group`
column.

- **The migration is non-destructive.** Members previously added via the old
  one-shot group expansion keep their access unchanged: their collaborator rows
  get `via_group = NULL`, which the app treats as an *explicit* individual share.
- **They are not retroactively "living."** Those old expansions were not recorded
  as group shares, so they do not appear in the ShareModal's group list and their
  members are not auto-reconciled. To make a group living again, just re-share it
  from the ShareModal — that records the group and takes over membership.
- **Living membership reconciles at OIDC login.** A user gains access to cards
  shared with a group when they next sign in as a member of it (and loses
  group-derived access when they sign in no longer in it). This is the only point
  the app re-reads a user's OIDC groups, so changes propagate on next login — not
  instantly mid-session. Local (non-OIDC) users have no groups.

---

## To 2.0.0 (offline / local-first)

### Database

There are **no production instances of CheckCheck yet**, so the pre-2.0 Alembic
history (revisions `0001`–`0009`) was collapsed into a single squashed no-op
baseline, `0010`. Because of that:

- **Recreate any pre-2.0 development database.** The schema is built by
  `SQLModel.metadata.create_all` on a fresh database (which then stamps the
  Alembic head). `create_all` does **not** alter existing tables, so a database
  carried over from a pre-2.0 checkout will be missing the 2.0 sync columns.
  Drop and recreate it.
- A dev database stamped at a pre-squash revision (`0001`–`0009`) must also be
  recreated — those revision ids no longer exist.
- **From 2.0 on, schema changes ship as real, autogenerated Alembic revisions**
  on top of `0010`. The pre-production "edit the models and recreate the DB"
  workflow is over; run the migrations to upgrade.

Postgres is the supported production database. SQLite is for local development
only.

### New sync surface

2.0 adds the local-first delta-sync API. Nothing to configure server-side, but
if you proxy or firewall the API, be aware of:

- **`GET /api/changes`** — the delta feed a client pulls with its cursor.
- **`GET /api/sync`** — the existing Server-Sent Events stream, now a *poke*
  (it signals "changes available"; it is no longer a data channel). Make sure
  your reverse proxy does not buffer or time out this long-lived SSE connection.

The full client contract is [`SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md).

### Local-first default & escape hatch

The frontend is **local-first (offline-capable) by default** as of 2.0. To run
the pre-2.0 online-only behaviour instead, set at build/deploy time:

```
NUXT_PUBLIC_LOCAL_FIRST=false
```

For one-off debugging you can also override in the browser with a `?localFirst=0`
query param (persisted to localStorage).

The app is also installable as a PWA; the service worker caches the app shell but
**never** caches `/api`, so data always comes from the server or the local store.
