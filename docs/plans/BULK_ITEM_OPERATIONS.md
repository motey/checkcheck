# Bulk item operations — "Untick all items" & "Delete ticked items"

**Status:** ✅ Done (2026-07-19). Implemented as specified — dedicated
`POST /api/checklist/{id}/items/uncheck-all` (needs `check`) and
`.../items/delete-checked` (needs `edit`) endpoints, each replayed as one
append-only outbox op (`bulk_uncheck` / `bulk_delete_checked`), ORM-object
mutation so `server_seq` advances, kebab-menu entries with a confirm dialog for
the destructive one, and the legacy SSE `cli_id=null` refetch branch. Covered by
`tests_bulk_item_ops.py` (8 backend), the new unit specs, and
`bulk-item-ops.spec.ts` (E2E). The brief below is retained as the design record.

This document is the full implementation brief for a fresh session. Read it
top-to-bottom before writing code; it captures the research so you don't have to
re-derive it.

**Feature:** Add two entries to a card's kebab (⋮) menu:

- **Untick all items** — sets `state.checked = false` on every item of the card.
- **Delete ticked items** — soft-deletes (tombstones) every checked item.

**Hard requirement from the maintainer:** both must work **offline** (local-first),
like every other item write. They are *not* online-only. A user does not expect a
"untick all" button to be greyed out on a train.

**Core design decision (settled):** these are **dedicated server-side endpoints**,
replayed through the outbox as **one op each**, not a client-side fan-out of N
per-item ops. Two reasons, both decisive:

1. **The client does not hold all the items.** Board cards load only a *preview*
   window (`appConfig.previewItemCount * 2`) plus server-provided counts
   (`total_backend_count_{checked,unchecked}_per_checklist`). A client-side loop
   would silently touch only the loaded slice and leave the rest wrong. See
   [`stores/checklist_item.ts`](../../CheckCheck/frontend/stores/checklist_item.ts)
   `fetchMultipleChecklistsItemsPreview` and `getItemCount`.
2. **Op-wave.** N per-item ops = N requests + N `SyncNotification` rows + N outbox
   ops for every collaborator. One endpoint = one transaction, one poke, N changed
   rows delivered through the normal delta cursor.

---

## 1. How the relevant machinery works today (orientation)

Read [`docs/SYNC_PROTOCOL.md`](../SYNC_PROTOCOL.md) first — it is the client
contract. Key facts that shape this feature:

### 1.1 The delta feed is `server_seq`-driven, not notification-driven

- Every syncable row carries a global monotonic `server_seq`, **stamped by mapper
  events** (`before_insert` / `before_update`) in
  [`model/_base_model.py:206-224`](../../CheckCheck/backend/checkcheckserver/model/_base_model.py).
- `GET /api/changes` ([`routes_changes.py`](../../CheckCheck/backend/checkcheckserver/api/routes/routes_changes.py))
  returns every row with `server_seq > since`. Item changes surface when the item's
  **own row, its `state`, or its `position`** row advanced — see
  `CheckListItemCRUD.list_changed_items`
  ([`db/checklist_item.py:147`](../../CheckCheck/backend/checkcheckserver/db/checklist_item.py))
  which ORs `CheckListItem.server_seq`, `CheckListItemState.server_seq`,
  `CheckListItemPosition.server_seq`.
- Tombstoned items surface via `list_tombstoned_item_ids`
  ([`db/checklist_item.py:203`](../../CheckCheck/backend/checkcheckserver/db/checklist_item.py)),
  which returns ids where `deleted_at IS NOT NULL AND server_seq > since`.

**Consequence:** a bulk endpoint that (a) flips `CheckListItemState.checked` on the
ORM objects, or (b) soft-deletes the `CheckListItem` ORM objects, automatically
surfaces in the delta feed with **no new feed code**. The SSE side needs only a
single `changes_available` poke.

### 1.2 ⚠️ THE make-or-break backend gotcha: mapper events need ORM objects

