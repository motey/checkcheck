# Plan: readable notes, slimmer note-only cards

**Status:** done (all chunks landed; on-device check open). Implements
[issue #22](https://github.com/motey/checkcheck/issues/22). Frontend only: no
backend, schema, sync or outbox change.

A "note-only card" is a card whose total item count is 0 (checked and
unchecked). Whether it has notes text does not matter for the slimming; it only
matters for the longer note clamp (no text, nothing to clamp). While a board
card's item preview has not loaded yet (no `checklistWasFullLoadedOnce` entry)
the count counts as unknown and the card keeps the checklist layout, so cards
with items do not flash as note cards on first paint.

Trap found on the way: the preview batch `GET /api/item?checklist_ids=...`
leaves a card with no items out of its response map entirely, so such a card
never got a count entry. `fetchMultipleChecklistsItemsPreview` now records
"no items" for every requested card the response left out (unless the card is
fully loaded, whose own list is the truth). Unit test:
`tests/unit/itemPreviewEmptyCards.spec.ts`.

## Chunks

1. **Shared helpers + preview** (`utils/cardLayout.ts`, `CheckList.vue`,
   `assets/css/main.css`)
   - Board preview keeps `<br>` line breaks for all cards (the `.md-clamp br`
     hide rule goes).
   - Note-only cards get a longer clamp (`.md-clamp-long`): 12 lines on phones,
     20 on desktop, instead of 2 and 3.
   - Note-only cards drop the empty item block and the `sm:min-h-32` floor.
2. **No "0 checked items"** (`CardParts/ItemsSection.vue`): the separator and
   collapse header only render when there is at least one checked item. Shared
   with the `/p/<token>` viewer, so both surfaces get it.
3. **Public viewer** (`pages/p/[token].vue`): a view-only link to a card with
   notes and no items no longer shows the "no items yet" filler or an empty
   body; the notes are the card.
4. **Mobile reminders** (`CheckListReminders.vue`): below `sm` the section
   collapses to a bell button with a count badge; tapping it (or the kebab's
   "Set a reminder") expands the full section. Desktop unchanged.
5. **Tests + changelog**: vitest for `utils/cardLayout.ts` and the Markdown
   line breaks, E2E for desktop (`note-cards.spec.ts`) and phone
   (`touch-note-cards.spec.ts`), CHANGELOG entry.

## Decisions taken by default (confirm with Tim)

- Long clamp numbers: 12 lines on phones, 20 on desktop (Tim raised them
  from the first draft's 8 and 12).
- The bell replaces the block on phones only; desktop keeps the full section
  (the issue scopes the bell to mobile).
- The bell expands the section inline in the editor (no popover, no second
  dialog: stacking dialogs is a known trap here). It has a "Hide" control to fold
  it back.
- Badge counts pending reminders of the caller on this card (what the section
  lists); above 9 it reads "9+".
- In the editor, a card with edit access keeps the "Add new item" row even when
  it has no items: it is the only way to add the first item.
- Paragraphs in the clamped preview get a small gap so a blank line in the
  source stays visible.
- The bell stays where the block was (bottom of the open card's scroll region),
  rather than moving into the header bar next to the pin.
- A view-only public link to a card with neither notes nor items keeps the
  "This list has no items yet." line, so the card is not blank.

## Settled with Tim

- On desktop every board card keeps room at its bottom for the hover toolbar
  (invisible until hover, Keep's model), also a short note-only card. Tim: keep
  it as is.
- The reminder bell stays phone-only.

## Left alone
- The documentation screenshots (`gen_screenshots.sh`) were not regenerated;
  shots that showed "0 checked items" or the phone reminders block will differ.

## Progress

- [x] 1 Shared helpers + preview (plus the preview-batch count fix)
- [x] 2 No "0 checked items"
- [x] 3 Public viewer
- [x] 4 Mobile reminders
- [x] 5 Tests + changelog
- [ ] On-device check on a real phone (Tim)
