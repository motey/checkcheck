# CheckCheck 2.0 — Work Items

**Status:** Active
**Date:** 2026-07-06
**Parent plan:** [VERSION_2.0_PLAN.md](VERSION_2.0_PLAN.md) (see its *Amendments* section for decisions)

---

## Decisions this breakdown is built on (2026-07-06)

1. **Sync engine: DIY delta-sync** (no PowerSync, no ElectricSQL, no client SQLite).
   Pinia stores stay the in-memory source of truth; store state is snapshotted to
   **IndexedDB** (identical in web / Tauri / Capacitor webviews); writes go through a
   **persisted outbox** replayed against the existing permission-checked REST
   endpoints; reads catch up via one new **`GET /api/changes?since=<cursor>`** delta
   endpoint reusing the existing access query. The existing SSE stream becomes a
   "changes available" poke.
2. **2.0 scope: offline PWA only.** Tauri desktop = 2.1, Capacitor mobile = 2.2.
3. **Concurrency posture: shared lists edited together is a core scenario.**
   Conflict rules stay per-field LWW (no text CRDT in 2.0), but conflict *surfacing*
   and revocation handling get a dedicated work item (WI-11) instead of being
   an afterthought.
4. **LWW clock: server-arrival order.** The server stamps `updated_at` (naive UTC,
   per model convention) on every write; the last upload to arrive wins. Client
   clocks are never trusted for ordering.
5. **Cursor is per-device and client-owned.** Clients store their own sync cursor;
   the server keeps no per-client state.
6. **LISTEN/NOTIFY is already done** (archived `SYNC_PLAN.md`); no Phase 0 spike needed.
7. **Do not write Alembic migrations for 2.0 work.** There are no production
   instances yet. Schema changes are made in the SQLModel models only; fresh
   databases get their schema via `create_all` at startup, and existing dev
   databases are simply **recreated** after model changes (`create_all` does
   not alter existing tables). The Alembic infra stays in place — the old
   history was squashed into a single no-op baseline
   (`migrations/versions/0010_squashed_baseline.py`, keeping revision id
   `0010` so stamped dev DBs still resolve). Real migrations resume with the
   first production release (see WI-15).

---

## How to read this

- One work item ≈ **one focused working session** (a Claude Code session; roughly a
  half-day including tests). L items may spill into a second session — split at the
  marked seam if so.
- Every item leaves the app **shippable online-first**. Frontend local-first behavior
  lands behind a `localFirst` feature flag until WI-15 flips it on.
- Backend items must pass both test harnesses (`run_backend_tests_with_sqlite.sh` /
  `_with_postgres.sh`); remember model definitions must be correct for fresh-Postgres
  `create_all` (naive UTC datetimes, FK `ondelete=CASCADE`).

Sizes: **S** (comfortable session), **M** (full session), **L** (full session, may split).

Dependency order: WI-1 → WI-2 → WI-3 → WI-4 → WI-5 unlock the backend; WI-6/WI-7 can
start in parallel once WI-3 exists; WI-8 → WI-9 → WI-10 → WI-11 → WI-12 build on both;
WI-13 → WI-14 → WI-15 close out the PWA.

---

## Phase 1 — Backend sync foundations

### WI-1 — Server-set `updated_at` on every syncable row (M)

**Goal:** A reliable per-row version signal, set only by the server.

- Fix the commented-out `updated_at` in `model/_base_model.py:TimestampedModel`
  (the old `text("current_timestamp(0)")` attempt broke pydantic). Use a
  Python-side callable instead: `sa_column_kwargs={"onupdate": <naive-utc-now
  callable>}` plus explicit stamping in the CRUD base so bulk/raw updates
  don't slip through.
- Confirm every syncable model inherits `TimestampedModel` (checklist,
  checklist_item, checklist_item_state, checklist_item_position,
  checklist_position, label, checklist_label, checklist_collaborator,
  checklist_public_share — verify each) and expose `updated_at` in read schemas.
- Model change only — no Alembic revision (decision 7); recreate dev DBs. A
  default of `updated_at = created_at` on row creation makes backfill moot.
- Tests: a write through every CRUD path bumps `updated_at`; both harnesses green.

**Done when:** no code path can modify a syncable row without bumping `updated_at`.

### WI-2 — Tombstones / soft delete (M)

**Goal:** Deletes propagate to offline clients instead of silently vanishing —
and stale edits can't resurrect deleted rows.

- Add `deleted_at` to checklist, checklist_item, label (and decide for
  checklist_label / checklist_collaborator — a tombstoned collaborator row is
  the cheapest way for the delta feed to compute "you lost access"; decide
  in-session and document).
- Cascade rule: tombstone the **parent row only**; children (state/position
  rows) stay untouched and are masked by the parent tombstone.
- Convert DELETE endpoints to set `deleted_at`; every read query filters
  `deleted_at IS NULL`; writes to a tombstoned row return a defined response
  (recommend 410) so the outbox can treat it as terminal.
- Garbage collection of old tombstones: explicitly **deferred** (note in docs).
- Tests: delete-then-stale-edit does not resurrect; existing suites green.

**Done when:** all deletes are tombstones and nothing resurrects.

**Decisions taken in-session (2026-07-06, implemented):**

- **Tombstoned tables:** `checklist`, `checklist_item`, `label` only. A
  `SoftDeleteMixin` (in `model/_base_model.py`) adds a nullable `deleted_at`;
  the generic `CRUDBase` masks tombstoned rows in every read path
  (`_get`/`get`/`list`/`find`) and exposes `soft_delete()` + an `include_deleted`
  escape hatch for the tombstone-aware guards. Custom per-entity queries filter
  at their choke points (`CheckListCRUD._add_user_has_access_query`, the item
  list/count/get/grid queries, the label queries, and the label-chip join).
