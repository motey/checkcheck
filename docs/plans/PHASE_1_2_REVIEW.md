# Phase 1 + 2 review — findings (WI-1 … WI-10)

**Status:** Review complete (2026-07-07)
**Scope:** consolidation pass over the backend sync foundations (WI-1…5) and the
frontend local-first layer (WI-6…10), against the contract in
[SYNC_PROTOCOL.md](../SYNC_PROTOCOL.md) and the per-item decision blocks in
[VERSION_2.0_WORK_ITEMS.md](VERSION_2.0_WORK_ITEMS.md).

Small, clearly-correct issues were **fixed during the review** (the `review: …`
commits on `feature/2.0-offline-clients`); see §"Fixed inline" at the bottom.
This file holds the findings that are **design-level or need a decision**,
ranked most-severe first. It feeds WI-11+ planning.

**Fix session 2026-07-07:** findings **1, 6, 7, 10 are now RESOLVED** (see the
per-finding notes below).

**Fix session 2026-07-07 (2nd):** findings **3 and 4 are now RESOLVED** (see their
per-finding notes).

**WI-11 session 2026-07-07:** findings **2 and 5 are now RESOLVED** — folded into
WI-11 as planned (they share its edit-guard/outbox seam). See their per-finding
notes and the WI-11 entry in [VERSION_2.0_WORK_ITEMS.md](VERSION_2.0_WORK_ITEMS.md).
Remaining open findings, deliberately handed to a follow-up session:
- **8, 9** — connectivity signal on SSE error; outbox-persistence-failure
  surfacing. Both are **WI-13/WI-14** territory (status UX) — cheap but want the
  UI that consumes them.

---

## 1. HIGH — Ownership transfer never delivers the item tree through the feed

**RESOLVED 2026-07-07 (fix session).** Access-gain is now keyed off the caller's
`checklist_position` row creation (option 1), not the accepted-collaborator seq.
Added a `granted_seq` column (`GrantSeqMixin` in `_base_model.py`) stamped once on
insert and never on update; `CheckListPositionCRUD.list_gained_access_checklist_ids`
returns cards with `granted_seq > since`, so every grant path (share, invite
accept, public-link join, ownership transfer) delivers the full tree uniformly.
Permission-level changes to an existing collaborator now re-emit the card
(card-level only) via a new `collaborator_changed` EXISTS in
`list_changed_checklist_ids_for_user`, replacing the old whole-tree collaborator
gain. The dead `CheckListCollaboratorCRUD.list_gained_access_checklist_ids` was
removed. Tests added: `test_ownership_transfer_delivers_the_whole_tree_to_new_owner`,
`test_permission_level_change_re_emits_card_without_re_shipping_tree`.

**Where:** `routes_checklist_share.py::transfer_ownership` vs
`routes_changes.py` / `CheckListCollaboratorCRUD.list_gained_access_checklist_ids`

