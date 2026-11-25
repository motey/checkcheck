# Issues #35 and #34: copies next to their source, animate cards leaving the board

Branch: `feat/issue-34-35-card-placement-animation`

Order: #35 first (one commit), then #34 (one commit).

## #35: copies land directly after their source card

"Duplicate card" and "Move checked items to new card" used to put the new card on
top of the board. Now the copy takes the slot right after the source in reading
order; the cards after it move one slot on.

### Design

- **Index (client):** the board sorts by descending index, pinned first, so "after
  the source" means a lower index in the source's own group (pinned or not). The
  client computes it with the WI-9 helper `fractionalIndexBetween` (decimal.js):
  - a known next card in the group: the midpoint between the two;
  - no next card and the board is fully loaded (or, for a pinned source, the
    normal section has started loading, so every pinned card is here): the usual
    end gap below the source;
  - no next card but more pages may follow: a small step below the source
    (`COPY_INDEX_STEP`), so the copy does not jump past cards that are on the
    server but not loaded yet.
  Pure function `planCopyPlacement` in `utils/cardCopy.ts`, unit tested.
- **Pinned:** a copy of a pinned card is pinned too. The client sends
  `position.pinned` along with `position.index`, so the optimistic card and the
  server land on the same slot even when the pin state changes in between (another
  device). New optional field `pinned` on `CheckListCopyPosition`; omitted, the
  server copies the caller's pin state of the source.
- **Archived source:** the copy is never archived, so it cannot sit next to an
  archived source. It goes to the top of the board, unpinned (the old behaviour).
- Both the local-first and the legacy online path send the placement.
- The #30 spawn animation needs no change: it flies from the source rect to the
  copy's slot, which is now the next one.

### Progress

- [x] Backend: `pinned` on the copy request, inherit from the source, tests
  (`tests_checklist_copy.py`, 3 new, sqlite + postgres)
- [x] OpenAPI + frontend types
- [x] Frontend: `planCopyPlacement`, store wiring (both paths), unit tests
  (`cardCopy.spec.ts`, `cardCopyStore.spec.ts`)
- [x] E2E: copy lands right after the source (normal and pinned, survives a
  reload); the #30 animation test now expects the source to stay put
- [x] CHANGELOG, commit (#35)

## #34: animate cards leaving the board

Own actions only: delete, archive, unarchive in the archive view, leaving a shared
card. Remote changes, paging, filters and search stay instant.

### Design

- Extend `utils/cardSpawn.ts` (rename the concept to "card motion"): next to the
  spawn hint, a leave hint `noteCardLeave(id)` the store sets right before the
  card leaves its list. Hints are kept per card id (two quick archives both
  animate), each with a TTL.
  - archive / unarchive: `archive()` notes the leave when the state changes. The
    board filter then drops the card (home view: archived; archive view: restored).
  - delete: `delete()` / `_localDelete()` note it before the splice.
  - leave a shared card: `revokeShare()` on the caller's own id notes it after the
    server call succeeded. The card is removed by the sync path (delta pull or the
    legacy SSE event), so this hint gets a longer TTL.
- `planCardFlip` learns a `leave` kind: the leaving card's old box, if it was on
  screen. The board keeps a ghost (a static clone of the card's `<li>`, fixed at
  its old box, no pointer events) that fades and shrinks out while the remaining
  cards slide into the gap with the existing `move` animation.
- Reduced motion: no ghost, no slide (instant, like today).
- Only cards on screen before or after the change animate (same rule as #30).

### Progress

- [x] `cardSpawn.ts` renamed to `utils/cardMotion.ts`: per-card hints
  (`noteCardSpawn`, `noteCardLeave`, `pendingCardMotions`, `matchCardMotions`,
  `clearCardMotions`) and `planCardFlip` with a `leave` kind; unit tests in
  `cardMotion.spec.ts`
- [x] Store hooks: `archive()` (when the state changes), `delete()` and
  `_localDelete()`, `revokeShare()` on the caller's own id (sync TTL)
- [x] Board: ghost + slide, reduced motion respected, off-screen cards skipped
- [x] E2E: `archive.spec.ts` "animates a card leaving the board on the user's
  own action only" (reduced motion instant, ghost + slide with motion, a card
  deleted through the API goes without animation)
- [x] CHANGELOG, commit (#34)

## Notes

- The ghost is a `cloneNode` of the card's `<li>`, taken before Vue removes it,
  fixed at the old box and prepended to `<body>` without a z-index: it paints
  below the board's cards (the sliding cards pass over it) and below the navbar.
  It carries `data-card-leaving` (for tests) and none of the card's ids, roles
  or tab stops.
- An undo of an archive (toast) notes a leave hint too, which never matches (the
  card comes back instead) and goes stale after 2 s. The store does not know
  which view the board shows, so it cannot tell the two apart; harmless.
- Leaving a shared card: the removal arrives through the sync, so the hint lives
  10 s. A remote removal of that same card inside that window would animate
  too; it is the card the user just left, so that is fine.
- Checked by eye with slowed-down animations (CDP playback rate) in a throwaway
  spec: the ghost looks like the card and fades in place.
