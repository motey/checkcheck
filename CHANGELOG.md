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

### Changed

- **Local-first is on by default.** Self-hosters who want the pre-2.0 online-only
  behaviour can opt out per-deploy with `NUXT_PUBLIC_LOCAL_FIRST=false`. A
  `?localFirst=0` query param / localStorage override also works for one-off
  debugging.
- **Online-only surfaces** (sharing, invitations, notifications) now clearly
  require connectivity and queue nothing while offline.

### Upgrade notes

See [`docs/UPGRADING.md`](docs/UPGRADING.md). In short: there are **no production
instances yet**, so 2.0 ships a squashed migration baseline — **recreate any
pre-2.0 development database** (the schema is built with `create_all`, which does
not alter existing tables). From 2.0 on, schema changes ship as real Alembic
revisions.
