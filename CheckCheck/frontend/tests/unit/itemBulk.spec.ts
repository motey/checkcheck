// Unit tests for the card bulk-action rules (issue #20). The sort vector is shared
// verbatim with backend/tests/tests_item_text.py: the server and the optimistic
// client must order and de-duplicate identically.
import { describe, it, expect } from "vitest";
import {
  compareItemTextForSort,
  duplicateItemIdsToRemove,
  itemsToMarkdown,
  sortItemsByName,
} from "@/utils/itemBulk";

const SORTED_VECTOR = [
  "1 apple",
  "2 eggs",
  "10 eggs",
  "Äpfel",
  "apple",
  "Apple pie",
  "banana",
  "Item 2",
  "item 10",
  "Zucchini",
  "",
];

let n = 0;
function it_(text: string, index: number, checked = false) {
  n++;
  return { id: `id-${String(n).padStart(3, "0")}`, text, position: { index }, state: { checked } };
}

describe("compareItemTextForSort", () => {
  it("orders the shared vector naturally, ignoring case and accents, empty last", () => {
    const shuffled = [...SORTED_VECTOR].reverse();
    shuffled.push(shuffled.shift()!);
    expect(shuffled.sort(compareItemTextForSort)).toEqual(SORTED_VECTOR);
  });

  it("folds case, whitespace, accents and leading zeros", () => {
    // Plain code point order would put "É" and "Z" after every lowercase letter.
    expect(compareItemTextForSort("Éclair", "dates")).toBeGreaterThan(0);
    expect(compareItemTextForSort("Éclair", "figs")).toBeLessThan(0);
    expect(compareItemTextForSort("  Milk   shake ", "milk shakes")).toBeLessThan(0);
    expect(compareItemTextForSort("007", "8")).toBeLessThan(0);
    // Equal keys fall back to the raw text, so the order stays deterministic.
    expect(compareItemTextForSort("Crème", "creme")).not.toBe(0);
  });
});

describe("sortItemsByName", () => {
  it("sorts checked and unchecked together and keeps ties in position order", () => {
    const items = [
      it_("milk", 1),
      it_("bread", 2, true),
      it_("Milk", 3),
      it_("apple", 0.5, true),
    ];
    const sorted = sortItemsByName(items).map((i) => `${i.text}@${i.position.index}`);
    // "Milk" vs "milk": same key, raw-text tiebreak puts "Milk" first (code point).
    expect(sorted).toEqual(["apple@0.5", "bread@2", "Milk@3", "milk@1"]);
  });

  it("does not mutate its input", () => {
    const items = [it_("b", 1), it_("a", 2)];
    sortItemsByName(items);
    expect(items.map((i) => i.text)).toEqual(["b", "a"]);
  });
});

describe("duplicateItemIdsToRemove", () => {
  it("keeps the topmost unchecked copy, else the topmost, and ignores empty items", () => {
    const milkChecked = it_("Milk", 1, true);
    const milkUnchecked = it_("  milk ", 2);
    const milkUnchecked2 = it_("MILK", 3);
    const eggsTop = it_("eggs", 4, true);
    const eggsOther = it_("Eggs", 5, true);
    const empty1 = it_("", 6);
    const empty2 = it_("", 7);
    const single = it_("butter", 8);
    const items = [eggsOther, milkUnchecked2, empty2, milkChecked, single, eggsTop, milkUnchecked, empty1];
    expect(duplicateItemIdsToRemove(items).sort()).toEqual(
      [milkChecked.id, milkUnchecked2.id, eggsOther.id].sort()
    );
  });

  it("returns nothing for a list without duplicates", () => {
    expect(duplicateItemIdsToRemove([it_("a", 1), it_("b", 2)])).toEqual([]);
  });
});

describe("itemsToMarkdown", () => {
  it("renders a Markdown checklist, skipping empty items and flattening line breaks", () => {
    const md = itemsToMarkdown([
      { text: "milk", state: { checked: false } },
      { text: "eggs", state: { checked: true } },
      { text: "   ", state: { checked: false } },
      { text: "two\nlines ", state: { checked: false } },
    ]);
    expect(md).toBe("- [ ] milk\n- [x] eggs\n- [ ] two lines");
  });

  it("is empty for no items", () => {
    expect(itemsToMarkdown([])).toBe("");
  });
});
