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

### WI-8 — Item store goes optimistic-local (M)

**Goal:** First real store migration — items work fully offline.

- `checklist_item` store: `create` / `update` / `delete` / `updateState` keep
  their signatures but mutate local state immediately and enqueue to the
  outbox; `create` generates the UUID client-side.
- Server responses / delta events reconcile local rows (WI-10 completes this;
  until then the legacy SSE refetch path still runs for flagged-off users).
- Existing frontend e2e suite green with the flag on (item flows only).

**Done when:** item CRUD + check/uncheck work offline and converge on reconnect.

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
