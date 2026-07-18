/**
 * Normalize checklist-item text for duplicate matching (Keep-style "suggest
 * unchecking an existing item instead of creating a duplicate").
 *
 * Case-insensitive, whitespace-insensitive: trim ends, lowercase, and collapse
 * any run of internal whitespace to a single space. So "  Milk " and "milk"
 * and "MILK" all normalize to "milk".
 */
export function normalizeItemText(text: string | null | undefined): string {
  return (text ?? "").trim().replace(/\s+/g, " ").toLowerCase();
}

/** Minimal shape needed to match — keeps the helper decoupled from the store type. */
interface MatchableItem {
  id: string;
  text: string;
}

/**
 * Autocomplete-style suggestions: the checked items whose normalized text
 * *starts with* what the user has typed so far, so the list narrows live as
 * they type (Keep's "search suggestions" feel) rather than only on an exact
 * match. Excludes the item being typed into (`currentItemId`), ranks an exact
 * match first, then preserves the incoming order, and caps the result at
 * `limit`. Returns [] when the typed text is empty.
 *
 * `checkedItems` is expected to be pre-filtered to checked items only (as
 * returned by the item store's `getCheckListItems(id, true)`).
 */
export function findMatchingCheckedItems<T extends MatchableItem>(
  checkedItems: readonly T[],
  currentItemId: string | undefined,
  typedText: string | null | undefined,
  limit = 5
): T[] {
  const query = normalizeItemText(typedText);
  if (!query) return [];
  const matches = checkedItems.filter(
    (it) => it.id !== currentItemId && normalizeItemText(it.text).startsWith(query)
  );
  // Exact matches first (the "you're about to duplicate this" case), otherwise
  // keep the list's existing order.
  matches.sort((a, b) => {
    const aExact = normalizeItemText(a.text) === query ? 0 : 1;
    const bExact = normalizeItemText(b.text) === query ? 0 : 1;
    return aExact - bExact;
  });
  return matches.slice(0, limit);
}
