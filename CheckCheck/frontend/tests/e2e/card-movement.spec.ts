/**
 * Card drag-and-drop reordering tests.
 *
 * Verifies that dragging a checklist card to a new position on the board:
 *   1. Updates the DOM order immediately.
 *   2. Persists the new order after a full page reload.
 *
 * Uses a narrow viewport (420 px) so the grid collapses to a single column,
 * making vertical drag order directly comparable to DOM index.
 */
import { test, expect, type Locator, type Page } from "@playwright/test";
import { resolve } from "path";

// ── helpers ──────────────────────────────────────────────────────────────────

async function apiPost(page: Page, path: string, body: object) {
  const res = await page.request.post(path, {
    data: body,
    headers: { "Content-Type": "application/json" },
  });
  expect(res.ok(), `POST ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

async function apiDelete(page: Page, path: string) {
  await page.request.delete(path).catch(() => {});
}

/**
 * Return the DOM index (0-based) of the .checklist-preview card that contains
 * the given text within the board grid.  Returns -1 if not found.
 */
async function cardIndex(page: Page, name: string): Promise<number> {
  const cards = page.locator("[data-testid=checklist-board] .checklist-preview");
  const count = await cards.count();
  for (let i = 0; i < count; i++) {
    const text = await cards.nth(i).textContent();
    if (text?.includes(name)) return i;
  }
  return -1;
}

/**
 * Simulate a drag-and-drop using low-level pointer events so the drag
 * activation threshold in @formkit/drag-and-drop is reliably triggered.
 *
 * targetYFraction – where inside the target to release (0 = top, 1 = bottom).
 * Releasing near the bottom half signals "drop after this item".
 */
async function drag(
  page: Page,
  source: Locator,
  target: Locator,
  targetYFraction = 0.8
) {
  const srcBox = await source.boundingBox();
  const tgtBox = await target.boundingBox();
  if (!srcBox || !tgtBox) throw new Error("Could not read bounding boxes for drag");

  const srcX = srcBox.x + srcBox.width / 2;
  const srcY = srcBox.y + srcBox.height / 2;
  const tgtX = tgtBox.x + tgtBox.width / 2;
  const tgtY = tgtBox.y + tgtBox.height * targetYFraction;

  await page.mouse.move(srcX, srcY);
  await page.mouse.down();
  // Small initial movement to pass the drag-activation distance threshold.
  await page.mouse.move(srcX + 2, srcY + 6, { steps: 5 });
  // Smooth arc to the target.
  await page.mouse.move(tgtX, tgtY, { steps: 30 });
  await page.mouse.up();
}

// ── tests ─────────────────────────────────────────────────────────────────────

test.describe("card movement", () => {
  // DnD + move API call + reload: allow 25 s before declaring a hang.
  test.setTimeout(25_000);

  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanup) {
      await apiDelete(page, `/api/checklist/${id}`);
    }
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("dragging a card to a new position reorders the board and persists after reload", async ({
    page,
  }) => {
    // Force a single-column grid so that DOM index == visual order.
    await page.setViewportSize({ width: 420, height: 900 });

    const tag = Date.now();
    const nameA = `DnD-Card-A-${tag}`;
    const nameB = `DnD-Card-B-${tag}`;

    // Card A is created first → lower position.index → appears after B.
    // Card B is created second → higher position.index → appears before A.
    const clA = await apiPost(page, "/api/checklist", { name: nameA });
    cleanup.push(clA.id);
    const clB = await apiPost(page, "/api/checklist", { name: nameB });
    cleanup.push(clB.id);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(page.getByText(nameA)).toBeVisible();
    await expect(page.getByText(nameB)).toBeVisible();

    // Confirm initial order: B (higher index) should be above A.
    const idxBefore_A = await cardIndex(page, nameA);
    const idxBefore_B = await cardIndex(page, nameB);
    expect(idxBefore_B, "B should start above A").toBeLessThan(idxBefore_A);

    // Drag B below A (drop onto the bottom portion of A).
    const cardB = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: nameB });
    const cardA = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: nameA });

    // Wait for the board move API call before asserting.
    const moveResponsePromise = page.waitForResponse(
      (r) => r.url().includes("/move/") && r.request().method() === "PUT",
      { timeout: 10_000 }
    );
    await drag(page, cardB, cardA);
    await moveResponsePromise;

    // After drag: A should now be above B.
    const idxAfter_A = await cardIndex(page, nameA);
    const idxAfter_B = await cardIndex(page, nameB);
    expect(idxAfter_A, "A should be above B after drag").toBeLessThan(idxAfter_B);

    // Reload to verify the new order is persisted server-side.
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(page.getByText(nameA)).toBeVisible();
    await expect(page.getByText(nameB)).toBeVisible();

    const idxReloaded_A = await cardIndex(page, nameA);
    const idxReloaded_B = await cardIndex(page, nameB);
    expect(
      idxReloaded_A,
      "A should still be above B after reload"
    ).toBeLessThan(idxReloaded_B);
  });
});