`server_seq` is stamped by `before_insert` / `before_update` **mapper flush
events**. These fire for **dirtied ORM instances during a unit-of-work flush**.
They do **NOT** fire for a Core / ORM-enabled bulk statement
(`session.execute(update(CheckListItemState).where(...).values(checked=False))` or
`delete(...)`).

If you write the bulk op as a Core `UPDATE ... SET` / `DELETE`, `server_seq` is
**never bumped**, the delta feed never ships the change, and **offline clients
never converge.** The feature will look like it works for the actor (optimistic)
and silently break for everyone else.

**Therefore: load the rows as ORM objects and mutate them in a loop**, so each gets
a `before_update`. This is slower than a bulk `UPDATE`, but Postgres at this app's
scale is fine (see [`db-targets`] framing — modest scale, Postgres prod). The
comment at `_base_model.py:202` ("no bulk ORM write can bypass it") is about the
mapper-event *coverage of ORM flushes*; it is **not** a promise that Core bulk
statements are covered. Do not be misled by it.

### 1.3 The outbox (offline write queue)

Framework-free engine:
[`utils/outbox.ts`](../../CheckCheck/frontend/utils/outbox.ts). Op builders:
[`utils/outboxOps.ts`](../../CheckCheck/frontend/utils/outboxOps.ts). Nuxt glue:
[`composables/useOutbox.ts`](../../CheckCheck/frontend/composables/useOutbox.ts).

An `OutboxOp` is `{ entityType, entityId, kind, request, seq, opId, ... }`. The
engine drains **globally in `seq` order** (per-entity order preserved), retries on
network/5xx, drops on 403/404/409/410. **Even when online, local-first writes go
through the outbox** and drain immediately — so there is one uniform path, not an
online branch and an offline branch.

Behaviour that branches on `entityType` / `kind` and that a new op must be correct
against:

