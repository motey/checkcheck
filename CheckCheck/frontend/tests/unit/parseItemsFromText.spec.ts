import { describe, it, expect } from "vitest";
import { parseItemsFromText, parseItemLine } from "@/utils/parseItemsFromText";
import { itemsToMarkdown } from "@/utils/itemBulk";

describe("parseItemsFromText", () => {
  it("makes one item per non-empty line, in order", () => {
    expect(parseItemsFromText("milk\n\n  eggs  \r\nbread\r\n\n")).toEqual([
      { text: "milk", checked: false },
      { text: "eggs", checked: false },
      { text: "bread", checked: false },
    ]);
  });

  it("strips bullet and ordered list markers", () => {
    expect(parseItemsFromText("- a\n* b\n• c\n+ d\n1. e\n12) f\n  - nested").map((i) => i.text)).toEqual([
      "a",
      "b",
      "c",
      "d",
      "e",
      "f",
      "nested",
    ]);
  });

  it("reads task-list boxes; [x] and [X] create checked items", () => {
    expect(parseItemsFromText("- [ ] open\n- [x] done\n- [X] DONE\n[x] no bullet\n* [ ] star")).toEqual([
      { text: "open", checked: false },
      { text: "done", checked: true },
      { text: "DONE", checked: true },
      { text: "no bullet", checked: true },
      { text: "star", checked: false },
    ]);
  });

  it("keeps text that only looks like a marker", () => {
    expect(parseItemsFromText("-5 degrees\n*important*\n1.5 kg flour\n2 eggs\n[x]ray").map((i) => i.text)).toEqual([
      "-5 degrees",
      "*important*",
      "1.5 kg flour",
      "2 eggs",
      "[x]ray",
    ]);
  });

  it("skips lines with nothing left after the markers", () => {
    expect(parseItemsFromText("-\n- [ ]\n- [x]   \n1.\n   ")).toEqual([]);
    expect(parseItemLine("")).toBeNull();
    expect(parseItemsFromText(null)).toEqual([]);
  });

  it("strips only one bullet and one box, so item text keeps its own markers", () => {
    expect(parseItemsFromText("- [ ] - dash item\n- [x] [ ] box item\n- [ ] 1. step")).toEqual([
      { text: "- dash item", checked: false },
      { text: "[ ] box item", checked: true },
      { text: "1. step", checked: false },
    ]);
  });

  it("round-trips the 'Copy list as text' output", () => {
    const items = [
      { text: "milk", state: { checked: false } },
      { text: "eggs", state: { checked: true } },
      { text: "- odd one", state: { checked: false } },
      { text: "[x] boxed", state: { checked: true } },
      { text: "3. third", state: { checked: false } },
    ];
    expect(parseItemsFromText(itemsToMarkdown(items))).toEqual(
      items.map((i) => ({ text: i.text, checked: i.state.checked }))
    );
  });
});
