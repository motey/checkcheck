// ── Keep a focused item row and its suggestion list visible (mobile editor M3) ─
//
// While an item is being typed its uncheck-suggestion list hangs off the row.
// On a phone the keyboard covers the bottom of the screen, so the list can land
// behind it. `revealRow` scrolls the row's scroll region just far enough that
// the whole block (row plus list plus its `scroll-margin-bottom`) ends above
// the visible bottom edge, but never so far that the row itself leaves the top:
// seeing what you type beats seeing the last suggestion.
//
// "Visible" is the scroll region clipped to `window.visualViewport`, so the
// keyboard counts as covered even where the browser does not resize the layout
// viewport for it (iOS Safari).

export interface RevealGeometry {
  /** Visible top and bottom edge of the scroll region, in client px. */
  visibleTop: number;
  visibleBottom: number;
  /** Top edge of the focused row itself (not the suggestion list). */
  rowTop: number;
  /** Bottom edge of the row plus everything hanging below it. */
  blockBottom: number;
  /** Extra air wanted below the block (its scroll-margin-bottom). */
  marginBottom: number;
}

/**
 * How far to scroll the region down (positive) or up (negative) to reveal the
 * block. 0 when it already fits. The row's top edge is a hard limit.
 */
export function revealScrollDelta(g: RevealGeometry): number {
  if (g.rowTop < g.visibleTop) return g.rowTop - g.visibleTop;
  const needed = g.blockBottom + g.marginBottom - g.visibleBottom;
  if (needed <= 0) return 0;
  return Math.min(needed, g.rowTop - g.visibleTop);
}

function scrollParent(el: HTMLElement): HTMLElement | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const { overflowY } = getComputedStyle(p);
    if (overflowY === "auto" || overflowY === "scroll") return p;
  }
  return null;
}

/**
 * Scroll `block` (the row wrapper, suggestion list included) into the visible
 * part of its scroll region, keeping `row` in view. No-op without a scrollable
 * ancestor.
 */
export function revealRow(block: HTMLElement, row: HTMLElement, behavior: ScrollBehavior = "smooth") {
  const region = scrollParent(block);
  if (!region) return;
  const regionRect = region.getBoundingClientRect();
  const vv = window.visualViewport;
  const vvTop = vv ? vv.offsetTop : 0;
  const vvBottom = vv ? vv.offsetTop + vv.height : window.innerHeight;
  const delta = revealScrollDelta({
    visibleTop: Math.max(regionRect.top, vvTop),
    visibleBottom: Math.min(regionRect.bottom, vvBottom),
    rowTop: row.getBoundingClientRect().top,
    blockBottom: block.getBoundingClientRect().bottom,
    marginBottom: parseFloat(getComputedStyle(block).scrollMarginBottom) || 0,
  });
  if (Math.abs(delta) >= 1) region.scrollBy({ top: delta, behavior });
}