- **`coalesce`** (`outbox.ts:167`): rule 1 collapses consecutive *update-like*
  kinds (`COALESCABLE = {update, state, position}`) into one via field-LWW; rule 2
  makes a `delete` cancel a queued `create` (and cascade-cancel a checklist
  create's children). A `delete` with no queued create **supersedes queued edits**
  for the same entity.
- **`isChecklistChild`** (`outbox.ts:146`): an `item` op is a child of card X when
  `op.request.pathParams.checklist_id === X`. Used so cancelling a card's
  never-sent create drops its queued child ops.
- **`pendingChecklistIds`** (`outbox.ts:354`): drives the per-card "unsynced" badge;
  reads an item op's `pathParams.checklist_id`.
- **`partitionResync`** (`outbox.ts:385`): after a `full_resync`, an `item` op
  survives only if its parent card exists (`pathParams.checklist_id` in `knownIds`
  or re-created) **and** (`kind==="create"` or the item id exists).
- **`outboxFieldGuard`** (`outbox.ts:307`): maps queued ops → protected DTO fields
  so a concurrent delta doesn't revert a pending local edit.
- **`affectsSidebarCounts`** (`outbox.ts:463`): true only for ops that move the
  *card-count* badges (home/archived/labels). Item counts are **not** these.

### 1.4 Delta application & count reconciliation (the part that saves us)

The one read path is `mergeDelta`
([`utils/deltaApply.ts`](../../CheckCheck/frontend/utils/deltaApply.ts)), driven by
`applyDelta` / `pullAndApply`
([`utils/localSnapshot.ts:430-465`](../../CheckCheck/frontend/utils/localSnapshot.ts)).

The important existing safety net: **preview-only cards can't derive exact item
counts from a delta**, so after any pull that touches a not-fully-loaded card,
`pullAndApply` calls **`schedulePreviewCountsRefresh`**
([`localSnapshot.ts:252-273,451-454`](../../CheckCheck/frontend/utils/localSnapshot.ts)),
a debounced `fetchMultipleChecklistsItemsPreview(previewTouched)` that **re-reads
the authoritative per-card counts from the server**. Fully-loaded cards get exact
counts from `recountFromArray` inside `mergeDelta`.

**Consequence:** the count drift you might fear (a bulk untick makes the server
ship, say, 100 changed items to a preview card that only cached 20 — and
`mergeDelta` would incrementally `+1` each of the 80 it inserts) is **already
self-healed** by the existing preview-count refresh after the drain-triggered
delta pull. **We do not need a new count-reconcile mechanism.** We only need the
optimistic local update to be *reasonable* (see §4.2); the delta pull + preview
refresh converges it to server truth.

### 1.5 Permissions

`ChecklistAccessLevel` ladder is `view < check < edit < owner`
([`api/access.py:44-56`](../../CheckCheck/backend/checkcheckserver/api/access.py)).
Precedent from single-item routes:

- Setting item state (`PATCH .../item/{id}/state`) requires **`check`**
  ([`routes_checklist_item_state.py:127`](../../CheckCheck/backend/checkcheckserver/api/routes/routes_checklist_item_state.py)).
- Deleting an item (`DELETE .../item/{id}`) requires **`edit`**
  ([`routes_checklist_item.py:369`](../../CheckCheck/backend/checkcheckserver/api/routes/routes_checklist_item.py)).

So: **untick-all → `check`**, **delete-checked → `edit`**. Frontend gates with
`usePermissions().can(card, "check" | "edit")` (see
[`components/CheckListItem.vue:195-198`](../../CheckCheck/frontend/components/CheckListItem.vue)).

---

## 2. API design

Two new endpoints on the item-state / item routers (prefix `/api`, registered in
[`api/routers_map.py`](../../CheckCheck/backend/checkcheckserver/api/routers_map.py)):

```
POST /api/checklist/{checklist_id}/items/uncheck-all       # requires: check
POST /api/checklist/{checklist_id}/items/delete-checked    # requires: edit
```

- **Path is `/items/...`** (plural collection verb) to avoid colliding with the
  existing `/checklist/{id}/item/{item_id}/...` singular routes. Confirm no route
  conflict at registration.
- **No request body** (recommended default). The server operates on the card's
  current server-side item set:
  - `uncheck-all`: every live item whose state is `checked = true` → set `false`.
  - `delete-checked`: every live item whose state is `checked = true` → soft-delete.
- **Response:** return the fresh authoritative counts so an *online* caller could
  reconcile immediately, e.g.
  `{ "item_count": int, "item_checked_count": int, "item_unchecked_count": int }`.
  (Offline callers won't see it; they reconcile via the delta pull. Returning it is
  cheap and useful, and makes the endpoint testable without a second query.)
- **Idempotent replay** (required by the outbox, protocol §8):
  - `uncheck-all` replayed → still all-unchecked. Trivially idempotent.
  - `delete-checked` replayed → already-tombstoned items are skipped
    (tombstone-aware, like the single delete at `routes_checklist_item.py:392`);
    any *newly* checked items are deleted. This is **"delete whatever is checked
    now"** semantics — see the accepted edge in §6.
- **Emit exactly one `changes_available` poke.** Create a single `SyncNotification`
  (`cl_id = checklist_id`, `cli_id = None`, a suitable `upd_prop`). The
  `SyncNotifiationCRUD.create` path auto-appends **one** `changes_available` frame
  carrying the current `server_seq`
  ([`db/sync_notification.py:145-149`](../../CheckCheck/backend/checkcheckserver/db/sync_notification.py)),
  which is the only signal the local-first client needs.
  - For the **legacy (flag-off) client**, pick an `upd_prop` its SSE switch maps to
    a card-items refetch. The `upd_prop` literal set is enumerated on
    `SyncNotification` ([`model/sync_notifications.py:28`](../../CheckCheck/backend/checkcheckserver/model/sync_notifications.py))
    and handled in [`composables/useSync.ts`](../../CheckCheck/frontend/composables/useSync.ts).
    Reuse `item_state` for uncheck-all and `item_deleted` for delete-checked
    (`cli_id = None`), and **verify** the legacy switch does a card-level items
    refetch when `cli_id` is null (if not, either add a branch or add a new
    `upd_prop` literal + handler). Local-first is the default, so this is a
    lower-priority correctness item, but don't leave the legacy path broken.

### OpenAPI + generated types

After adding endpoints, regenerate the committed OpenAPI and the frontend types —
see the [`openapi-regen`] memory: dump `CheckCheck/openapi.json` (backend venv,
≥64-char dummy secrets, `SETUPTOOLS_SCM_PRETEND_VERSION` pinned), then
`bun run postinstall` in the frontend to regenerate `$checkapi` path types. The
outbox op `request.path` templates must match the generated openFetch path
templates exactly.

---

## 3. Backend implementation

### 3.1 CRUD methods

Add to `CheckListItemStateCRUD`
([`db/checklist_item_state.py`](../../CheckCheck/backend/checkcheckserver/db/checklist_item_state.py))
and/or `CheckListItemCRUD`
([`db/checklist_item.py`](../../CheckCheck/backend/checkcheckserver/db/checklist_item.py)):

```python
# uncheck-all — MUST mutate ORM objects (see §1.2)
async def uncheck_all_items(self, checklist_id) -> int:
    # SELECT the state rows of LIVE items in this checklist that are checked.
    #   join CheckListItemState -> CheckListItem
    #   where CheckListItem.checklist_id == checklist_id
    #     and CheckListItem.deleted_at IS NULL
    #     and CheckListItemState.checked == True
    # For each row: row.checked = False; session.add(row)
    # commit once. Each dirtied row -> before_update -> fresh server_seq.
    # Return the number of rows flipped.
```

```python
# delete-checked — MUST soft-delete ORM objects (see §1.2)
async def delete_checked_items(self, checklist_id) -> int:
    # SELECT LIVE CheckListItem rows in this checklist whose state.checked is True.
    # For each: soft_delete (sets deleted_at -> before_update -> server_seq).
    #   Reuse the existing soft_delete on the base CRUD; do NOT hard-delete
    #   children in the same session (cascade crash — see docs/development.md
    #   "Tombstone the parent only" and the [tombstone-cascade-gotcha] memory).
    # Return the number tombstoned.
```

Notes:
- **Do not** use `session.execute(update(...))` / `delete(...)` bulk statements.
- Batch the commit (single `await self.session.commit()` after the loop) so it is
  one transaction and the `server_seq` allocator lock is held once. Be aware the
  allocator serialises the commit tail and can deadlock under concurrency into a
  retryable 5xx **by design** (see `_base_model.py:177` and
  [`development.md`](../development.md) gotcha) — the outbox replays it; don't "fix" it.
- Consider a sensible cap / streaming if a card could have very many items, but at
  current scale a straight loop is acceptable.

### 3.2 Routes

Add the two routes. Mirror the existing state/delete routes for the permission
`Security(...)` dependency, the `verify_...`/access wiring, and the
`SyncNotifiationCRUD` injection. Skeleton:

```python
@router.post("/checklist/{checklist_id}/items/uncheck-all", response_model=...)
async def uncheck_all(
    checklist_id: uuid.UUID,
    checklist_access = Security(require_checklist_permission(ChecklistAccessLevel.check)),
    state_crud: CheckListItemStateCRUD = Depends(CheckListItemStateCRUD.get_crud),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
):
    await state_crud.uncheck_all_items(checklist_id=checklist_access.checklist.id)
    await sync_crud.create(SyncNotification(
        cl_id=checklist_access.checklist.id, cli_id=None, upd_prop="item_state"))
    return <fresh counts>
```

```python
@router.post("/checklist/{checklist_id}/items/delete-checked", response_model=...)
# Security(require_checklist_permission(ChecklistAccessLevel.edit))
# ... delete_checked_items(...), SyncNotification(upd_prop="item_deleted", cli_id=None)
```

Register in [`api/routers_map.py`](../../CheckCheck/backend/checkcheckserver/api/routers_map.py)
(they can live on the already-registered item / item-state routers; no new router
needed).

---

## 4. Frontend implementation

### 4.1 Outbox op modelling (the subtle part)

Add **two new op kinds** to `OutboxOpKind`
([`outbox.ts:27`](../../CheckCheck/frontend/utils/outbox.ts)):

```ts
export type OutboxOpKind =
  | "create" | "update" | "delete" | "state" | "position"
  | "bulk_uncheck" | "bulk_delete_checked";   // NEW
```

Model each bulk op as:

```ts
{
  entityType: "item",              // reuse "item" so isChecklistChild /
                                   // pendingChecklistIds / partitionResync work
                                   // via pathParams / entityId with NO edits.
  entityId: checkListId,           // the card id — so partitionResync keeps the op
                                   // while the card exists.
  kind: "bulk_uncheck" | "bulk_delete_checked",
  request: {
    method: "post",
    path: "/api/checklist/{checklist_id}/items/uncheck-all",   // or /delete-checked
    pathParams: { checklist_id: checkListId },
  },
}
```

**Why this shape (verify each against `outbox.ts`):**

- **`coalesce`:** the new kinds are **not** `delete`/`create` and **not** in
  `COALESCABLE`, so they fall through to the final `return [...queue, incoming]` —
  **append-only, never merged, order preserved.** This is *required for
  correctness*: `bulk_uncheck` and `bulk_delete_checked` are **distinct,
  order-dependent operations**, not LWW edits to one field. If they coalesced
  across kinds (e.g. a `delete` superseding a queued `bulk_uncheck`), "untick all
  then delete checked" would diverge between client and server. **Do not add these
  kinds to `COALESCABLE`, and do not reuse the `delete` kind.**
- **`isChecklistChild` / `pendingChecklistIds`:** both read `pathParams.checklist_id`
  for `entityType==="item"`, so the per-card pending badge lights up and a
  card-create cancel correctly drops a queued bulk op. **No change needed.**
- **`partitionResync`:** `item` branch keeps the op iff the parent card exists and
  (`kind==="create"` or the entityId exists). With `entityId = checkListId` and the
  card surviving, both hold → kept. **No change needed.**
- **`outboxFieldGuard`:** for these kinds `itemOpFields` returns `[]` (default
  branch) → the guard protects nothing for a queued bulk op. This means a
  *concurrent* remote delta arriving in the small window before the bulk op drains
  could transiently revert the optimistic state; it self-heals on the next pull.
  **Accepted** (see §6). If you want to harden it later, that requires teaching the
  guard about per-checklist protection (it is currently keyed by item id, and a
  bulk op has no item-id list) — out of scope for v1.
- **`affectsSidebarCounts`:** returns `false` for these (they move *item* counts,
  not card badges). Correct — item counts reconcile via the delta pull's
  preview-count refresh (§1.4).

Add builders to
[`utils/outboxOps.ts`](../../CheckCheck/frontend/utils/outboxOps.ts):

```ts
const ITEMS_UNCHECK_ALL_PATH   = "/api/checklist/{checklist_id}/items/uncheck-all";
const ITEMS_DELETE_CHECKED_PATH = "/api/checklist/{checklist_id}/items/delete-checked";

export function itemsUncheckAllOp(checkListId: string): OutboxOpInput { /* as above */ }
export function itemsDeleteCheckedOp(checkListId: string): OutboxOpInput { /* as above */ }
```

### 4.2 Store actions

Add to `useCheckListsItemStore`
([`stores/checklist_item.ts`](../../CheckCheck/frontend/stores/checklist_item.ts)),
following the fork pattern used by every other action
(`isLocalFirstEnabled()` → optimistic `_local...`; else legacy `$checkapi`).

```ts
async uncheckAllItems(checkListId: string) {
  if (isLocalFirstEnabled()) return this._localUncheckAll(checkListId);
  await $checkapi(".../items/uncheck-all", { method: "post", path: {...} });
  await this.fetchMultipleChecklistsItemsPreview([checkListId], null, true); // reconcile
}

async deleteCheckedItems(checkListId: string) {
  if (isLocalFirstEnabled()) return this._localDeleteChecked(checkListId);
  await $checkapi(".../items/delete-checked", { method: "post", path: {...} });
  await this.fetchMultipleChecklistsItemsPreview([checkListId], null, true);
}
```

Optimistic locals — update the **loaded** items and set counts directly, then
enqueue the single op. The delta pull + `schedulePreviewCountsRefresh` reconciles
to server truth afterward (§1.4):

```ts
_localUncheckAll(checkListId) {
  const list = this.checkListsItems[checkListId] ?? [];
  for (const it of list) it.state = { ...it.state, checked: false,
                                       updated_at: new Date().toISOString() };
  // Counts: everything unchecked. total unchanged.
  const total = this.total_backend_count_per_checklist[checkListId] ?? list.length;
  this.total_backend_count_checked_per_checklist[checkListId] = 0;
  this.total_backend_count_unchecked_per_checklist[checkListId] = total;
  useOutbox().enqueue(itemsUncheckAllOp(checkListId));
}

_localDeleteChecked(checkListId) {
  const list = this.checkListsItems[checkListId] ?? [];
  this.checkListsItems[checkListId] = list.filter((it) => !it.state.checked);
  const checked = this.total_backend_count_checked_per_checklist[checkListId] ?? 0;
  const total = this.total_backend_count_per_checklist[checkListId] ?? 0;
  this.total_backend_count_per_checklist[checkListId] = Math.max(0, total - checked);
  this.total_backend_count_checked_per_checklist[checkListId] = 0;
  // unchecked count unchanged.
  useOutbox().enqueue(itemsDeleteCheckedOp(checkListId));
}
```

Caveats to handle:
- For a **fully-loaded** card these optimistic updates are exact (all items
  present). For a **preview** card they update only the loaded slice but set the
  count maps directly — the authoritative refresh converges them.
- If `checklistWasFullLoadedOnce[checkListId]` is true, `getItemCount` derives from
  the array, which the mutations above keep correct.
- Re-sort not needed (untick doesn't move items unless "separate checked" grouping
  is on — but the collection component re-derives grouping from `state.checked`
  reactively; verify `CheckListItemCollection/Seperated.vue` recomputes).

### 4.3 Kebab menu entries

Edit
[`components/CheckListFooter/Button/MoreOptionsMenu.vue`](../../CheckCheck/frontend/components/CheckListFooter/Button/MoreOptionsMenu.vue).
Append two **action** items (the existing two are `type: "checkbox"`; these are
default click items) to the `items` computed:

```ts
// gate with usePermissions().can(card, "check"/"edit") and hide/disable when
// there are no checked items (checkedCount === 0).
{
  label: "Untick all items",
  icon: "i-lucide-square",              // or i-lucide-list-x
  disabled: !canCheck.value || checkedCount.value === 0,
  onSelect() { checkListItemStore.uncheckAllItems(props.checkListId); },
},
{
  label: "Delete ticked items",
  icon: "i-lucide-trash",
  disabled: !canEdit.value || checkedCount.value === 0,
  onSelect() { openDeleteCheckedConfirm(); },   // destructive -> confirm first
},
```

- Get `checkedCount` from `useCheckListsItemStore().getItemCount(checkListId, true)`
  as a `computed` (see `Seperated.vue:70`).
- Get `canCheck`/`canEdit` from `usePermissions().can(...)` (see
  `CheckListItem.vue:195`). The card is `checkListsStore.get(checkListId)`.
- **Delete ticked items is destructive → confirm dialog.** Reuse the `UModal`
  pattern from
  [`components/CheckListFooter/Button/Archive.vue`](../../CheckCheck/frontend/components/CheckListFooter/Button/Archive.vue)
  ("Delete forever?"): a `v-model:open` modal with Cancel / destructive confirm.
  Untick-all is non-destructive → no confirm needed.
- **Add `data-testid`s** (load-bearing for E2E — see `development.md`): e.g.
  `card-untick-all`, `card-delete-ticked`, `confirm-delete-ticked`.

The menu renders from `CheckListFooter/Toolbar.vue` on every card, so it is
available both on the board (preview) and in the opened card.

---

## 5. Testing

### 5.1 Backend (`CheckCheck/backend/tests/`)

Authoritative run is Postgres (`./run_backend_tests_with_postgres.sh`); SQLite for
quick iteration. Add cases (near `tests_changes.py` and the item-state tests):

- `uncheck-all` flips all checked items to unchecked; unchecked items untouched.
- `delete-checked` tombstones exactly the checked items; unchecked survive;
  already-tombstoned items are not re-processed (idempotent replay returns success).
- **`server_seq` actually advanced** — the critical regression guard for §1.2:
  after each bulk op, `GET /api/changes?since=<before>` returns the affected items
  (uncheck → in `items` with `state.checked=false`; delete → ids in
  `item_tombstones`). This test fails loudly if someone "optimises" the CRUD into a
  Core bulk `UPDATE`/`DELETE`.
- Permission enforcement: `check` required for uncheck-all (a `view` collaborator
  gets 403), `edit` for delete-checked (a `check` collaborator gets 403).
- One `changes_available` poke emitted (not N).

### 5.2 Frontend unit (`CheckCheck/frontend/tests/unit/`, vitest)

- `outboxOps.spec.ts`: the two new builders produce the expected op shape
  (entityType `item`, entityId = checkListId, correct path/kind).
- `outbox.spec.ts`: **coalesce is append-only** for the new kinds — enqueue
  `bulk_uncheck` then `bulk_delete_checked` on the same card → **two** ops, order
  preserved (guards the order-dependence bug in §4.1). Also: a queued
  `bulk_delete_checked` does **not** cancel a queued `bulk_uncheck`.
- `outbox.spec.ts` / resync: `partitionResync` keeps a bulk op while its card
  exists, drops it when the card is gone.
- `pendingChecklistIds` includes the card of a queued bulk op (badge).

### 5.3 E2E (`CheckCheck/frontend/tests/e2e/`, Playwright)

See [`testing/E2E_TESTING.md`](../testing/E2E_TESTING.md) and the selector guide
`tests/e2e/LLM_GUIDE.md`; run via the wrapper scripts (see the
[`frontend-e2e-playwright-cli`] memory — use the local CLI via bun).

- Untick-all: check several items, open kebab → "Untick all items", assert all
  render unchecked and the checked count is 0.
- Delete-ticked: check several, kebab → "Delete ticked items" → confirm, assert
  only the unchecked remain.
- **Offline** variant (there is an existing offline-sync suite from WI-15): go
  offline, perform each bulk op, assert optimistic result; go online, assert it
  drained (one request) and the server state matches. Reuse the offline harness.
- Note the known DnD/sharing/counts flake caveat (re-run before blaming your
  change — [`flaky-e2e-dnd-sharing`] memory).

---

## 6. Edge cases, tradeoffs & accepted limitations

- **"Delete checked" = "delete whatever is checked at replay time."** With the
  single actor draining sequentially this matches intent (their own later
  checks/unchecks are ordered relative to the bulk op via `seq`). The only anomaly:
  a **collaborator** checks an item in the window between the user clicking and the
  op draining → that item is also deleted. This is consistent with the button's
  plain meaning ("delete all ticked") and is accepted for v1. *Refinement option if
  ever needed:* accept an optional `{ item_ids: [...] }` body and send explicit ids
  when the card is fully loaded (still one op / one request), falling back to
  server-side "all checked" for preview cards. Not recommended for v1 (adds
  complexity; large bodies).
- **Transient flap under concurrent edit** (§4.1, `outboxFieldGuard` protects
  nothing for bulk ops): a remote delta landing before the bulk op drains can
  briefly show pre-op state; the next pull converges. Accepted.
- **Count convergence** relies on the drain → poke → delta pull →
  `schedulePreviewCountsRefresh` chain (§1.4). If you ever change that chain, re-check
  bulk-op counts on preview cards.
- **Empty operation:** if nothing is checked, the endpoints are no-ops (0 rows,
  still fine to emit a poke or skip it). The menu already disables the entries when
  `checkedCount === 0`, so this is mostly defensive.
- **"Separate checked items" grouping** (`checked_items_seperated`): after
  untick-all, the "checked" group empties; after delete-checked likewise. Verify
  the collection components recompute grouping reactively from `state.checked`.

---

## 7. File-by-file checklist

**Backend**
- [ ] `db/checklist_item_state.py` — `uncheck_all_items` (ORM-object mutation).
- [ ] `db/checklist_item.py` — `delete_checked_items` (ORM soft_delete loop).
- [ ] `api/routes/routes_checklist_item_state.py` (or `_item.py`) — two routes with
      `check`/`edit` `Security`, single `SyncNotification` each.
- [ ] `api/routers_map.py` — confirm routes are on a registered router / no path
      conflict.
- [ ] `tests/` — CRUD + route + `server_seq`-advanced + permission tests.
- [ ] Regenerate `CheckCheck/openapi.json` ([`openapi-regen`] memory).

**Frontend**
- [ ] `utils/outbox.ts` — add `bulk_uncheck` / `bulk_delete_checked` to
      `OutboxOpKind`. (No coalesce/guard/resync edits needed — verify each.)
- [ ] `utils/outboxOps.ts` — `itemsUncheckAllOp` / `itemsDeleteCheckedOp`.
- [ ] `stores/checklist_item.ts` — `uncheckAllItems` / `deleteCheckedItems` +
      `_localUncheckAll` / `_localDeleteChecked`.
- [ ] `components/CheckListFooter/Button/MoreOptionsMenu.vue` — two menu entries,
      permission + checked-count gating, confirm modal for delete, `data-testid`s.
- [ ] `bun run postinstall` — regenerate `$checkapi` types after openapi update.
- [ ] `tests/unit/` — outboxOps + coalesce/resync/pending specs.
- [ ] `tests/e2e/` — online + offline specs.

**Docs**
- [ ] `CHANGELOG.md` — new feature line.
- [ ] `docs/SYNC_PROTOCOL.md` — optional: note the two bulk endpoints as
      order-dependent, append-only outbox ops (if you want the contract to mention
      them; not strictly required since they replay through the same mechanism).
- [ ] This plan → mark done / move to `docs/archive/` when shipped.

---

## 8. Open questions for the implementer to decide

1. **Response body shape** — return fresh counts (recommended) vs `204 No Content`.
   Counts make the online path and tests simpler.
2. **Legacy (flag-off) SSE `upd_prop`** — confirm `item_state` / `item_deleted`
   with `cli_id=null` trigger a card-items refetch in `useSync.ts`; if not, add a
   handler or a new `upd_prop` literal. (Low priority — local-first is default.)
3. **Icon choice / menu placement** — cosmetic; keep the destructive one visually
   distinct (color) and consider a divider above it.
4. **Cap on item count per op** — probably unnecessary at current scale; decide if a
   guardrail is wanted.

---

### Memory pointers (for the next session's recall)

Related memories worth reading: `[[wi8-item-optimistic]]`, `[[wi9-reorder-checklist-store]]`,
`[[wi10-delta-application]]`, `[[wi7-outbox]]`, `[[chunk-b-sync-engine]]`,
`[[tombstone-cascade-gotcha]]`, `[[timestampedmodel-onupdate-gotcha]]`,
`[[openapi-regen]]`, `[[db-targets]]`.
