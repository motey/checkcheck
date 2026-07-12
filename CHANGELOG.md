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