- **`checklist_collaborator`: NOT tombstoned in WI-2** (deferred to WI-4). Revoke
  / leave / whole-card delete keep hard-deleting collaborator + per-user position
  rows, so access is revoked immediately and no orphaned pending-invite can be
  read. WI-4's `removed_checklist_ids` is computed by diffing the access query
  (the card simply drops out of the user's access set) — a collaborator tombstone
  is one possible optimisation there, but it drags in re-share resurrection
  semantics that belong with the delta-feed design, not here.
- **`checklist_label`: NOT tombstoned** — it is a pure per-user association;
  remove stays a hard delete. There is no resurrection risk, and the delta feed
  re-derives a card's label set from live rows (WI-4). When the *label itself* is
  tombstoned, its lingering link rows are masked at read time by a
  `Label.deleted_at IS NULL` join filter.
- **Cascade:** deleting a checklist tombstones the checklist row only; its
  content children (items / states / item-positions) are left untouched and
  masked by the parent tombstone (they are no longer DB-cascaded, since the
  parent is no longer hard-deleted).
- **Write to a tombstoned row → `410 Gone`** (terminal for the outbox), distinct
  from `404` (never existed). Enforced at the shared guards: the checklist-access
  dependency, the authed + public `verify_item_belongs_to_checklist`, and the
  item / label update paths. Re-issuing a *delete* is idempotent success (safe
  outbox replay), not a 410.

### WI-3 — Client-generated IDs + idempotent writes (M)

**Goal:** Outbox replays (network retries, reconnect double-sends) are always safe.

- Create endpoints accept an optional client-supplied UUID; retrying the same
  create returns the existing row instead of erroring or duplicating.
- PATCH endpoints replay-safe (same op applied twice = same result).
- Define semantics for ops against rows the client can no longer see
  (tombstoned → 410, access revoked → 403); these become the outbox's
  "terminal" error set in WI-7.
- Tests: every mutating endpoint gets a replay-twice test.

**Done when:** replaying any mutation twice yields identical state, and terminal
errors are distinguishable from retryable ones.

**Decisions taken in-session (2026-07-06, implemented):**

- **Client-supplied id is optional, per-create.** Added a nullable `id` to the
  three API create schemas (`CheckListApiCreate`, `CheckListItemCreateAPI`,
  `LabelCreate`). Omitting it keeps the legacy server-assigns-id behaviour; the
  table models already had `default_factory=uuid4`, so the routes only forward a
  client id when it is actually set (a null `id` in the payload is dropped so the
  factory still runs). No new columns — model change only, no Alembic (decision 7).
- **Idempotent create = pre-check by id, not DB upsert.** Each create route, when
  given an id, does a tombstone-aware `get(id, include_deleted=True)` before
  inserting and branches: **live + belongs to caller → return the existing row**
  (200, no duplicate, no sync poke — a replay); **tombstoned → 410 Gone** (a
  create replay must not resurrect, terminal); **owned by another user / another
  card → 409 Conflict** (a genuine UUID collision, terminal). "Belongs to caller"
  is owner-scoped for checklist/label and card-scoped (path `checklist_id`) for
  items. Chose this over a blind `INSERT … ON CONFLICT` upsert because the
  ownership/tombstone branches need distinct terminal status codes and an upsert
  would also silently overwrite a live row's fields on replay.
- **The public anonymous item-create surface gets the same treatment** (it shares
  `CheckListItemCreateAPI`), so a retried anonymous create can't duplicate either;
  it was also the one route that would have crashed on the new `id` field (the
  explicit `id=` kwarg + `**model_dump()` collided) until `id` was excluded there.
- **PATCH/PUT are already replay-safe by construction** (field-level LWW: applying
  the same op twice yields the same row, only `updated_at` re-stamps). No endpoint
  changes needed; covered by replay-twice tests instead.
- **Hardened `CRUDBase.update` to never repoint the primary key**: `LabelUpdate`
  inherits the optional `id` from `LabelCreate`, so a replayed/hostile label PATCH
  body could otherwise have moved the row. The update loop now skips `id`
  unconditionally (no legitimate path updates a PK).
- **Terminal vs retryable error set (the outbox contract for WI-7):** `409`
  (id collision, create) and `410` (row tombstoned) and `403` (access revoked /
  insufficient permission) and `404` (never existed) are all **terminal**;
  network / `5xx` are retryable. 403/410 were already enforced at the shared
  access guards in WI-2; WI-3 adds the 409 collision and the 410 create-replay.

### WI-4 — Delta feed: `GET /api/changes?since=<cursor>` (L)

**Goal:** One endpoint that tells a device everything that changed since its cursor.

- **Cursor design decision (do first, document in the endpoint docstring):**
  recommend a global monotonic `server_seq` (Postgres sequence; SQLite dev
  equivalent) stamped alongside `updated_at` — timestamp cursors have
  same-instant and clock-granularity edge cases. `(updated_at, id)` with an
  overlap window is the fallback if a sequence column is unwanted.
- Response shape: changed rows grouped per entity (flat, matching store
  shapes), tombstones, `removed_checklist_ids` (access revocations),
  `next_cursor`, and a `full_resync` flag for "your cursor is too old / unknown".
- Access filtering reuses the existing owner+collaborator access query
  (careful: pending invitees are excluded only by the position inner-join,
  not a status filter — don't accidentally leak to them here).
- **Access-gain delivery:** when a user gains access to a checklist, the next
  delta must deliver the checklist *and all its children* regardless of their
  `server_seq` (they predate the grant). Emit synthetic changes keyed off the
  collaborator row's seq, or touch the checklist tree on share changes —
  decide in-session.
- Tests: ordinary edits, tombstones, gain access, lose access, cursor
  pagination, unknown cursor → full_resync.

**Split seam if it runs long:** land the endpoint + ordinary-changes tests first;
access-gain/loss semantics as the second half.

**Done when:** backend tests show two simulated devices converging through the
endpoint across all scenarios above.

**Decisions taken in-session (2026-07-06, implemented):**

- **Cursor = global `server_seq`** (chose the recommended option over
  `(updated_at, id)`). A single-row counter table (`sync_seq`, model
  `SyncSequence` in `model/_base_model.py`) hands out a strictly monotonic int;
  `TimestampedModel` gains a nullable, indexed `server_seq` column stamped on
  **every** insert *and* update by `before_insert` / `before_update` mapper events
  (same choke point as the `updated_at` stamp, so no CRUD/bulk path can bypass
  it — a `soft_delete` bumps it too, so tombstones surface). The allocator
  (`_allocate_server_seq`) does `UPDATE sync_seq … +1` then `SELECT` on the flush's
  own connection; the row lock is held to commit, so **committed `server_seq`
  values are monotonic in commit order** — a reader that consumed up to N can
  never miss a row that commits later with a smaller seq. This globally serialises
  the commit tail of writes; acceptable at this app's scale, documented on the
  model. The counter row is seeded to 0 right after `create_all` (`INSERT …
  ON CONFLICT (id) DO NOTHING`, valid on both backends); model change only, no
  Alembic (decision 7). Used sqlmodel's `Field` (not the pydantic `Field` the
  module aliases) for the new columns so `index=True` / `primary_key` actually
  take effect.
- **`next_cursor` = server high-water mark read *before* the entity queries.** A
  row that commits mid-pull is therefore delivered now *and* re-delivered next
  pull (harmless — LWW application is idempotent) rather than skipped. At-least-
  once, never at-most-once.
- **`full_resync`** triggers only when `since < 0` or `since > current max seq`
  (client ahead of a reset/restored DB — no per-client state to consult). It is
  computed as `since = 0` and flagged so the client drops its cache. With a
  never-reset, un-GC'd sequence a normal cursor is never "too old".
- **Access-gain** is keyed off the caller's **accepted collaborator row's**
  `server_seq` (`list_gained_access_checklist_ids`): when it is `> since` the whole
  card tree predates the grant, so the feed ships the card **and all its live
  items** regardless of their seq. Owned/pre-existing cards need no special case
  (their own rows carry a fresh seq). A permission change re-delivers the tree
  (minor, documented waste).
- **Access-loss → `removed_checklist_ids` via a client-supplied `known` list.**
  WI-2 kept collaborator revoke a **hard delete** (no seq signal), and the server
  is stateless, so the endpoint takes an optional comma-separated `known` query
  param (the ids the client currently caches) and returns
  `known − currently-accessible − tombstoned`. This directly implements WI-2's
  "diff the access query" note **without** tombstoning collaborators (avoids the
  re-share resurrection semantics). Online clients still learn of revocation
  immediately via the existing SSE `share_removed`/`checklist_deleted` poke; this
  is the offline catch-up path.
- **Response shape** reuses the existing read models verbatim
  (`CheckListApiWithSubObj` / `CheckListItemRead` / `LabelReadAPI`) so nested
  position/state/label serialisation and `my_permission` match every other
  endpoint and the client stores. Changed rows are grouped flat per entity;
  removals are id lists (`checklist_tombstones`, `item_tombstones`,
  `label_tombstones`, `removed_checklist_ids`). A card is emitted at card-level
  when its row / the caller's position / the caller's labels changed; item changes
  surface independently (an item edit does **not** re-emit its card).
- **Pagination deferred.** The delta is computed in one response (bounded account
  size — SQLite is dev-only, Postgres scale is modest). `next_cursor` is exposed
  and the convergence tests walk the cursor to empty; true server-side page
  limiting is left for later if an account ever needs it.
- **Split seam ignored** — both halves (endpoint + ordinary changes, and
  access-gain/loss) landed in one session; `tests/tests_changes.py` covers
  ordinary edits, item state/position, tombstones, gain, loss, bootstrap,
  full_resync, and two-device convergence. Both harnesses green.

### WI-5 — Sync protocol glue: bootstrap, SSE poke, convergence suite (M)

**Goal:** A documented, tested client-facing sync contract.

- New-device bootstrap: define `since=0` (or a dedicated snapshot call) as
  "full state + fresh cursor"; make sure it's efficient enough for the
  largest realistic account.
- SSE: add a lightweight "changes available" event; keep the existing
  per-entity payloads working so the legacy (non-flagged) frontend path is
  untouched during the transition.
- Write `docs/SYNC_PROTOCOL.md`: cursor rules, response shape, error/terminal
  semantics, LWW rule, access-change behavior. This is the contract WI-6..11
  are built against.
- Convergence test scenarios: concurrent edits from two clients, offline
  outbox replay followed by delta pull, share-added mid-flight.

**Done when:** the protocol doc exists and the convergence suite is green.

**Decisions taken in-session (2026-07-06, implemented):**

- **Bootstrap is `GET /api/changes?since=0` — no separate endpoint.** WI-4 already
  makes `since=0` return the full accessible state (owned + accepted-collaborator
  cards, their items, the caller's labels) with `full_resync=false` and a fresh
  `next_cursor`; WI-5 just formalises and documents that as *the* bootstrap. No
  server-side pagination was added (deliberately deferred — see the doc's §3/§6);
  the response is computed in one shot, acceptable at this app's scale.
- **SSE poke = an ADDITIONAL `changes_available` event, emitted alongside every
  board-mutating per-entity event.** The frozen legacy per-entity payloads
  (`item_text`, `checklist`, `share_removed`, …) are left byte-for-byte unchanged
  so the flag-off frontend is untouched; the poke is a *second* message to the
  *same* recipients. It is emitted centrally in `SyncNotifiationCRUD.create()`
  (one place, not the ~20 call sites) via a new private `_emit()` that both the
  event and the poke share, so target resolution / transport (pg_notify vs SQLite
  drain) and the explicit-targets-on-delete behaviour are identical for both.
  `notification` (personal bell events — not board data the delta feed returns)
  and the poke itself emit no poke (avoids useless pulls / recursion).
- **The poke carries `server_seq`** (the WI-3 hint's optional nicety, taken): a new
  nullable `server_seq` field on `SyncNotification`, set to
  `get_current_server_seq()` at emit time and shipped in both the pg_notify JSON
  and the SQLite-drained payload. A flagged client can skip the pull when the
  poke's seq `<=` its cursor. Null on the legacy per-entity events. Model change
  only, no Alembic (decision 7) — the fresh test/dev DBs get the column via
  `create_all`. The legacy `handle()` switch has no `default` branch, so the new
  `upd_prop` value is a safe no-op there.
- **Convergence suite** (`tests/tests_convergence.py`) drives the real server over
  HTTP and covers: two clients editing the same shared card → LWW end-state; two
  clients editing different fields → both survive; an offline burst with a
  client-supplied id **replayed** (idempotent create, no duplicate) then pulled by
  a second device → converges; a share granted **mid-flight** → whole tree
  delivered on the recipient's next pull from an old cursor; and the
  `changes_available` poke riding alongside the legacy `item_text` event and
  carrying an advanced `server_seq`. Both harnesses green. "Concurrent" is
  interleaved sequential writes from two tokens (the harness has no true
  parallelism — noted in the module docstring).
- **`docs/SYNC_PROTOCOL.md`** written as the consolidated client contract (cursor
  rules, response shape + at-least-once delivery, LWW = server-arrival order,
  terminal-vs-retryable error set 409/410/403/404, access-gain full-tree /
  access-loss `known`-diff, and the two SSE message kinds). It restates WI-3/WI-4
  decisions; it invents nothing new. This is what WI-6..11 build against.

---

## Phase 2 — Frontend local-first layer

### WI-6 — Local persistence + hydration (M)

**Goal:** The board renders instantly from local data, network or not.

- IndexedDB snapshot layer (`idb` or similar) for checklist, checklist_item,
  label, user, publicConfig store state; snapshot format carries a schema
  version and is **disposable** (mismatch → drop + full resync; no client
  migrations to maintain).
- Boot: hydrate stores from cache synchronously-ish, then delta-pull in the
  background.
- Introduce the `localFirst` feature flag gating all of Phase 2.
- Debounced persistence on store changes (VueUse `watchDebounced` or a Pinia
  plugin).

**Done when:** with the flag on and network blocked, a reload still renders the
board read-only from cache.

**Decisions taken in-session (2026-07-06, implemented):**

- **Snapshot layer = `idb` over IndexedDB** (`utils/snapshotDb.ts`). One DB
  (`checkcheck-localfirst`), one kv object store. **Disposable + versioned:**
  `SNAPSHOT_SCHEMA_VERSION` is used as the IndexedDB version, so any bump runs
  the `upgrade` hook which drops and recreates the store — no client migrations,
  ever (protocol §6). A DB left at a *higher* version (app downgrade) throws
  `VersionError` on open; that's caught, the DB is `deleteDB`'d, and reopened
  fresh. All reads/writes are best-effort (swallow + warn) so a broken cache
  degrades to a network boot instead of a white screen.
- **Five stores snapshotted, slice-picked** (`utils/localSnapshot.ts` `SPECS`):
  `checkList` (only `checkLists` + `total_backend_count` + `counts`; **not** the
  transient search/filtered-view state), `checkListitem` (items + the
  full-loaded flag + the three count maps), `checkListLabelStore` (`labels`),
  `user` (`me` only — API keys are re-fetched, never cached), `publicConfig`
  (`config`). `share` / `invite` / `notification` / `color` are **not** persisted
  — they stay online-only (WI-12). Picked slices are JSON round-tripped before
  hitting IndexedDB (strips Vue reactive proxies, which don't survive structured
  clone).
- **Persistence = a Pinia plugin + `watchDebounced`** (`registerSnapshotPersistence`),
  registered via `pinia.use()` (applies to already-created stores too). Each
  snapshotted store deep-watches its picked slice and writes it 500 ms after the
  last change (2 s `maxWait`). Single writer for the entity snapshot — the
  legacy fetch path fills the stores, the watcher persists them.
- **Hydration before first paint via a client plugin** (`plugins/localFirst.client.ts`,
  **awaited**): registers persistence, then `hydrateStores` `$patch`es each store
  from its snapshot. Awaiting in the plugin means the stores are populated before
  any component mounts, so the board's `watchEffect` paints from cache
  immediately. The plugin runs on every route but only hydrates/persists (both
  harmless on `/login` and the anonymous `/p/<token>` viewer); the network pull
  is kept out of it.
- **Background delta pull scoped to the cursor** (`runBackgroundSync`, kicked off
  from `pages/index.vue` onMounted on the authed board only, so an `/api/changes`
  401 can't happen on public/login routes). On boot it reads the stored cursor,
  pulls `GET /api/changes?since=<cursor>&known=<cached ids>`, persists
  `next_cursor`, and on `full_resync` drops the snapshot (the legacy fetch +
  watcher rebuild it). **Deliberately does NOT apply the returned rows into the
  live stores** — that (and replacing the `useSync.ts` refetch) is WI-10. In WI-6
  the snapshot stays fresh via legacy-fetch→watcher; this pull only owns the
  cursor + `full_resync` so WI-10 inherits a correct high-water mark. The legacy
  SSE/refetch path is untouched.
- **`localFirst` flag is a CLIENT rollout gate, not a server capability**
  (`utils/localFirst.ts`). Default lives in `runtimeConfig.public.localFirst`
  (env `NUXT_PUBLIC_LOCAL_FIRST`), **default off** until WI-15. Distinct from the
  `GET /api/public-config` flags in `stores/publicConfig.ts`. Resolution order:
  `?localFirst=1|0` query param (also persisted to localStorage) → localStorage
  override → runtimeConfig default. The query-param/localStorage override exists
  because the E2E bundle is a static `nuxt generate` build where runtimeConfig
  can't be set per run — it's how the spec and manual testing flip the flag.
- **Not handled in WI-6 (documented gaps):** a *different* user logging in on the
  same device would briefly see the previous user's cached board until the pull
  resolves (per-user cache namespacing deferred — auth/identity work is WI-13);
  and a true offline *cold start* (app shell unreachable) needs the service
  worker (WI-13). WI-6's "network blocked" means the API only.
- **Verified:** new spec `tests/e2e/local-persistence.spec.ts` — flag on, card
  created, snapshot confirmed in IndexedDB, then `**/api/**` aborted and the page
  reloaded; the board still renders the card from cache. Green, plus `add-item` /
  `board-empty` re-run to confirm the flag-off path is untouched.

### WI-7 — Outbox (L)

**Goal:** Writes made offline survive restarts and replay correctly on reconnect.

- Persisted op queue in IndexedDB: op = endpoint descriptor + payload +
  client-generated ids + enqueue metadata.
- Replay engine: strict per-entity ordering, sequential drain, retry with
  backoff on network/5xx, **terminal** handling for 403/404/410 (drop op,
  emit an event WI-11 will surface).
- Coalescing: consecutive text edits to the same item collapse to the latest;
  create-then-delete offline cancels out.
- Online/offline detection (navigator.onLine + a probe request; SSE state as
  a signal).

**Split seam if it runs long:** queue + replay first; coalescing second.

**Done when:** unit tests cover replay, ordering, coalescing, and terminal
drops; a manual offline-edit → restart → reconnect round-trip syncs.

**Decisions taken in-session (2026-07-06, implemented):**

- **The outbox gets its OWN IndexedDB database** (`checkcheck-outbox`,
  `utils/outboxDb.ts`, `OUTBOX_SCHEMA_VERSION`), deliberately separate from the
  WI-6 snapshot DB (`checkcheck-localfirst`). This resolves the disposability
  tension flagged in the plan: the snapshot's `upgrade` hook drops every store on
  a `SNAPSHOT_SCHEMA_VERSION` bump (disposable by design — protocol §6), but
  queued offline writes are *precious*. Two DBs, versioned independently, means a
  snapshot-shape change can never silently discard unsynced writes (and vice
  versa). One object store `ops`, keyed by the op's monotonic `seq`; the engine
  treats it as a whole-queue load/rewrite (`OutboxStore.load` / `persist`) — the
  queue is tiny so a clear+rewrite per change is simpler than per-op diffs and
  avoids a separate seq-counter row.
- **Engine core is framework-free** (`utils/outbox.ts`): op shape, error
  classification, coalescing, and the replay engine import nothing from
  Nuxt/Vue/idb, so they unit-test in plain vitest with deps (store, `$checkapi`
  transport, connectivity, scheduler, clock) injected. The IndexedDB store,
  transport and connectivity are wired in `composables/useOutbox.ts` (a
  `createSharedComposable` singleton) and booted by `plugins/outbox.client.ts`
  when `isLocalFirstEnabled()`.
- **Op shape** = endpoint descriptor + payload + ids + metadata:
  `{ seq, opId, entityType, entityId, kind, request:{method,path,pathParams,
  query,body}, enqueuedAt, attempts }`. `path` is the openFetch path *template*
  with `pathParams` (not a pre-interpolated URL) so ops stay inspectable and
  coalescable. `entityId` carries the **client-generated UUID** — for a `create`
  it is the client-supplied id the endpoint accepts (protocol §8 / WI-3), so a
  replay is idempotent; it is also half of the `(entityType, entityId)` per-entity
  ordering key. **The stores do NOT build/enqueue ops yet** (WI-8 items, WI-9
  positions/checklists) — WI-7 only models the shape and stands up the engine so
  those items drop in via `useOutbox().enqueue(...)`.
- **Drain = a single serial loop in `seq` order.** Since ops for one entity keep
  their relative enqueue order, a global serial drain trivially satisfies "strict
  per-entity ordering" — chosen over per-entity lanes as simplest-correct at this
  app's scale. A **retryable** failure stops the whole drain and arms one backoff
  timer (head-of-line blocking, accepted); a **terminal** failure drops just that
  op (emits `op-dropped`) and the drain continues past it. Exponential backoff
  with full jitter, capped at 30 s. A fresh `enqueue` while a backoff timer is
  armed does **not** re-kick the drain (would hammer a failing server); only
  `setOnline(true)` (a real connectivity change) cancels the timer and drains now.
- **Terminal vs retryable follows protocol §8** (`classifyError`): network (no
  status) + `5xx` are retryable; `403/404/409/410` are terminal → `op-dropped`
  (WI-11 surfaces it). For statuses the contract leaves open: `401`/`408`/`429`
  are **retryable** (a `401` is session-expiry — WI-13 adds an offline-auth grace;
  dropping a queued write on a transient blip is worse than holding it), and every
  other `4xx` (e.g. `400`/`422` validation) is **terminal** (a malformed op never
  self-heals). Documented on the function.
- **Coalescing** (pure `coalesce(queue, incoming, lockedSeqs)`), applied at
  enqueue over the *non-in-flight* queue: (1) consecutive update-like edits
  (`update`/`state`/`position`) to the same entity merge field-by-field into the
  earlier queued op (LWW, incoming wins), keeping its slot — only the final value
  is ever sent; (2) a `delete` whose entity still has a queued `create` removes
  **all** queued ops for that entity and drops the delete (create-then-delete
  offline cancels out); a `delete` with no queued create supersedes queued edits
  and is appended. The **in-flight op is locked** from coalescing — merging into a
  request already on the wire would silently lose the newer value, so a mid-flight
  edit appends as its own op instead.
- **Online/offline** (`utils/connectivity.ts`): `navigator.onLine` +
  `online`/`offline` events as the baseline, `setConnectivity()` fed by the SSE
  `onopen` in `composables/useSync.ts` (a live sync socket proves reachability —
  gated behind the flag so the frozen legacy path is untouched), and a `probe()`
  helper for the "interface up but server unreachable" case. The engine drains
  only while `isOnline()`.
- **Unit runner: vitest + fake-indexeddb** introduced this item (no runner
  existed — package.json had only Playwright). Natural Vite/Nuxt fit; the
  framework-free engine needs no Nuxt harness. `tests/unit/outbox.spec.ts` (22
  tests) covers classification, backoff, coalescing (all rules incl. the locked
  in-flight case), sequential ordering, offline gating, network-retry-then-succeed
  via a manual scheduler, terminal drop + `op-dropped` + continued drain, coalesce
  through the live queue, and a **restart round-trip against the real IndexedDB
  store** (Engine A queues offline → fresh Engine B loads and drains on
  reconnect). `bun run test:unit`. Chose unit tests over Playwright for the engine
  because the "done-when" targets (replay/ordering/coalescing/terminal) are pure
  logic best tested deterministically; the **UI** offline-edit round-trip needs
  the store wiring and lands with WI-8 (the flag-on E2E `local-persistence.spec`
  already confirms the outbox plugin boots without breaking hydration).
- **Scope kept clean:** no store `$checkapi` calls were rewired; `useSync.ts`
  gained only a flag-gated `setConnectivity(true)` on SSE open; `runBackgroundSync`
  (WI-6) is untouched (delta→store application is still WI-10).

### WI-8 — Item store goes optimistic-local (M)

**Goal:** First real store migration — items work fully offline.

- `checklist_item` store: `create` / `update` / `delete` / `updateState` keep
  their signatures but mutate local state immediately and enqueue to the
  outbox; `create` generates the UUID client-side.
- Server responses / delta events reconcile local rows (WI-10 completes this;
  until then the legacy SSE refetch path still runs for flagged-off users).
- Existing frontend e2e suite green with the flag on (item flows only).

**Done when:** item CRUD + check/uncheck work offline and converge on reconnect.

**Decisions taken in-session (2026-07-07, implemented):**

- **Dual-path, gated at the top of each action.** `create` / `update` / `delete` /
  `updateState` keep their signatures; when `isLocalFirstEnabled()` they delegate to
  a private `_local*` sibling (optimistic mutate + `useOutbox().enqueue(...)`) and
  `return` before the legacy code. Flag-off, the existing inline `$checkapi` bodies
  are reached **byte-for-byte** — a one-line guard per action, no fork of the store.
  Position/reorder actions (`updatePosition`, the `move/above|under` endpoints) and
  the checklist store are deliberately **left on the legacy path** (WI-9). The
  `useOutbox()` singleton is touched only inside the flag-on branch, so flag-off
  never constructs the engine.
- **Op builders in their own module** (`utils/outboxOps.ts`): `itemCreateOp` /
  `itemUpdateOp` / `itemStateOp` / `itemDeleteOp` map the item endpoints to WI-7's
  `OutboxOpInput` (path *template* + `pathParams`, `entityType:"item"`,
  `kind ∈ create|update|state|delete`). WI-7 shipped no builders on purpose; this is
  the first batch and WI-9 extends it for positions/checklists. Framework-free so it
  unit-tests in plain vitest (`tests/unit/outboxOps.spec.ts`).
- **`create` synthesises a full local row before the server confirms.** The id is
  `crypto.randomUUID()` (or a caller-supplied one), carried in the create op's `body.id`
  (WI-3's optional client id → protocol §8), so the eventual server row and WI-10
  delta upsert share it with no duplicate. A plausible `CheckListItemType` is built
  (nested `state{checked,updated_at}` / `position{index,indentation,updated_at}`,
  `text`, `updated_at`) so the board renders it immediately.
- **Append index = `max(existing index) + 1`** (`nextItemIndex`, `ITEM_INDEX_STEP`).
  The server assigns `position.index` online; offline WI-8 needs a **minimal** numeric
  append so `_insertNewAtCorrectIndex`'s binary search stays correct — the value is
  numeric and strictly larger than every existing one. A caller that already passes an
  explicit index (the "add item after" affordance) keeps it. This is *append only* —
  full fractional-index reorder (mid-list insert, drag) is **WI-9**; `decimal.js` was
  not pulled in here.
- **Count maps kept consistent offline.** `create`/`delete`/`updateState` shift
  `total_backend_count[_checked|_unchecked]_per_checklist` via a shared `_adjustCounts`
  (`?? 0`-guarded), so the sidebar badges are right when `checklistWasFullLoadedOnce`
  is false. `updateState` only shifts on a real flip (an idempotent re-check must not
  drift the counters — stricter than the legacy path, which is fine as this is new code).
- **Offline card-open resilience (one small read-path fix).** Opening a card in edit
  mode `await`s `refreshAllCheckListItems`, which **throws** offline and broke the
  editor mount. Guarded it with `.catch(() => {})` in `components/CheckList.vue`
  (matching the adjacent `fetch(...).catch` already there) so the editor opens on the
  hydrated/optimistic cache instead of throwing. Online behaviour is unchanged; the
  real delta-driven reconciliation replaces this refetch in WI-10.
- **Reconciliation is still the legacy SSE refetch until WI-10.** Flag-on users keep
  running `useSync().connect()`, so the per-entity refetch reconciles optimistic rows
  with server truth once the outbox drains (client id ⇒ no duplicate). **Known tension
  (not fixed here):** on SSE reconnect `useSync.onopen` calls `checkListStore.resync()`
  and card-open re-runs `refreshAllCheckListItems`, either of which can momentarily
  clobber optimistic rows whose ops haven't drained yet. The real fix is WI-10/WI-11.
- **Create-then-delete offline cancels out for free** via WI-7 coalesce rule 2 (both
  ops share `entityId`), so an item created and deleted while offline never reaches the
  server.
- **Verified:** new E2E `tests/e2e/local-item-offline.spec.ts` (flag on) — add an item,
  edit its text, `route.abort("**/api/**")`, reload → the item hydrates from the
  snapshot (reopen the card, see the textarea) and the ops are still queued in
  `checkcheck-outbox`; check the item offline; restore the API + reload → the outbox
  drains and the server item carries the text **and** `checked:true` under the client
  id. Plus `tests/unit/outboxOps.spec.ts` (builders + append-index math). Re-ran
  `add-item` / `item-movement` / `card-editor` / `local-persistence` to confirm the
  flag-off legacy path is untouched. `bun run test:unit` + the Playwright CLI via bun.
  The anonymous public viewer (`usePublicCard.ts` / `/p/[token]`) was left alone
  (online-only, WI-12).

### WI-9 — Reorder/positions + checklist store (M/L)

**Goal:** Full board manipulation offline.

- Item reorder offline: the `move/above|under` endpoints compute the
  fractional index server-side — offline needs **client-side index
  computation** (decimal.js is already on the client) plus a plain
  position PATCH as the outbox op. Keep the move endpoints for the legacy path.
- Sort tiebreak: order by `(position.index, item.id)` so identical fractional
  keys are deterministic (replaces the plan's jitter idea).
- `checklist` store: create / update (title, color) / delete / labels /
  position → optimistic + outbox, client UUIDs on create.

**Done when:** creating, editing, reordering, labeling, archiving and deleting
checklists and items all work offline and converge.

**Decisions taken in-session (2026-07-07, implemented):**

- **Same dual-path shape as WI-8**, extended to the checklist store, the item
  position/reorder actions, and the two label-association actions. Each action
  gets a one-line `if (isLocalFirstEnabled()) return this._local…(...)` guard at
  the top; flag-off falls through to the untouched legacy `$checkapi` body
  byte-for-byte. `archive` / `setPinned` / `reorderCheckLists` /
  `reorderChecklistItems` need **no** guard — they already delegate to
  `updatePosition` / the `move*` helpers, which now carry the guard, so both
  paths compose for free. Verified against the flag-off legacy path (`git diff`
  is guard-lines-only in the legacy bodies) and by re-running the legacy suite.
- **Client-side fractional index in `utils/outboxOps.ts`** (`fractionalIndexBetween`,
  decimal.js): the midpoint of two present neighbours, or one `POSITION_END_GAP`
  (`"0.4"`, matching the server) past a single end neighbour. It is the exact
  twin of the server's `move/{above,under}` math, so the client-computed key and
  the (later-online) server assignment agree. **Neighbours are read from the
  current cached order, NOT excluding the moved item** — this mirrors the
  server's `get_prev`/`get_next` over live DB rows, which makes "drag an item to
  where it already sits" a harmless near-no-op instead of a jump. The offline op
  is a **plain position PATCH** (`kind:"position"`, coalescable) — the plain
  endpoint stores the given index verbatim, so no server recomputation and a
  clean round-trip. The legacy `move/{above,under}` PUTs stay for flag-off.
- **Items sort ascending, checklists descending** (`_sort`), so "above"/"under"
  map to opposite index directions between the two stores — the item move helper
  and the checklist move helper pass the (before, after) neighbour pair
  accordingly into the one shared `fractionalIndexBetween`.
- **Sort tiebreak `(index, id)`** added to the item `_sort` (and the neighbour
  sorts) so equal fractional keys — possible after two clients converge on the
  same midpoint — order deterministically instead of flapping. Harmless on the
  legacy path (equal indices are degenerate there).
- **Checklist `create` synthesises a full `CheckListApiWithSubObj` row** with a
  client UUID (`crypto.randomUUID()`), `my_permission:"owner"`, `owner_id` from
  the user store, an empty **fully-loaded** item list (so the board renders it
  without the online path's offline-failing `refreshAllCheckListItems`), and an
  index of `highest + 0.4` (mirrors the server). The create op sends that
  **explicit** `position.index` so a replay stores the same slot the board shows
  (omitting it makes the server recompute `highest+0.4` at replay time — a
  divergence). Colour edits resolve the nested `color` object from the colour
  store on the optimistic row (the board renders `color`, not `color_id`).
- **Label attach/detach is a `(checklist,label)`-pair op**, not label CRUD (which
  stays online — WI-12). `entityType:"label"`, `entityId:"{clId}:{labelId}"`,
  `kind` create (PUT) / delete (DELETE) — both idempotent, and an attach-then-
  detach of the same pair cancels via the WI-7 create/delete coalesce (rule 2).
- **Op builders live in `utils/outboxOps.ts`** (the WI-8 module), extended with
  `itemPositionOp`, `checklist{Create,Update,Delete,Position}Op`,
  `checklistLabel{Add,Remove}Op`, `checklistLabelKey`, `fractionalIndexBetween`
  and `POSITION_END_GAP`. Unit-tested in `tests/unit/outboxOps.spec.ts` (28
  tests: builders + the fractional math incl. a 20-deep mid-list-insert
  no-drift check). `bun run test:unit`.
- **Verified:** new E2E `tests/e2e/local-checklist-offline.spec.ts` (flag on) —
  (A) create a card offline (client UUID from the `/card/<id>` URL), title it,
  reload still-offline → it hydrates titled from the snapshot and the ops stay
  queued; restore the API + reload → the outbox drains and the server card
  carries the title under the client id. (B) drag one item below another offline
  → optimistic reorder + a queued position op; restore + reload → the server
  converges on the new order. Re-ran the flag-off `item-movement` /
  `card-movement` / `checklist` / `label-reorder` and the WI-8
  `local-item-offline` to confirm the legacy + item paths are untouched. The
  E2E backend's `openapi.json` version churn was `git checkout`-ed out.
- **Known gaps (deferred, not blockers):** offline **restore-from-Archive** goes
  through `checkListStore.fetch()`, which refreshes (throws offline) for a card
  not in `checkLists` — archive-from-board (the common flow) works; the
  archive-view edge is WI-10/12 territory. Reconciliation is still the legacy SSE
  refetch until WI-10, so the WI-8 "resync can momentarily clobber undrained
  optimistic rows" tension applies to checklists/positions too.

### WI-10 — Delta application replaces `useSync` refetch (M)

**Goal:** One code path applies server truth, live or after an offline gap.

- On SSE poke or reconnect: pull `/api/changes`, apply rows/tombstones/
  `removed_checklist_ids` into stores, persist the new cursor.
- Replace the per-entity refetch logic in `useSync.ts` for flagged mode
  (keep count-badge refresh behavior, driven by applied deltas).
- Retain the focused-field edit protection when applying remote text changes.
- `full_resync` handling: drop cache, re-bootstrap.

**Done when:** two browsers converge live via poke→delta, and a browser that
was offline for an hour converges on reconnect — no full refetches.

**Decisions taken in-session (2026-07-07, implemented):**

- **One framework-free merge core (`utils/deltaApply.ts` `mergeDelta`)** folds a
  `ChangesResponse` into plain array/record slices that the app backs with the
  live Pinia store state (`checkLists`, `checkListsItems`, `labels`, plus the
  item count maps). Kept Nuxt/Vue-free like the WI-7 outbox engine, so it
  unit-tests in plain vitest (`tests/unit/deltaApply.spec.ts`, 21 tests: upsert,
  sort, tombstone, `removed_checklist_ids`, LWW-on-focused-field, count
  recompute/adjust, and an **idempotent re-apply** no-op). Mutating the reactive
  store arrays in place is reactive, so the same function serves both tests and
  the live app.
- **`applyDelta(pinia)` in `utils/localSnapshot.ts` is the single read path.** It
  pulls `GET /api/changes?since=<cursor>&known=<ids>`, calls `mergeDelta`,
  persists `next_cursor`, and walks the cursor to empty (bounded to 20 pages).
  Overlapping triggers are serialised through a module-level promise chain
  (idempotent, but avoids cursor races from bursty pokes). `runBackgroundSync`
  (the WI-6 boot pull) is now a thin wrapper over it — the "NOTE: scope boundary"
  is gone; boot applies rows.
- **`full_resync` = drop + wholesale rebuild** (`rebuildFromFull`): a since=0
  response is the caller's entire accessible state, so it replaces the stores
  outright and marks every card **fully loaded** (a bootstrap ships all live
  items). A **normal** since=0 pull (cursor 0, no snapshot) stays an *upsert* via
  `mergeDelta`, NOT a rebuild — a rebuild would wipe undrained offline-optimistic
  rows the outbox hasn't sent yet; upsert is non-destructive (it only removes
  ids the server explicitly tombstoned/revoked). `rebuildFromFull` is reserved
  for the genuine "client ahead of a reset DB" case (§5).
- **`useSync.ts` dual path.** Flag-on, the SSE `changes_available` poke is the
  ONLY board-reconcile trigger (`applyDelta(pinia, { sinceSeq: server_seq })`,
  skipping the pull when `server_seq <= cursor`, §9b); an SSE **reconnect** does
  one `applyDelta` instead of the legacy `resync()` + `fetchCounts()`. The frozen
  per-entity events are ignored for board state flag-on, but the **side-channel
  stores that are NOT in the delta feed** (shares → `refreshIfOpen`, invites,
  notifications — online-only, WI-12) still react to their own events via a new
  `handleSideChannel`. Flag-**off** keeps the entire legacy switch byte-for-byte.
- **Sidebar counts are delta-driven, not blanket.** `mergeDelta` returns a
  `cardLevelChanged` flag set only when a card create/delete/tombstone/revoke or
  an **archived/pinned/label-set** change lands (a pure name/text edit does not);
  `applyDelta` then debounces a server-authoritative `fetchCounts()`. Per-checklist
  item preview counts are maintained the WI-8 `_adjustCounts` way (incremental for
  preview lists; recomputed exactly from the array for fully-loaded lists).
  `total_backend_count` follows `cardCountDelta` (guarded against the −1 "never
  loaded" sentinel).
- **Focused-edit protection lifted to the store layer.** New `utils/editGuard.ts`
  is a tiny module-level registry (a `Set`, connectivity.ts-style) that
  CheckList.vue / CheckListItem.vue mark on focus and clear on blur/unmount;
  `mergeDelta` consults it and keeps the local `name`/`text` for a field the user
  is actively editing while still applying the rest of the server row (§4). This
  is the seam WI-11's conflict toast plugs into. The existing component
  `localName`/`localText` focus guards stay as the textarea-level defence.
- **Card-open no longer blind-refetches flag-on.** The `refreshAllCheckListItems`
  on opening a card now runs flag-on **only the first time** a card is opened
  (never fully loaded locally — backfills a preview-only card); once loaded,
  deltas keep it fresh, so we don't refetch and don't clobber undrained optimistic
  edits (the known WI-8 tension). Flag-off is unchanged. The board's initial
  `fetchNextPage` on mount is deliberately kept for both paths (boot bootstrap, not
  an SSE-driven refetch — outside the "no full refetches" done-when).
- **Verified:** new E2E `tests/e2e/local-delta-apply.spec.ts` (flag on) — (1) a
  change in tab A converges into tab B via poke→delta, asserting B hit
  `/api/changes` and **not** a `GET /api/checklist` board list; (2) a tab put
  offline with `context.setOffline(true)` (drops the SSE stream) misses a rename
  made from an independent online context, then converges on reconnect via one
  delta pull, again no board refetch. Re-ran the flag-off `sync` (legacy SSE) and
  the WI-8/9 `local-item-offline` / `local-checklist-offline` /
  `local-persistence` / movement specs — both paths green. Typecheck adds no new
  errors over the pre-existing baseline; `openapi.json` version churn
  `git checkout`-ed out.

### WI-11 — Conflict & revocation UX (M)

**Goal:** Concurrent shared-list editing feels sane; losing access offline is
handled gracefully. (Core scope per decision 3 — not an afterthought.)

- Superseded-edit detection: an incoming delta that overwrites a field the
  user changed locally in this session → unobtrusive toast ("Item updated by
  Anna — your change was replaced").
- Terminal outbox drops (from WI-7): access revoked / row deleted while
  offline-edited → discard local state, show a clear one-time message, remove
  the card cleanly.
- Delete-vs-edit: tombstone wins; notify the editor.
- Pending-changes indicator per card while its ops are queued (feeds WI-14).

**Done when:** a scripted matrix of conflict scenarios (documented in the test
plan) each produce the designed UX, verified in e2e where feasible.

### WI-12 — Remaining stores + online-only surfaces (S/M)

**Goal:** No dead-ends offline in surfaces that stay server-authoritative.

- `share`, `invite`, `notification` stores stay online-only (per plan §5):
  disable their actions offline with a hint, queue nothing.
- Public viewer `/p/[token]` untouched (online-only, read-only).
- Sweep components for direct `$checkapi` usage outside stores; route or gate.

**Done when:** every UI surface either works offline or clearly says why not.

---

## Phase 3 — Offline PWA

### WI-13 — Service worker app shell + offline auth grace (M)

**Goal:** Cold start with no network loads the app.

- `@vite-pwa/nuxt` (Workbox): precache the SPA shell, sensible update flow
  ("new version available" toast), never cache `/api/*`.
- Installability: manifest, icons, theme colors.
- Auth offline grace: an expired session while offline must **not** bounce to
  the OIDC redirect — keep the app usable on local data, block only sync
  upload, refresh session on reconnect.

**Done when:** airplane-mode cold start renders the board with local data.

### WI-14 — Sync status UI (S/M)

**Goal:** The user always knows where their data stands.

- Global indicator: online/offline, syncing, pending-op count, last-synced
  timestamp; manual "sync now".
- Surface outbox terminal errors (from WI-7/11) in one consistent place.

**Done when:** the manual test matrix (offline, flaky, recovering, conflicted)
shows accurate states.

### WI-15 — Offline e2e suite, flag flip, release (M/L)

**Goal:** 2.0 ships with the flag on and offline behavior under CI.

- Playwright offline scenarios (`context.setOffline(true)`): offline edit →
  reconnect → converge; two-context concurrent-edit conflict; revocation
  while offline. (Run via the local `@playwright/test` CLI through bun; a few
  DnD/sharing specs are known-flaky — re-run before blaming changes.)
- Flip `localFirst` default on; remove or park the legacy refetch path.
- **Migrations resume here:** if 2.0 is the first release anyone runs in
  production, squash again to a clean baseline at the final 2.0 schema; from
  then on every schema change gets a real Alembic revision.
- Docs: README/user-facing notes, changelog, upgrade notes for self-hosters
  (recreate-DB requirement pre-2.0, new endpoint).

**Done when:** offline suite green, flag default on, 2.0 tagged.

---

## Explicitly deferred (2.1+)

- Tauri desktop shell (2.1) and Capacitor mobile (2.2) — packaging only, the
  data layer above already works in their webviews.
- Device-token auth for native shells (the existing `api_token` UserAuth
  scheme is the intended building block).
- Text CRDT for `item.text` (revisit if LWW + conflict toasts prove painful).
- Tombstone garbage collection job.
