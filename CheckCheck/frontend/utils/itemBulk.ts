// ── Card-level bulk item helpers (issue #20) ─────────────────────────────────
//
// Pure, framework-free rules behind the kebab-menu bulk actions. "Sort items by
// name" and "Remove duplicates" run on the server (one outbox op each, the server
// holds every item), but the client applies the same rules optimistically, also
// offline. The sort and duplicate rules are therefore exact twins of the backend's
// `checkcheckserver/item_text.py` + `CheckListItemCRUD.sort_items_by_name` /
// `remove_duplicate_items`: on a fully loaded card the local result must equal the
// server's, or a later offline drag would land between the wrong neighbours on
// replay. Change one side only together with the other (the unit test vectors are
// shared verbatim with backend/tests/tests_item_text.py).

import { normalizeItemText } from "@/utils/normalizeItemText";

interface SortableItem {
  id: string;
  text?: string | null;
  position: { index: number };
}

interface StatefulItem extends SortableItem {
  state: { checked: boolean };
}

/** One natural-sort chunk: `[kind, length, value]`, kind 0 = digit run, 1 = text. */
type Chunk = [number, number, string];

const RUNS = /[0-9]+|[^0-9]+/g;

function foldForSort(text: string | null | undefined): string {
  return (text ?? "")
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase();
}

function chunksOf(folded: string): Chunk[] {
  const chunks: Chunk[] = [];
  for (const run of folded.match(RUNS) ?? []) {
    if (run[0]! >= "0" && run[0]! <= "9") {
      const digits = run.replace(/^0+/, "") || "0";
      chunks.push([0, digits.length, digits]);
    } else {
      chunks.push([1, 0, run]);
    }
  }
  return chunks;
}

function cmp<T extends number | string>(a: T, b: T): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/**
 * Natural, case and accent insensitive comparison of two item texts: ASCII digit
 * runs compare as numbers ("Item 2" before "Item 10"), "Äpfel" sorts next to
 * "apfel", empty texts go last, and equal keys fall back to the raw text.
 */
export function compareItemTextForSort(a: string | null | undefined, b: string | null | undefined): number {
  const fa = foldForSort(a);
  const fb = foldForSort(b);
  const emptyDiff = (fa ? 0 : 1) - (fb ? 0 : 1);
  if (emptyDiff) return emptyDiff;
  const ca = chunksOf(fa);
  const cb = chunksOf(fb);
  for (let i = 0; i < Math.min(ca.length, cb.length); i++) {
    const x = ca[i]!;
    const y = cb[i]!;
    const d = cmp(x[0], y[0]) || cmp(x[1], y[1]) || cmp(x[2], y[2]);
    if (d) return d;
  }
  return cmp(ca.length, cb.length) || cmp(a ?? "", b ?? "");
}

function inPositionOrder<T extends SortableItem>(items: readonly T[]): T[] {
  return items
    .slice()
    .sort((a, b) => a.position.index - b.position.index || cmp(a.id, b.id));
}

/**
 * The items in "sort by name" order. Ties keep their current `(index, id)` order
 * (Array#sort is stable). The server writes `rank + 1` as the new index of each.
 */
export function sortItemsByName<T extends SortableItem>(items: readonly T[]): T[] {
  return inPositionOrder(items).sort((a, b) => compareItemTextForSort(a.text, b.text));
}

/**
 * Ids "Remove duplicates" deletes: items whose text matches by `normalizeItemText`
 * (the item-suggestion rule). Per group the topmost unchecked copy stays, or the
 * topmost copy when all are checked. Empty texts are never duplicates.
 */
export function duplicateItemIdsToRemove<T extends StatefulItem>(items: readonly T[]): string[] {
  const groups = new Map<string, T[]>();
  for (const item of inPositionOrder(items)) {
    const key = normalizeItemText(item.text);
    if (!key) continue;
    const group = groups.get(key);
    if (group) group.push(item);
    else groups.set(key, [item]);
  }
  const remove: string[] = [];
  for (const group of groups.values()) {
    if (group.length < 2) continue;
    const keeper = group.find((it) => !it.state.checked) ?? group[0]!;
    for (const it of group) if (it !== keeper) remove.push(it.id);
  }
  return remove;
}

/**
 * The items as a Markdown checklist (`- [ ] milk` / `- [x] eggs`), one line per
 * non-empty item, in the order given. Line breaks inside an item become spaces so
 * every item stays one line (and the text reads back as the same items).
 */
export function itemsToMarkdown(items: ReadonlyArray<{ text?: string | null; state: { checked: boolean } }>): string {
  const lines: string[] = [];
  for (const item of items) {
    const text = (item.text ?? "").replace(/\s*[\r\n]+\s*/g, " ").trim();
    if (!text) continue;
    lines.push(`- [${item.state.checked ? "x" : " "}] ${text}`);
  }
  return lines.join("\n");
}
