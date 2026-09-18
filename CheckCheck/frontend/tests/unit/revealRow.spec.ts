// @vitest-environment jsdom
//
// Unit tests for utils/revealRow.ts (mobile editor plan, M3). jsdom has no
// layout, so the rects are stubbed; the math is what is under test.
import { describe, it, expect, vi, afterEach } from "vitest";
import { revealRow, revealScrollDelta } from "@/utils/revealRow";

// Visible region 100..500 unless a case overrides it.
const base = { visibleTop: 100, visibleBottom: 500, marginBottom: 0 };

describe("revealScrollDelta", () => {
  it("does nothing when the block already fits", () => {
    expect(revealScrollDelta({ ...base, rowTop: 200, blockBottom: 400 })).toBe(0);
  });

  it("scrolls down just enough to clear the bottom edge plus the margin", () => {
    expect(revealScrollDelta({ ...base, rowTop: 400, blockBottom: 560, marginBottom: 64 })).toBe(124);
  });

  it("counts the margin even when the block itself fits", () => {
    expect(revealScrollDelta({ ...base, rowTop: 300, blockBottom: 480, marginBottom: 64 })).toBe(44);
  });

  it("never scrolls the row out of the top", () => {
    // Needs 300 to show the whole list, but the row is only 50 below the top.
    expect(revealScrollDelta({ ...base, rowTop: 150, blockBottom: 800 })).toBe(50);
  });

  it("scrolls up when the row is above the visible top", () => {
    expect(revealScrollDelta({ ...base, rowTop: 60, blockBottom: 200 })).toBe(-40);
  });
});

describe("revealRow", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    delete (window as { visualViewport?: unknown }).visualViewport;
  });

  // jsdom has no window.visualViewport at all, so install a plain value.
  function setViewport(vv: Pick<VisualViewport, "offsetTop" | "height"> | null) {
    Object.defineProperty(window, "visualViewport", { value: vv, configurable: true });
  }

  function rect(top: number, bottom: number) {
    return { top, bottom, left: 0, right: 0, width: 0, height: bottom - top, x: 0, y: top, toJSON() {} } as DOMRect;
  }

  function setup(regionRect: DOMRect, rowRect: DOMRect, blockRect: DOMRect, scrollable = true) {
    const region = document.createElement("div");
    if (scrollable) region.style.overflowY = "auto";
    const block = document.createElement("div");
    const row = document.createElement("div");
    block.appendChild(row);
    region.appendChild(block);
    document.body.appendChild(region);
    region.getBoundingClientRect = () => regionRect;
    row.getBoundingClientRect = () => rowRect;
    block.getBoundingClientRect = () => blockRect;
    const scrollBy = vi.fn();
    region.scrollBy = scrollBy as unknown as typeof region.scrollBy;
    return { block, row, scrollBy };
  }

  it("scrolls the nearest scrollable ancestor with the requested behaviour", () => {
    setViewport(null);
    const { block, row, scrollBy } = setup(rect(0, 400), rect(350, 380), rect(350, 450));
    revealRow(block, row, "auto");
    expect(scrollBy).toHaveBeenCalledWith({ top: 50, behavior: "auto" });
  });

  it("treats the part below the visual viewport (the keyboard) as hidden", () => {
    setViewport({ offsetTop: 0, height: 300 });
    // The region is 600 tall in layout px, but only 300 are visible.
    const { block, row, scrollBy } = setup(rect(0, 600), rect(250, 280), rect(250, 350));
    revealRow(block, row);
    expect(scrollBy).toHaveBeenCalledWith({ top: 50, behavior: "smooth" });
  });

  it("does not scroll when the block fits", () => {
    setViewport(null);
    const { block, row, scrollBy } = setup(rect(0, 600), rect(100, 130), rect(100, 200));
    revealRow(block, row);
    expect(scrollBy).not.toHaveBeenCalled();
  });

  it("is a no-op without a scrollable ancestor", () => {
    setViewport(null);
    const { block, row, scrollBy } = setup(rect(0, 100), rect(350, 380), rect(350, 450), false);
    expect(() => revealRow(block, row)).not.toThrow();
    expect(scrollBy).not.toHaveBeenCalled();
  });
});