**Failure scenario:** A owns a card with 20 items. A transfers ownership to B,
who was **not** previously an accepted collaborator (nothing prevents this —
`transfer_ownership` accepts any user id and creates B's position row). B's
device pulls `/api/changes?since=<cursor>`: the **card** is emitted (the
`set_owner` update bumped the checklist row's seq, and B's fresh position row
matches the card-level query), but the **items** are not — access-gain delivery
is keyed *only* off an accepted **collaborator** row with `server_seq > since`
(§7), and B has no collaborator row (the transfer deletes it: "owner is not a
collaborator"). B sees an empty card. Flag-on, the WI-10 "first-open backfill"
rescues it once B opens the card; the board preview and counts stay wrong until
then, and a pure-feed client (future) never converges.

**Suggested fix:** give access-gain a signal that covers *every* grant path.
Options, in order of preference:
1. Key gain off **the caller's `CheckListPosition` row creation** — every grant
   path (instant share, invite accept, public-link join, ownership transfer)
   creates the position row at grant time. Needs "created since cursor", which
   `server_seq` alone can't distinguish from an update; add e.g. a
   `granted_seq`/`created_seq` column on `checklist_position` stamped once on
   insert (model change only — decision 7 means no migration needed).
2. Or special-case the transfer route: treat the new owner like a gain by
   touching every item row of the card (wasteful, and another route to forget).

Test to add with the fix: transfer to a non-collaborator, pull from the old
cursor, assert the full item tree arrives.

## 2. MEDIUM — Delta application clobbers undrained optimistic edits (non-focused fields)

**RESOLVED 2026-07-07 (WI-11).** The focused-edit guard was generalised from
"actively-typed name/text" to "any field with an undrained outbox op". New
`outboxFieldGuard(queue)` (utils/outbox.ts) maps each queued op to the DTO field
paths it will overwrite — `state.checked`, `position.index`, a card's `labels`,
`name`/`text`/`color_id` — and reports queued-delete rows via `isRemoved`. The
delta pull composes it with the focus registry (`combineGuards`, utils/editGuard)
and passes the result to `mergeDelta`, which now preserves every protected field
(nested `position`/`state` cloned so the incoming server DTO is never mutated) and
will not resurrect a row deleted offline. A concurrent server change to a
protected field is recorded in `DeltaSummary.conflicts` and surfaced as an
unobtrusive "also edited elsewhere" toast (WI-11 part 1) — the local value is
kept, so no flap and no lost write (LWW converges once the op drains). 21 new
vitest cases (deltaApply preservation/conflict/queued-delete; outboxFieldGuard
mapping). Frontend units green (104).

**Where:** `utils/deltaApply.ts` `mergeDelta` (checklist/item upsert), known
WI-8/WI-9 tension, now delta-shaped.

**Failure scenario:** offline (or slow-drain) edit queued in the outbox — say a
reorder (`position.index`) or a check — then a delta arrives for that row
(another user edited a *different* field). The server row still carries the old
position/state, `mergeDelta` takes the server row wholesale (only focused
`name`/`text` are guarded), so the local optimistic value visibly reverts; it
converges again only after the op drains and the next poke re-delivers. No data
is lost (LWW on the server ends correct) but the flap is user-visible and looks
like a lost write.

**Suggested fix (WI-11):** extend the edit-guard idea to the outbox — when
applying a server row, preserve locally the fields covered by that entity's
queued ops (the op bodies say exactly which fields are pending). The seam
exists: `mergeDelta` already takes an injected guard.

## 3. MEDIUM — Create-then-delete cancellation orphans child ops

**RESOLVED 2026-07-07 (2nd fix session).** `coalesce` rule 2 now cascades: when a
`delete` cancels a queued **checklist** `create`, it also drops that card's
never-sent child ops — item writes (matched by
`request.pathParams.checklist_id === id`) and checklist⇄label association ops
(matched by the `"{checklistId}:"` prefix of the pair `entityId`). A new
`isChecklistChild` helper encapsulates the match; locked (in-flight) child ops are
left alone, mirroring the existing rule-2 lock handling. Three vitest cases added
(`tests/unit/outbox.spec.ts`): the full offline create→items→label→delete cascade,
the no-cascade case when the card's create already drained (children are legit
server writes), and the locked-child case. Frontend units green (26 outbox specs).

**Where:** `utils/outbox.ts` `coalesce` rule 2.

**Failure scenario:** offline, user creates a card, adds three items, attaches a
label, then deletes the card. The card's create+delete cancel (rule 2 keys on
`entityId`), but the three item creates (`entityId = item id`) and the label
pair-op (`entityId = "cl:label"`) survive. On reconnect they replay against a
card the server never saw → 404, three-four terminal `op-dropped` events — which
WI-11 will surface as scary "your change was discarded" toasts for a flow the
user *intended*.

**Suggested fix:** when a delete cancels a queued create, also drop queued ops
whose request targets the cancelled entity (`pathParams.checklist_id === id`,
and the `clId:` prefix of label pair keys). Pure change in `coalesce` + unit
tests. Alternatively WI-11 can suppress the toasts for 404s that follow a
cancelled parent — but cancelling at the queue is cleaner.

## 4. MEDIUM — No concurrency test for the `server_seq` commit-order guarantee

