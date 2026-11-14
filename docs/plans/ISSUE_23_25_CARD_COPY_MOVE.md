# Issues #23 and #25: duplicate a card, move checked items to a new card

Branch: `feat/issue-23-25-card-copy-move`

## Settled (Tim)

- **Duplicate (#23):** new card, title + " (copy)", note, all items (unchecked,
  same order), color and the actor's labels. Not copied: shares, public links,
  reminders.
- **Move checked (#25):** new card, title + " (done)", the checked items keep
  order and checked state and leave the original card. Confirmation dialog first.
  Color and labels carried over like #23.
- One server endpoint each, one request and one transaction, shared copy logic.
  Both are card actions in the kebab menu.

## Design

### API

- `POST /api/checklist/{checklist_id}/duplicate` (needs `view`)
- `POST /api/checklist/{checklist_id}/items/move-checked` (needs `edit`)

Both take an optional body `CheckListCopyRequest`:

- `id`: client id of the new card. Makes the call idempotent: a replay finds the
  card and returns it unchanged (no second copy, and for move no second move of
  items checked since). Owned by someone else: 409. Tombstoned: 410.
- `position.index`: where the new card sits on the caller's board (the client
  sends the slot it already shows, like the plain create). Default: top.
- `item_ids`: map source item id to the client id for its copy. The client only
  holds a preview of a card, so it maps the items it has loaded; the server uses
  those ids and generates the rest. This is what lets the optimistic offline card
  and the server's card share item ids, so the delta pull updates the rows in
  place instead of duplicating them. Unknown keys are ignored; a value that is
  already an item id is a 409; duplicate values are a 422.

Response: the new card (`CheckListApiWithSubObj`).

Copy logic lives in `api/checklist_copy.py` (`copy_checklist`), used by both
routes. It stages the card, the caller's position, the items (row, position,
state), and the caller's label links, then (move only) tombstones the source
items, and commits once. ORM objects throughout, so every row gets a
`server_seq` (no Core bulk statements).

Move is "copy, then tombstone the originals", not "re-parent the item rows". A
re-parented row would never reach the source card's other devices and
collaborators as a removal (the delta feed reports item removals only as
tombstones), so they would keep showing the moved items.

### Sharing rule

- The new card is always owned by the acting user and shared with nobody.
- Duplicate needs only `view`: anyone who can see a card can use it as a
  template for their own board.
- Move needs `edit`, the same as "Delete checked items". A collaborator with
  edit rights who moves items takes them out of the owner's card into a card of
  their own. That is no more than "Delete checked items" already allows them, and
  they keep a copy.
- Labels are per user, so only the actor's own labels on the source card are
  carried over.

### Sync

- The new card's position row is a fresh grant (`granted_seq`), so the actor's
  other devices get the whole card with all items in the next delta pull.
- Move tombstones the source items, so every device of every collaborator of the
  source drops them through `item_tombstones`.
- SSE: `checklist_created` for the new card (reaches only the actor), and for
  move an `item_deleted` with `cli_id=null` on the source (the legacy client's
  card-level refetch, like delete-checked).

### Offline (local-first)

Works offline, one outbox op each, following the bulk-op pattern.

- New op kinds `duplicate` and `move_checked`, `entityType: "checklist"`,
  `entityId` = the NEW card id, `pathParams.checklist_id` = the source.
- Engine rules for the new kinds:
  - They count as creates of the new card for `queuedCreateIds` (so `known=`
    leaves the optimistic card out until the op drains, no flap) and for
    `partitionResync` (an op on the new card survives while the op is queued).
  - `duplicate` is cancelled like a create when the new card is deleted before
    it drained (nothing ever reached the server).
  - `move_checked` is NOT cancelled by a later delete of the new card: the items
    already left the source locally, so the move must still happen and the
    delete then removes the new card. A delete of the new card keeps the queued
    move.
  - After a `full_resync` they survive only while the source card still exists.
  - They move sidebar counts (a new card), so the idle refetch fires.
  - The per-card pending badge covers both the new card and the source.
- Optimistic local state: the new card is inserted with copied fields and the
  loaded items (mapped ids), counts taken from the source counts. For a source
  that was fully loaded the copy is exact and marked fully loaded; otherwise it
  is a preview and the delta pull plus the preview count refresh converge it.
- A dropped op (403/404/410) removes the new card through the normal drop path
  (delta pull, `removed_checklist_ids`); for move the source card's items are
  re-read so the optimistically removed items come back.

## Progress

- [x] Backend: copy logic, both routes, `tests_checklist_copy.py` (15 tests,
  sqlite + postgres)
- [x] OpenAPI + frontend types
- [x] Frontend: outbox kinds + builders (`utils/outbox.ts`, `utils/outboxOps.ts`),
  optimistic plan (`utils/cardCopy.ts`), store actions (`duplicate`,
  `moveCheckedToNewCard`), kebab entries (`composables/useCardCopyActions.ts`,
  `MoveCheckedConfirm.vue`), drop handling in `useSyncNotices`, unit tests
  (`cardCopy.spec.ts`, `cardCopyStore.spec.ts`)
- [x] E2E: `card-copy.spec.ts` (duplicate, move with confirm, both offline)
- [x] CHANGELOG

## Notes

- The menu entries sit in their own group between the view toggles and the
  reminder entry, so the item bulk actions further down stay untouched.
- Toast icons used from a `.ts` file must be listed in `nuxt.config.ts`
  (`icon.clientBundle.icons`), the scan only covers components.
- Opening a preview-only copy before its op drained would run the full item GET
  for a card the server does not know yet (a 404 with an error toast).
  `CheckList.vue` now skips that GET while the card's create (or copy) op is
  queued; the delta pull after the drain delivers the items.

## Decisions taken by default (for Tim to confirm)

- Duplicate needs only `view` (template use by share recipients).
- The new card goes to the top of the board (like a new card), unpinned and
  unarchived, even when the source is pinned or archived.
- The card settings (separate checked items, collapsed, suggestions) are copied.
- Empty title: the copy is called "(copy)" / "(done)".
- Move with nothing checked: the menu entry is disabled; the endpoint still
  creates an empty card (server is "move whatever is checked now", and a replay
  must not fail).
