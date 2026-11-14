// ── "Add items from text" parser (issue #24) ─────────────────────────────────
//
// Turns pasted or typed multi-line text into checklist items: one item per
// non-empty line, with common list markers removed. It reads back what
// "Copy list as text" writes (`itemsToMarkdown` in utils/itemBulk.ts), so a list
// copied from one card can be added to another unchanged.

export interface ParsedItem {
  text: string;
  checked: boolean;
}

// An optional bullet (`-`, `*`, `+`, `•`) or ordered marker (`1.`, `1)`) that must
// be followed by whitespace (or end the line), so "-5 degrees", "*important*" and
// "1.5 kg flour" keep their text.
const BULLET = /^(?:[-*+•]|\d+[.)])(?:\s+|$)/;
// An optional task-list box (`[ ]`, `[x]`, `[X]`) followed by whitespace or the
// end of the line.
const CHECKBOX = /^\[([ xX])\](?:\s+|$)/;

/** Parse one line; null when nothing is left (empty line, bare marker). */
export function parseItemLine(line: string): ParsedItem | null {
  let rest = line.trim();
  rest = rest.replace(BULLET, "");
  let checked = false;
  const box = CHECKBOX.exec(rest);
  if (box) {
    checked = box[1] !== " ";
    rest = rest.slice(box[0].length);
  }
  rest = rest.trim();
  return rest ? { text: rest, checked } : null;
}

/** All items in the text, in order. Empty lines are skipped. */
export function parseItemsFromText(text: string | null | undefined): ParsedItem[] {
  const items: ParsedItem[] = [];
  for (const line of (text ?? "").split(/\r\n|\r|\n/)) {
    const item = parseItemLine(line);
    if (item) items.push(item);
  }
  return items;
}