**RESOLVED 2026-07-07 (2nd fix session).** Added
`tests/tests_server_seq_concurrency.py` — a Postgres-only test (skips under
`--db=sqlite`, which serialises writes and can't exercise the race). It fires 3
barrier-synchronised bursts of 24 simultaneous item creates while a reader thread
walks the cursor forward (never resetting `since` below what it reached), then
asserts every created id was delivered by the forward walk — i.e. no row committed
with a seq the cursor had already passed. Verified green on the Docker Postgres
harness (3 runs, ~9s each). The deadlock-retry expectation is now documented on
`_allocate_server_seq` (Postgres may abort a contending transaction → retryable
5xx → idempotent outbox replay; self-heals, shows up only as retry noise).

Incidental observation while writing the test (not a correctness bug, not chased):
every authenticated API-token request re-stamps `UserAuth.last_used_at`
(`api/auth/utils.py::validate_api_token` → `touch_last_used_at`), which is a
`TimestampedModel` UPDATE and so **allocates a fresh `server_seq` on every
request**. `UserAuth` is not in the delta feed, so this is harmless to sync
correctness, but it means the global cursor churns upward on pure read traffic (a
client polling `/api/changes` always sees `next_cursor` advanced even with an
empty payload). Worth a glance if cursor-advance is ever used as a "something
changed" signal on its own; pokes are event-driven, so nothing relies on it today.

**Where:** `model/_base_model.py` `_allocate_server_seq`; test harness gap.

The whole cursor design rests on "committed `server_seq` values are monotonic in
commit order" (allocator holds the counter-row lock to commit). The reasoning is
sound and SQLite serialises writes anyway, but on Postgres nothing *tests* it —
`tests_convergence.py` is interleaved-sequential by design (noted in its
docstring). Also note: holding the counter lock between a transaction's other
row locks widens the deadlock surface for multi-row transactions (Postgres
resolves via deadlock error → 5xx → outbox retries, so it self-heals, but it
will show up as noise under real concurrency).

**Suggested fix:** a Postgres-only pytest that fires N parallel writes (async
gather over HTTP) while a reader walks the cursor, asserting no row is ever
skipped (every id eventually delivered exactly-or-more-than-once, none lost).
Document the deadlock-retry expectation on the allocator.

## 5. MEDIUM — `full_resync` silently discards the meaning of queued ops

**RESOLVED 2026-07-07 (WI-11).** `rebuildFromFull` now reconciles the outbox
against the reset server BEFORE rebuilding. New `partitionResync(queue, knownIds)`
(pure, utils/outbox) splits the queue: a `create` re-POSTs its row and any op
whose target still exists (in the resync payload or re-created by a surviving
queued create) survives; an `update`/`state`/`position`/`delete` (or label
toggle) for a row the reset DB never knew would 404, so it is dropped here rather
than draining to a silent terminal error. `OutboxEngine.reconcileResync` applies
the partition (sparing the in-flight op), persists, and returns the dropped ops;
`localSnapshot` emits one aggregate `resync-dropped` sync-notice → a single
"server was reset; N pending changes couldn't be applied" toast (useSyncNotices).
9 new vitest cases (partitionResync + engine.reconcileResync).

**Where:** `utils/localSnapshot.ts` `rebuildFromFull` + outbox.

On `full_resync` (server DB reset/restore) the client rebuilds stores wholesale
— correct — but the outbox queue is left as-is and then drains against the reset
DB: queued creates re-create rows (fine), queued updates/deletes of rows the
reset server doesn't know hit 404 and are dropped as terminal. That's probably
the right outcome, but it happens silently today. WI-11 should surface it
("server was reset; N pending changes could not be applied") and WI-14's status
UI should show the queue was partially discarded.

## 6. LOW/MEDIUM — Item create is three separate commits

**RESOLVED 2026-07-07 (fix session).** Added `CRUDBase.stage_create` (validate +
`session.add`, no commit); `create_checklist_item` now stages item + position +
state and commits once, and `create_checklist` stages checklist + position and
commits once (both refresh the inserted rows afterward so relationships load
eager, not lazily during async serialization). The crash-between-commits window
is closed and two seq allocations per item create are saved. Full suite green.

**Where:** `routes_checklist_item.py::create_checklist_item` (item, then
position, then state — three `session.commit()`s).

A crash between commits leaves an item row without position/state. Every read
path inner-joins position (and the feed joins both), so the orphan is invisible
*forever* — and a create **replay** then finds the bare row and returns it with
`state`/`position` null, which the response model does not tolerate (5xx), so
the outbox retries a permanently broken op. Extremely narrow window, but the fix
is cheap: build all three rows and commit once (the mapper events stamp all
three in one transaction; also removes two seq allocations per create).
`create_checklist` has the same shape (checklist, then position).

## 7. LOW — `CRUDBase.get_multiple` does not mask tombstones

**RESOLVED 2026-07-07 (fix session).** Added the `deleted_at IS NULL` mask
(mirroring `list`) plus an `include_deleted` escape hatch, so the still-unused
method is no longer a tombstone-leak landmine.

**Where:** `db/_base_crud.py::get_multiple`.

Every other generic read path filters `deleted_at IS NULL`; `get_multiple`
doesn't. It currently has **no callers**, so this is a landmine, not a bug — but
the next person to call it on checklist/item/label gets tombstone leakage.
Either add the mask (mirroring `list`) or delete the method.

## 8. LOW — SSE error does not feed the connectivity signal

**Where:** `composables/useSync.ts` `es.onerror` / `utils/connectivity.ts`.

`onopen` proves reachability (`setConnectivity(true)`) but `onerror` never
reports loss, so after the server goes away with `navigator.onLine` still true
the outbox believes it is online and burns drain attempts into network errors
(backoff makes this cheap; it self-corrects on the next real signal). Worth a
`setConnectivity(false)` on error — or at least a `probe()` — when WI-14 starts
showing connectivity state to the user, so the indicator isn't lying.

## 9. LOW — Outbox persistence failure is only a console line

**Where:** `utils/outboxDb.ts` `persist` catch.

If the IndexedDB write fails (quota, private-mode eviction), the queue keeps
draining in memory but a reload before it drains silently loses the user's
writes. WI-14 should surface persistent-storage failure; consider
`navigator.storage.persist()` at boot (WI-13 territory).

## 10. DOC — WI-2 decision block contradicts the implemented owner-delete

**RESOLVED 2026-07-07 (fix session).** WI-2's `checklist_collaborator` decision
bullet in `VERSION_2.0_WORK_ITEMS.md` now separates revoke/leave (hard-delete the
link + position) from whole-card owner delete (tombstones the card, keeps the
rows so the tombstone stays resolvable to collaborators).

**Where:** `VERSION_2.0_WORK_ITEMS.md` WI-2 decisions ("Revoke / leave /
whole-card delete keep hard-deleting collaborator + per-user position rows") vs
`routes_checklist.py::delete_checklist` (owner delete tombstones the card and
**keeps** collaborator + position rows, so the tombstone stays resolvable to
collaborators — which is what `list_tombstoned_checklist_ids_for_user` relies
on). The code is right; the decision text describes only revoke/leave. Fix the
sentence when the doc is next touched.

---

## Test-harness gaps (known, deliberate — not bugs)

- **Conflict / concurrent-edit / offline-revocation E2E** — none yet; that *is*
  WI-11's done-when, don't build early. The backend halves are covered
  (`tests_convergence.py`, `tests_changes.py`, and the new
  `test_revoked_collaborator_write_is_403_terminal`).
- **`server_seq` under true concurrency** — CLOSED (finding 4):
  `tests/tests_server_seq_concurrency.py` (Postgres-only).
- **Frontend units** — `outbox`, `outboxOps`, `deltaApply`, and now `editGuard`
  + `connectivity` have specs. Still unit-less: `snapshotDb` (needs
  fake-indexeddb plumbing; its logic is a thin idb wrapper — low value) and the
  store optimistic helpers (`_localCreate`/`_localMoveItem` etc. — need a Pinia
  harness; their pure math already lives in tested `outboxOps`). Deemed not
  worth the harness cost now; revisit if they start growing logic.
- **Known flaky E2E** — the DnD/sharing specs, and `counts.spec.ts` badge
  assertions when run in parallel with other card-creating specs (relative
  assertions on the shared admin board race across workers). Re-run
  individually before blaming a change.
- **`sharing-modal.spec.ts` "owner can add and remove a collaborator" fails
  deterministically** (4/4 runs this session, including alone), timing out on
  the dialog's user-search input — and it fails **identically on the pre-review
  WI-10 commit**, so it is pre-existing, not introduced by this review. It was
  previously binned as per-run flake; today it looks environment/state-shaped
  (its 4 sibling tests in the same file pass). Worth a focused debugging
  session before WI-11 leans on the share dialog.
- **`archive.spec.ts` delete-forever asserted the pre-WI-2 404** — it had not
  been re-run since tombstones landed. Fixed inline (now expects 410); a
  reminder that "suite green" claims in the WI logs covered *selected* specs,
  not the whole suite.

---

## Fixed inline during this review (`review:` commits)

1. **Label detach never surfaced in `/api/changes`** (HIGH) — hard-deleted link
   row leaves no seq trace; even live flag-on clients kept the stale chip. Fixed
   by re-stamping the caller's position row on detach
   (`CheckListPositionCRUD.touch`); repro test added.
2. **No-access was 401, protocol says 403** (HIGH) — the outbox classifies 401
   as retryable session expiry, so a revoked collaborator's queued writes would
   retry forever and head-of-line block the queue; the legacy client also
   bounced to /login. Guard now raises 403; regression test drives the queued-op
   replay shapes after a revoke.
3. **Flag-on: deleted label's chip lingered on cards** — the server never
   re-emits cards on label delete, so `mergeDelta` now strips the chip from
   cached cards on a label tombstone.
4. **Flag-on: `known=` reported offline-created cards** — the server echoed them
   back as `removed_checklist_ids` and the client deleted its own optimistic
   card. The pull now excludes entity ids with a queued outbox create.
5. **Flag-on: preview-card count drift** — an upserted item outside the preview
   window is indistinguishable from a new one (and a tombstone outside the
   window decrements nothing); `applyDelta` now schedules an authoritative
   per-card count refetch for touched preview-only cards.
6. **`GET /checklist/{id}/item?checked=` was a no-op** — filter join was built
   and discarded.
7. **Foreign-label update/delete returned 401** → 404 (per-user resource, no
   existence leak, no session-expiry misfire).
8. New regression tests: fresh user's first label create; editGuard +
   connectivity unit specs.
