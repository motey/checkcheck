# Session Status — 2026-05-31

## What was done this session

### Backend
- Implemented Postgres LISTEN/NOTIFY sync (vs SQLite polling drain)
- Fixed all asyncio event-loop lifecycle bugs (`asyncio.run()` isolation, `await db_engine.dispose()`)
- Fixed Postgres compatibility: `datetime` naive UTC, `exists_ok` unique violation, `PRAGMA` guard
- Fixed position midpoint formula bugs in both `routes_checklist_item_pos.py` and `routes_checklist_position.py` (was `(a-b)/2 + a` instead of `(a-b)/2 + b`)
- Removed broken SQLite `ON CONFLICT (cl_id, upd_prop)` upsert (caused data loss for same-prop different-item moves)
- Cleaned up sync notification code (renamed internals, removed dead imports)
- Migration 0003: drops the now-removed unique constraint from existing DBs
- Dev scripts: `run_dev_backend_server_with_oidc_on_postgres.sh`, `build_server_dev_env.sh`

### Frontend
- Removed `transferAttrs` anti-pattern from all stores — replaced with `splice`/direct assignment
- `CheckList.vue`: changed `ref()` to `computed()` so the component always tracks the current store object
- Fixed `refresh()` in checklist store to use `splice` (replace) — not `transferAttrs`
- Fixed card/item list deduplication in `fetchNextPage` and `create()`
- Fixed FormKit DnD array-replacement duplication in `CheckListBoard.vue` and `CheckListItemCollection/index.vue` (use `splice` in-place, not ref value replacement)
- Fixed text field wipe: local `ref` + focus guard in `CheckList.vue` and `CheckListItem.vue`
- Fixed `total_backend_count++` race in `useSync.ts` (`checklist_created` case)
- Fixed `checklist_created` SSE: skip redundant GET when creator's tab already has the item
- Fixed `reorderChecklistItems` shallow-copy problem: move functions now look up store item by ID

## The drag-and-drop bug — FIXED and verified on Postgres

### Root cause (confirmed by console logging)
`watchEffect` in `CheckListItemCollection/index.vue` and `CheckListBoard.vue` fires **during an active drag** when:
1. A previous drag's async (`_sort`, `sortBySubset`) completes while the next drag is already started
2. The SSE `item_position` debounce (400ms) fires while a drag is in progress
3. Any other store update arrives mid-drag

The `watchEffect` calls `checklistItems.value.splice(0, len, ...storeOrder)` which resets FormKit DnD's internal list to the store order. When the user releases, `event.values` reports the **original order** (not the drag destination). Every drag appears to be a no-op.

### Fix applied (needs testing)

**`CheckListItemCollection/index.vue`**:
```javascript
let dragInProgress = false;
// in useDragAndDrop:
onDragstart: () => { dragInProgress = true; },
onDragend: (event) => { dragInProgress = false; ... },
// in watchEffect:
if (dragInProgress) return;  // ← skip splice while dragging
```

**`CheckListBoard.vue`**: same pattern with `checklistDragInProgress`.

### Verified on Postgres
- Item reorder within checklist ✓
- Card reorder on board ✓
- SQLite migration fix: 0003 `try/except` now wraps the entire `with batch_alter_table` block so the ValueError from `flush()` is caught correctly

### Remaining uncertainties
- The `sortBySubset` call in `reorderChecklistItems` may be unnecessary now (store is already sorted by `_sort`). Could simplify but not critical.
- Position indices will converge toward 0 over time (midpoint shrinks). A backend "reindex" endpoint would eventually be needed, but not urgent.

## Debug server
See `memory/debug_server.md` for how to start the isolated debug server on port 8282.

## Files changed this session (not exhaustive)
- `CheckCheck/backend/checkcheckserver/api/routes/routes_sync_notification.py`
- `CheckCheck/backend/checkcheckserver/api/routes/routes_checklist_item_pos.py` (formula fix)
- `CheckCheck/backend/checkcheckserver/api/routes/routes_checklist_position.py` (formula fix)
- `CheckCheck/backend/checkcheckserver/db/sync_notification.py`
- `CheckCheck/backend/checkcheckserver/db/_init_db.py`
- `CheckCheck/backend/checkcheckserver/db/_base_crud.py`
- `CheckCheck/backend/checkcheckserver/model/sync_notifications.py`
- `CheckCheck/backend/checkcheckserver/model/_base_model.py`
- `CheckCheck/backend/checkcheckserver/config.py`
- `CheckCheck/backend/migrations/versions/0002_add_unique_constraint_to_sync_notification.py`
- `CheckCheck/backend/migrations/versions/0003_drop_sync_notification_unique_constraint.py`
- `CheckCheck/frontend/stores/checklist.ts`
- `CheckCheck/frontend/stores/checklist_item.ts`
- `CheckCheck/frontend/stores/label.ts`
- `CheckCheck/frontend/components/CheckList.vue`
- `CheckCheck/frontend/components/CheckListItem.vue`
- `CheckCheck/frontend/components/CheckListBoard.vue`
- `CheckCheck/frontend/components/CheckListItemCollection/index.vue`
- `CheckCheck/frontend/composables/useSync.ts`
- `run_dev_backend_server_with_oidc_on_postgres.sh`
- `build_server_dev_env.sh`
