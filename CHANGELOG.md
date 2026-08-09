# Changelog

All notable changes to CheckCheck are recorded here. The format is loosely based
on [Keep a Changelog](https://keepachangelog.com/); dates are ISO-8601.

## [2.0.0] — unreleased

The **offline / local-first** release. CheckCheck now works while disconnected:
edits are applied locally, queued, and synced back when the connection returns,
and the app is installable as a PWA.

### Added

- **Local-first sync.** Each device keeps a local snapshot (IndexedDB), a
  pending-write **outbox**, and a single integer **cursor** (`server_seq`).
  Writes apply optimistically offline and replay on reconnect. This is now the
  **default** — see *Changed* below for the escape hatch.
- **Delta feed** — `GET /api/changes` returns everything that changed since a
  cursor; the existing SSE stream (`GET /api/sync`) is now a lightweight *poke*
  ("changes available, pull now") rather than a data channel. The full contract
  is documented in [`docs/SYNC_PROTOCOL.md`](docs/SYNC_PROTOCOL.md).
- **Conflict handling** — per-field Last-Writer-Wins by server-arrival order,
  with user-visible toasts when a local edit is superseded or a share is revoked
  while offline, plus a per-card "pending changes" indicator and a global sync
  status indicator (online / syncing / pending / last-synced) with a
  **Sync now** action.
- **PWA** — installable app shell via a service worker (the API is never
  cached), and an offline auth grace period so a disconnected reload does not
  bounce you to `/login`.
- **Soft delete (tombstones)** for checklists, items, and labels so deletions
  propagate correctly through the delta feed.
- **Bulk item actions** in a card's ⋮ menu — **Untick all items** and **Delete
  ticked items** — each a single offline-safe operation (one dedicated endpoint,
  one outbox op) rather than a per-item fan-out, so they work on the whole card
  even when only a preview is loaded and reach collaborators through the normal
  delta feed. Deleting ticked items asks for confirmation.
- **Markdown card notes.** The card description (the "notes" field) now renders
  as Markdown on the board preview, in the open card when you are not editing, on
  the public share page, and for view-only collaborators. Inside an open card the
  notes swap to a plain text editor on focus so you edit the raw source, and back
  to the rendered view on blur. A small "Markdown supported" hint opens a
  formatting cheat-sheet. Supported: bold, italic, strikethrough, inline and
  fenced code, links, lists, headings, blockquotes, and rules. All output is
  sanitized (images are intentionally not supported in this version). Existing
  plain-text notes render unchanged and no data migration is needed.
- **Markdown in item text (slim).** Checklist items now render an inline-only
  subset (bold, italic, strikethrough, inline code) so a single item label can be
  emphasized. Block syntax (headings, lists, quotes) and links stay plain, since
  items are single-line labels. Rendering is optimized for the board's hot path:
  plain items ("Buy milk") take a zero-cost fast path and formatted items are
  memoized, so search and reordering stay smooth.
- **Items edit on focus.** Inside an open card an item shows its rendered
  Markdown until you click or tab into it, at which point it swaps to the raw
  text so you edit the source, and swaps back on blur. Only the row you are
  editing is a text field. Adding items, Enter to add the next one, backspace to
  merge, and the uncheck suggestions all behave as before. View-only
  collaborators never get an edit surface at all.
- **Links in item text.** A URL typed into an item is detected and followed by a
  small boxed-arrow icon that opens it in a new tab. The URL text itself stays
  plain, so tapping the item still opens the card as before and only the icon
  opens the link.
- **Living group shares.** Sharing a card with an OIDC group is now a first-class,
  persistent share: the ShareModal lists the groups a card is shared with (each at
  its own permission), you can share with several groups and remove one as a unit,
  and membership is *living* — people who join a shared group gain access on their
  next sign-in, and leavers lose it. Revoking a group removes the access it
  granted while leaving individual shares intact; an explicit individual share
  always takes precedence over a group's level. Group members are represented by
  the group itself, not listed one-by-one under "Share with people", so the
  people list stays uncluttered. (Migration `0012`.)
- **Email notifications.** Off by default. Once an operator sets `EMAIL_ENABLED`
  and points the server at a mail server, CheckCheck can also email you about the
  things the bell already shows: a card shared with you, an invitation, and a
  public link being opened. Each user picks a delivery mode per notification type
  *and* per channel under "Notifications" in the avatar menu: `off`, `immediate`,
  an `hourly` digest, or a `daily` digest sent in the user's own timezone (the
  in-app channel is immediate or off). Every message carries an unsubscribe link
  that turns that type off without signing in, and the settings dialog has a
  "Send test email" button. Clicking a card link in a message opens the card and
  marks exactly that notification read. Mail is queued and sent by a background
  task inside the server process, so a slow or unreachable mail server never
  blocks a request; temporary failures retry with backoff and are then left in
  the outbox for an operator to inspect. Administrators can set the instance
  defaults (`NOTIFY_DEFAULT_MODES`), disable a type for everyone
  (`NOTIFY_DISABLED_TYPES`), keep card contents out of email entirely
  (`NOTIFY_EMAIL_CONTENT_MODE: minimal`), and cap volume per recipient
  (`NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR`). For local testing, `EMAIL_TRANSPORT`
  can write `.eml` files to a directory or print to the console instead of
  sending. See [`docs/configuration.md`](docs/configuration.md).
  (Migrations `0013`, `0014`.)
