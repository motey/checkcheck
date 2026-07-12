# DOC_NOTES (archived)

> Archived 2026-07-12. This was the pre-writing trap list. The operator-facing
> facts now live in `docs/configuration.md`, `docs/deployment.md`, and
> `docs/administration.md`; the developer/designer/security notes below are kept
> here for history and overlap with project memory.

# DOC_NOTES - non-obvious facts the full documentation must not miss

Collected 2026-07-11 after the full 2.0 review, while the whole system was in
one head. Not documentation — a trap list for whoever (or whatever) writes it
later. Obvious/derivable content is omitted; everything here is easy to get
wrong without full context. Link, don't paraphrase, the existing sources:
`docs/SYNC_PROTOCOL.md` (the contract), `docs/UPGRADING.md`, `docs/ISSUES.md`,
`docs/testing/E2E_TESTING.md`, `tests/e2e/LLM_GUIDE.md`,
`docs/plans/VERSION_2.0_WORK_ITEMS.md` (per-item "decisions taken" = the why).

## Users

- Conflict toasts are informational, not errors: "also edited elsewhere" means
  the LOCAL value was kept and will sync (nothing lost). Only "…was discarded"
  toasts mean a write was dropped (delete/revocation won).
- Labels are **per-user**, even on shared cards — your label set on a shared
  card is invisible to collaborators. Archive/pin are also per-user positions,
  not card state.
- Online-only by design (don't document as missing features): sharing,
  invites, notifications, label create/rename/delete/sort.

## Operators

- Generate the config reference from `backend/checkcheckserver/config.py`
  (pydantic-settings, every field has a description) — don't hand-write it.
- SSE transport depends on the DB backend: Postgres = `pg_notify` channel
  `checkcheck_sync` (multi-worker safe); SQLite = notification table + drain
  loop (single process only). Reverse proxies must not buffer `GET /api/sync`.
- **Restoring a DB backup is not neutral:** clients' cursors run ahead →
  `full_resync` self-heals reads, but queued offline writes on those clients
  get partially dropped (users see "server was reset"). Never reset `sync_seq`
  on a live DB.
- Migrations: schema frozen at Alembic baseline `0010`; real revisions from
  2.0 on; pre-2.0 installs recreate the DB once (UPGRADING.md).
- Support kill-switch: `?localFirst=0` (persists in that browser's
  localStorage — it must be undone with `?localFirst=1`, not by removing the
  param). Deploy default: `NUXT_PUBLIC_LOCAL_FIRST`.

## Developers

Gotchas that each cost a debugging session once:

- `model/_base_model.py` aliases **pydantic's** `Field` — `index=`/`nullable=`
  silently no-op there; real columns need sqlmodel's `SQLField`.
- `updated_at` is stamped by a `before_update` **mapper event**, not column
  `onupdate` — an `sa_column_kwargs` callable leaks into `json_schema_extra`
  and breaks OpenAPI/server boot.
- `server_seq` allocation holds the counter-row lock until commit (that's what
  makes cursors safe); it serializes commit tails and can deadlock under
  Postgres → retryable 5xx **by design**, the outbox replays.
- Tombstone the parent only (checklist/item/label); never hard-delete a child
  in the same session. Generic reads auto-mask `deleted_at`.
- "Has access" = a `CheckListPosition` row exists (inner join), NOT collaborator
  status; its `granted_seq` is the access-gain signal for the delta feed.
- Hard deletes leave no seq trace: revoke and label-detach compensate by
  `touch`ing a position row so the SSE poke advances. **Any new hard-delete
  path needs the same treatment or offline clients miss it.**
- Tests run `create_all`, not migrations — model defs must be right for a
  fresh Postgres DB (naive-UTC datetimes, FK `ondelete=CASCADE` on the model).
- Dual venv: pdm `backend/.venv` runs the dev server; uv root `.venv` runs
  pytest. Sync both on dependency changes.
- Frontend layering is deliberate: framework-free cores (`utils/outbox.ts`,
  `deltaApply.ts`, `editGuard.ts`, `connectivity.ts` — plain vitest) ←
  Nuxt-bound glue (`localSnapshot.ts`, `useOutbox`, `useSync`) ← stores with
  `isLocalFirstEnabled()` forks. New offline mutations follow the pattern:
  mutate store → enqueue op (`outboxOps.ts` builder) → field-guard covers it.
- The two IndexedDB DBs have **opposite lifecycles**: snapshot
  (`checkcheck-localfirst`) is disposable (version bump = drop); outbox
  (`checkcheck-outbox`) is precious (version bump must migrate, never drop).
- Flag-on, the ONLY board read trigger is the `changes_available` poke →
  delta pull; legacy per-entity SSE payloads are frozen. Flag-off must stay
  byte-for-byte legacy until the path is removed.
- Idempotency is a public API feature: creates accept a client UUID `id`
  (replay returns the existing row), delete replays succeed, PATCH is
  field-LWW. Terminal-vs-retryable table: SYNC_PROTOCOL §8.
- `/api/changes` is authed-only — anonymous public-link viewers can't sync.

## Test runners

- Run Playwright via the **local** `@playwright/test` CLI through bun — bare
  `bunx playwright` picks a cached mismatched version and breaks collection.
- Known flakes: a few DnD/sharing/counts specs fail non-deterministically in
  parallel (disjoint sets per run) — re-run before blaming a change.
  `sharing-modal.spec.ts:93` fails **deterministically** in both flag states
  (pre-existing, pre-WI-10). Keep this list current or CI trust dies.
- Offline E2E technique: block API writes with a route predicate that leaves
  `GET /api/sync` + `GET /api/changes` live (see `offline-sync.spec.ts`).
- Click cards via `[data-testid=card-title]`; modals are declarative
  `v-model:open` — don't reintroduce imperative opens (old double-dialog bug).
- Postgres backend test run is the one that counts (SQLite is dev-only).

## Designers

- Nuxt UI toasts only support `primary`/`error`/`neutral` — no green/amber.
  That constraint shaped the sync notices; it's not an oversight.
- Keyboard-a11y attrs (`tabindex`/`role`) on draggables must go on the FormKit
  `<li>` node itself — on a child, focus-on-mousedown cancels the drag.
- `data-testid` attributes are load-bearing for E2E; renaming one is a
  breaking change.

## Security

- SSE routing fields (`target_user_ids`, `target_tokens`) travel inside the
  pg_notify payload between processes and are stripped before the client —
  a regression here is a cross-user data leak.
- The IndexedDB snapshot holds the full board unencrypted on device and (until
  2.0_REVIEW_FINDINGS Chunk A lands) is NOT cleared on logout — shared-device
  caveat.
- All sync writes reuse the permission-checked REST endpoints; the single
  access choke point is `_add_user_has_access_query`.
