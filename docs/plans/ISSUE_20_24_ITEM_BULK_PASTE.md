# Issues #20 and #24: more item bulk actions, add items from text

Branch `feat/issue-20-24-item-bulk-paste`. Builds on the bulk item operations design
(`docs/archive/BULK_ITEM_OPERATIONS.md`): dedicated server endpoints, one append-only
outbox op each, ORM-object mutation so `server_seq` advances, one `cli_id=null` poke.

## Chunk 1: #20 bulk actions (backend + frontend)

Status: done 2026-11-14. Backend `tests_bulk_item_ops.py` (+10) and `tests_item_text.py`,
vitest `itemBulk.spec.ts` + `outboxOps.spec.ts` additions, E2E `item-bulk-actions.spec.ts`
(online, clipboard, offline drain of three queued bulk ops). Menu logic lives in
`composables/useItemBulkActions.ts` so `MoreOptionsMenu.vue` only wires entries.

New card kebab entries next to "Uncheck all items" / "Delete checked items":

| Action | Endpoint | Permission | Outbox kind | Poke `upd_prop` |
|---|---|---|---|---|
| Check all items | `POST /api/checklist/{id}/items/check-all` | check | `bulk_check` | `item_state` |
| Sort items by name | `POST /api/checklist/{id}/items/sort-by-name` | edit | `bulk_sort_by_name` | `item_position` |
| Remove duplicates | `POST /api/checklist/{id}/items/remove-duplicates` | edit | `bulk_remove_duplicates` | `item_deleted` |
| Copy list as text | none (client only) | view | none | none |

Rules (mirrored exactly in `checkcheckserver/item_text.py` and `frontend/utils/itemBulk.ts`,
so the optimistic result on a fully loaded card equals the server result):

* **Sort key:** NFKD, drop combining marks (so "Äpfel" sorts with "apfel"), collapse
  whitespace, lowercase, then natural order (ASCII digit runs compare as numbers, so
  "Item 2" before "Item 10"). Empty items go last. Ties: raw text, then current
  position, then id. New indices are `rank + 1` (1, 2, 3, ...); only changed rows are
  written.
* **Duplicates:** same rule as item suggestions (`normalizeItemText`: trim, collapse
  whitespace, lowercase). Per group the topmost unchecked copy stays, else the topmost
  copy. Empty items are never treated as duplicates.
* **Copy as text:** Markdown checklist `- [ ] text` / `- [x] text` in display order
  (unchecked first when "Separate checked items" is on). Items only, no title, so the
  output round-trips through "Add items from text" (#24). Newlines inside an item are
  flattened to spaces. A preview-only card is fully loaded first when online and no
  op is pending for it; offline it copies what is cached and says so.

Replay semantics like the existing bulk ops: "operate on what the server has now".

## Chunk 2: #24 add items from text

Status: done 2026-11-14.

UX decided by Tim: no automatic split on paste (pasting stays as today), no undo
toast, no Ctrl+Shift+V handling. Instead a kebab entry "Add items from text" opens
`components/CheckListFooter/AddItemsFromTextModal.vue`: a multi-line textarea, a hint
about the rules, a live count ("4 items will be added, 1 of them checked."), Cancel
and "Add N items" (Ctrl/Cmd+Enter also confirms). Below `sm` the dialog is full
screen pinned to the visual viewport like the card editor, so the buttons stay above
the keyboard. Confirm calls the store's `createMany`: one normal optimistic create per
item, appended in order where new items go (server: after the last index; local:
`nextItemIndex`).

Parser `utils/parseItemsFromText.ts` (tests in `parseItemsFromText.spec.ts`): one item
per non-empty line; strips one bullet (`-`, `*`, `+`, `•`, `1.`, `1)`, only when
followed by whitespace, so "-5 degrees" and "1.5 kg" stay) and then one task box;
`[x]` / `[X]` creates a checked item. Round-trips `itemsToMarkdown`, including item
texts that themselves start with a marker.

Why one create op per item and not a batched endpoint: the client holds every new
item, each create is idempotent by client id, and per-item ops keep all outbox rules
(delete cancels create, resync, pending badge, own-write echo) working with no new
op kind. A typical paste is a handful to a few dozen items.

Out of scope: an "auto-split on paste" setting (Tim sees it as a possible later
follow-up).

## Decisions taken by default

* Sort permission is `edit` (same as every position route); check-all is `check`.
* Empty items are skipped by "Remove duplicates" and by "Copy list as text"; sort puts
  them last.
* "Sort items by name" asks no confirmation (the issue asks only for one on remove
  duplicates).
* "Copy list as text" copies items only, without the card title, so the output reads
  back through "Add items from text" unchanged.
* "Add items from text" needs `edit` (same as creating an item) and adds nothing for
  lines that are empty after removing markers.

