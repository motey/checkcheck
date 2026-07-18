import { describe, it, expect } from "vitest";
import { normalizeItemText, findMatchingCheckedItems } from "@/utils/normalizeItemText";

describe("normalizeItemText", () => {
  it("trims, lowercases and collapses internal whitespace", () => {
    expect(normalizeItemText("  Milk ")).toBe("milk");
    expect(normalizeItemText("MILK")).toBe("milk");
    expect(normalizeItemText("Whole   Milk")).toBe("whole milk");
    expect(normalizeItemText("Whole\tMilk\n")).toBe("whole milk");
  });

  it("treats null/undefined/blank as empty", () => {
    expect(normalizeItemText(null)).toBe("");
    expect(normalizeItemText(undefined)).toBe("");
    expect(normalizeItemText("   ")).toBe("");
  });
});

describe("findMatchingCheckedItems", () => {
  const items = [
    { id: "a", text: "Milk" },
    { id: "b", text: "Bread" },
    { id: "c", text: "Milk chocolate" },
    { id: "d", text: "milk" }, // case-variant exact duplicate
  ];

  it("suggests as you type via prefix match (case/whitespace-insensitive)", () => {
    expect(findMatchingCheckedItems(items, "new", "mi").map((i) => i.id)).toEqual([
      "a",
      "c",
      "d",
    ]);
    expect(findMatchingCheckedItems(items, "new", " MI ").map((i) => i.id)).toEqual([
      "a",
      "c",
      "d",
    ]);
  });

  it("ranks an exact match first", () => {
    // Typing the full word: "Milk" and "milk" are exact, "Milk chocolate" is not.
    const ids = findMatchingCheckedItems(items, "new", "milk").map((i) => i.id);
    expect(ids.slice(0, 2).sort()).toEqual(["a", "d"]);
    expect(ids[2]).toBe("c");
  });

  it("does not match on a non-prefix substring", () => {
    expect(findMatchingCheckedItems(items, "new", "chocolate")).toEqual([]);
  });

  it("excludes the item being typed into", () => {
    expect(findMatchingCheckedItems(items, "a", "milk").map((i) => i.id)).not.toContain("a");
  });

  it("returns [] for empty typed text", () => {
    expect(findMatchingCheckedItems(items, "new", "   ")).toEqual([]);
    expect(findMatchingCheckedItems(items, "new", "")).toEqual([]);
  });

  it("caps the number of suggestions", () => {
    const many = Array.from({ length: 10 }, (_, i) => ({ id: `x${i}`, text: `Task ${i}` }));
    expect(findMatchingCheckedItems(many, "new", "task", 5)).toHaveLength(5);
  });
});
