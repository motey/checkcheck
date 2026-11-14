// Layout decisions for note-only cards (issue #22), kept as pure functions so the
// unit suite can pin them without mounting a component. The card surfaces
// (CheckList.vue, CardParts/ItemsSection.vue, CheckListReminders.vue and the
// `/p/<token>` viewer) read their answers from here.

/**
 * A card with no items at all, checked or unchecked. Such a card is about its
 * notes: it gets no empty item block, no minimum height and a longer note
 * preview on the board.
 *
 * `null`/`undefined` means "not known yet" (the board has not fetched the
 * card's item preview); that keeps the checklist layout, so a card with items
 * does not flash as a note card while its first page loads.
 */
export function isNoteOnlyCard(itemCount: number | null | undefined): boolean {
  if (itemCount === null || itemCount === undefined) return false;
  return itemCount <= 0;
}

/**
 * Classes for the clamped notes block on a board preview. Cards with items keep
 * the short clamp (2 lines on phones, 3 on desktop) so the items stay in view; a
 * note-only card has nothing else to show and gets the long clamp
 * (`.md-clamp-long` in assets/css/main.css).
 */
export function previewNotesClampClass(itemCount: number | null | undefined): string {
  return isNoteOnlyCard(itemCount) ? "md-clamp md-clamp-long" : "md-clamp";
}

/**
 * Whether the "checked items" separator / collapse header renders. With nothing
 * checked it would only say "0 checked items", which is noise, on the board as
 * well as in the open card.
 */
export function showCheckedSection(checkedCount: number | null | undefined): boolean {
  return (checkedCount ?? 0) > 0;
}

/** The count badge on the phone's reminder bell: nothing for 0, capped at "9+". */
export function reminderBadgeText(count: number): string | null {
  if (count <= 0) return null;
  return count > 9 ? "9+" : String(count);
}

/** Accessible name of the phone's reminder bell. */
export function reminderBellLabel(count: number): string {
  if (count <= 0) return "Reminders, none set";
  return count === 1 ? "Reminders, 1 set" : `Reminders, ${count} set`;
}
