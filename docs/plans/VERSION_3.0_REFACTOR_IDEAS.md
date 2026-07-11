# 3.0 refactor ideas — maintainability & user-scale

Written 2026-07-11 with the full 2.0 branch in context. Target: what to change
if CheckCheck needs to (a) stay maintainable as contributors rotate and
(b) scale in **user numbers** (more accounts, more concurrent devices — not
bigger single boards). Ordered so the highest leverage-per-risk comes first.
None of this blocks tagging 2.0; items marked ⚠ are the ones that actively
hurt at scale.

## 1. Scalability (server)

- ⚠ **Replace the single global `sync_seq` counter row.** It serializes the
  commit tail of *every* write in the system and widens the deadlock surface
  (`_base_model.py::_allocate_server_seq` holds the row lock until commit).
  Fine for one household, lethal for thousands of concurrent users. Options,
  in ascending effort:
  1. **Per-account counter row** (counter keyed by owner/workspace) — writes
     from different accounts stop contending entirely; the cursor stays a
     plain int per client because a client only syncs one account. Smallest
     conceptual change, biggest win.
  2. Postgres-native: `pg_current_xact_id()`-style / sequence + "no gaps
     visible" handling — more throughput, much subtler correctness story
     (the current design's *commit-order* guarantee is what makes cursors
     safe; don't trade it away silently).
  Keep the commit-order invariant either way; `tests_server_seq_concurrency.py`
  is the guard rail and needs to grow with this change.
- ⚠ **SSE fan-out.** Today every app process receives every `pg_notify` event
  and filters per connection; each browser tab holds one SSE connection.
  At user scale: move target-resolution out of the hot path (it queries
  collaborators per event), consider consolidating to one poke *type* keyed by
  account, and put a connection budget/heartbeat story in place. If horizontal
  scaling beyond a few processes is ever needed, swap pg_notify for a real
  broker behind the same `SyncNotifiationCRUD` seam (it's already the single
  choke point — good; keep it that way).
- **Paginate `GET /api/changes`.** The contract already reserves the shape
  (client walks `next_cursor` to empty; §3 explicitly defers server limiting).
  Add a `limit` + "has_more" semantics without changing the client algorithm.
  Do the same for the bootstrap (`since=0`) case — it currently ships the
  whole account in one response.
- **Tombstone GC** (already deferred in the protocol): a periodic job deleting
  tombstones older than N days, paired with a documented "cursor older than
  the GC horizon ⇒ `full_resync`" rule. Without it, every delta query scans a
  monotonically growing table forever.
- **Counts endpoint on the hot path.** Flag-on clients call `fetchCounts`
  after card-level deltas; at scale, either derive all sidebar counts
  client-side from the snapshot (the data is already there — the server call
  exists mostly for the preview-window blind spots) or cache it. This also
  resolves the `shared_by_me` actor-blindness class of bugs at the root.
- **Per-request transaction discipline.** `CRUDBase` commits per call, so one
  REST request can span several commits (create + position + state…). Most
  multi-row paths were patched with `stage_create`, but the default is still
  commit-per-CRUD-call. 3.0 should make "one request = one transaction" the
  rule (session-level `begin()` in the dependency), which also shrinks the
  seq-lock hold windows from item 1.

## 2. Client lifecycle & multi-device (the 2.0 blind spot)

- ⚠ **Key all client storage by user id** (snapshot, outbox, cursor) and clear
  on logout — 2.0_REVIEW_FINDINGS Chunk A is the tactical fix; 3.0 should make
  identity a first-class parameter of the persistence layer rather than a
  bolted-on check (e.g. DB name suffix `checkcheck-<userId>-…`, plus an
  eviction story for stale accounts).
- **Single-writer outbox across tabs.** Replace whole-queue clear+rewrite with
  per-op put/delete and Web Locks leader election (one tab drains, others
  enqueue). This turns the multi-tab race class off permanently instead of
  patching instances.
- **Make "access changed" a first-class seq event.** The owner-position
  `touch` trick (revoke, label-detach) is clever but is now cargo-culted in
  two places and must be remembered for every future hard-delete. A tiny
  `access_event` table (or tombstoning the collaborator row like everything
  else) would let the delta feed and the poke derive access changes uniformly
  — and would also fix "downgrade vs revoke" ambiguity (findings A3).

## 3. Maintainability (server)

- **Collapse the `CRUDBase` generics machinery.** Four type params, metaclass,
  `__init_subclass__` caching, and three fallback `get_*_cls` methods — for
  what is effectively a per-table module with 2–3 real queries. A plain
  explicit repository per entity (or a much thinner generic) would cut a whole
  layer of indirection new contributors currently have to decode.
- **One datetime policy, enforced.** Naive-UTC-everywhere works but is
  convention-by-memory; add a linter/test that rejects tz-aware datetimes in
  models, or bite the bullet and go tz-aware (`timestamptz`) in the 3.0 schema
  while migrations are still cheap.
- **Split `routes_checklist_share.py`** (invites, public links, groups,
  transfer, revoke in one file, ~1000 lines) along the same lines the tests
  already split.
- **Kill the flag fork.** Once 3.0 is local-first-only: delete the legacy SSE
  per-entity refetch path in `useSync`, the frozen event payloads' client
  handling, and every `isLocalFirstEnabled()` fork in the stores. That is the
  single biggest complexity payback available — the stores currently carry two
  complete mutation implementations each.

## 4. Maintainability (client)

- **De-duplicate sort/compare logic.** Card and item ordering comparators
  exist in the stores AND in `deltaApply.ts` ("mirrors store `_sort`") — a
  drift bug waiting. Extract one module both import.
- **Type the op catalog.** `OutboxRequest` stores openFetch path templates as
  strings; a typo compiles fine and 404s at replay time. Generate the op
  builders (or at least the path constants) from `openapi.json` like the
  client itself.
- **Give label pair-ops their own entity type.** The `"{clId}:{labelId}"`
  composite id inside `entityType: "label"` is a latent trap (see findings
  B3); a distinct `"checklist_label"` type makes the guard/partition logic
  honest and frees `"label"` for real offline label CRUD, which users will
  eventually ask for.
- **Store shape:** `checklist_item` keeps five parallel per-checklist record
  maps (items, fullyLoaded, 3 count maps) that must be mutated in lockstep
  (delta apply, optimistic ops, resync all do). Fold them into one
  `Record<clId, {items, fullyLoaded, counts}>` so a missed-lockstep bug
  becomes structurally impossible.

## 5. Testing / CI at scale

- **Contract tests over the protocol.** The protocol doc is prose; encode its
  rules (§3 apply-algorithm, §8 error table) as a shared fixture set that both
  backend tests and frontend unit tests consume, so the two ends can't drift.
- **Deflake before growing.** The parallel DnD/sharing flake set and the
  deterministic `sharing-modal.spec.ts:93` failure erode trust; at more
  contributors this becomes "everyone re-runs CI twice". Budget a focused
  deflake session (it's already a chunk in 2.0_REVIEW_FINDINGS).
- **Load test the sync path once.** One scripted scenario (N devices, one
  account; N accounts, one process) against Postgres to find the real numbers
  for items 1.1/1.2 before designing them — the current design's limits are
  asserted, not measured.

## 6. Explicit non-goals (keep saying no)

- **CRDTs** — still not warranted; per-field LWW + conflict toasts hold up.
  Revisit only for `item.text` if users actually collide in practice.
- **Client SQLite / heavy sync frameworks** — IndexedDB snapshot + outbox is
  small, debuggable, and testable; the 2.0 review found its bugs in the
  lifecycle glue, not the engine.
- **Per-client server state** — the stateless cursor design is the best
  property the protocol has; every scaling change above preserves it.

## Suggested sequencing

1. Finish 2.0_REVIEW_FINDINGS Chunks A/B (they are 3.0 prerequisites anyway).
2. §2 client lifecycle + §3 "kill the flag fork" — pure maintainability,
   no schema risk, huge complexity payback.
3. §1.1 per-account seq + §1 transaction discipline (one migration window,
   one concurrency-test expansion).
4. §1 pagination + tombstone GC (contract-compatible, ship independently).
5. §5 load test → decide whether §1.2/SSE broker work is needed at all.