- **Send a public link by email** (`SHARING_PUBLIC_LINK_EMAIL_ENABLED`, off by
  default). The owner of a card can mail an existing public link to someone who
  has no account, with an optional personal message. This is deliberately *not*
  a second way to add a collaborator: it sends an anonymous capability link, so
  anyone holding it gets in without signing in and revoking the link cuts off
  everyone. The UI is built around that distinction. The field lives inside the
  public-link block and only appears once a link exists, it is labelled by
  audience ("People outside CheckCheck") rather than by mechanism, it spells out
  the consequence difference against adding a collaborator, and it confirms the
  granted level in plain words before sending. Operators can list their own
  domains in `SHARING_INTERNAL_EMAIL_DOMAINS` to raise a soft "that address looks
  internal, add them as a collaborator instead" hint, which offers the
  collaborator box as its primary action but never blocks the send. A link's
  passphrase is never included in the mail. Sends are rate-limited per account
  (`SHARING_PUBLIC_LINK_EMAIL_MAX_PER_HOUR`) and the recipient address is never
  echoed back in an error, so the endpoint is neither an open relay nor an
  address oracle.
- **Named public links.** Every public link carries a short name, given when it
  is created or changed later by clicking it in the list, so several links on one
  card can be told apart. Leaving the field empty picks the next automatic name
  (`Link-1`, `Link-2`, and so on). The name is the owner's own label: it is shown
  in the link list, in the picker for mailing a link, and in the "your public link
  was opened" notification, and it never reaches an anonymous visitor or the
  person a link is mailed to. (Migration `0018`.)
- **A public link now shows the whole card**
  ([#11](https://github.com/motey/checkcheck/issues/11)). `/p/<token>` used to be a
  much smaller second implementation of the card, so features added to the card
  never reached it. It now renders from the same components the open card does, and
  gains everything that had drifted: the **"separate checked items" layout** with
  its collapsible checked section (the reported symptom: a visitor could neither
  see checked items grouped nor untick them), **Markdown-rendered item text**,
  the card's **colour theme**, and, on an `edit` link, **drag-reordering items**
  and editing the card's **title and notes**. Whether the checked section is
  collapsed is stored on the card, not per visitor, so a `check`-or-better link
  persists the toggle for everyone while a `view` link keeps it to its own browser
  session. The public surface stays online only: it has no outbox and is not in
  the delta feed. No migration.
- **Per-user webhooks** (`NOTIFY_WEBHOOK_ENABLED`, off by default). A third
  notification channel that POSTs a small JSON body to a URL each user saves in
  the notification settings, with a "Send test webhook" action. Because this lets
  signed-in users make the server issue outbound requests, target hosts are
  resolved and checked per attempt and anything on a loopback, link-local, or
  private range is refused unless `NOTIFY_WEBHOOK_ALLOW_PRIVATE_IPS` is set.
  Deliveries use the same retry and backoff policy as email.
- **Date reminders.** A card's ⋮ menu has "Set a reminder": pick a date and a
  time, optionally repeat it every day, week or month, and add a note to
  yourself. When it comes due you are notified through the channels you already
  chose for notifications, so a reminder can reach you by mail or webhook while
  the app is closed (this is the one notification type that ships with
  `immediate` email on by default, since a reminder you only see next time you
  open the app is not a reminder). The open card lists the reminders you have set
  on it, each removable in one click. Reminders are **personal**: they belong to
  whoever set them, only that person is notified, and collaborators on a shared
  card neither see nor are disturbed by each other's. A repeating reminder keeps
  its wall-clock time in the timezone from your notification settings, so it does
  not drift across a daylight-saving change, and a reminder on a card you can no
  longer open (deleted, or the share was revoked) is silently dropped when it
  would have fired rather than notifying you about something you cannot reach.
  Setting one needs a connection: unlike card edits, reminders are never queued
  offline, because a reminder whose time passes while it sits in a queue is worse
  than no reminder. Administrators can switch the whole feature off with
  `NOTIFY_DISABLED_TYPES: ["reminder_due"]`. (Migration `0015`.)

### Changed

- **Local-first is on by default.** Self-hosters who want the pre-2.0 online-only
  behaviour can opt out per-deploy with `NUXT_PUBLIC_LOCAL_FIRST=false`. A
  `?localFirst=0` query param / localStorage override also works for one-off
  debugging.
- **Online-only surfaces** (sharing, invitations, notifications) now clearly
  require connectivity and queue nothing while offline.
- **The in-app notification feed is pruned.** Notifications that have been read
  are removed after `NOTIFY_FEED_RETENTION_DAYS` (default 180; set it to 0 to
  keep everything). Unread notifications are never pruned.

### Upgrade notes

See [`docs/UPGRADING.md`](docs/UPGRADING.md). In short: there are **no production
instances yet**, so 2.0 ships a squashed migration baseline — **recreate any
pre-2.0 development database** (the schema is built with `create_all`, which does
not alter existing tables). From 2.0 on, schema changes ship as real Alembic
revisions.

Email, the public-link invitation and webhooks are all **off by default**, so an
existing deployment behaves exactly as before until an operator turns them on.
Two things to know before enabling mail:

- `NOTIFY_EMAIL_REQUIRE_VERIFIED` must stay **false**. There is no address
  verification flow yet, so no address is ever marked verified and turning the
  setting on silently stops every outgoing message.
- `NOTIFY_DISPATCH_IN_PROCESS` (default true) makes the server itself drain the
  message queue, which is what a normal single-container deployment wants. Turn
  it off only if something else drains the queue, otherwise messages go out
  twice.
