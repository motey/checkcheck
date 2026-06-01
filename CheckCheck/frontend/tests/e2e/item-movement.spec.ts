/**
 * Item drag-and-drop reordering tests.
 *
 * Opens a checklist in edit mode (the modal), drags one item past another
 * using the drag handle (.list-item-drag-handle), and verifies:
 *   1. The textarea order in the dialog changes immediately.
 *   2. After closing and reopening the modal the new order persists.
 *
 * Items only expose their drag handle when parentEditMode=true, i.e. when
 * the checklist is open in the edit modal.
 *
 * DOM note: in edit mode items render as <textarea> elements (via UTextarea
 * with v-model).  The textarea VALUE is set reactively, not via text content,
 * so page.getByText() / filter({ hasText }) do NOT match item text inside
 * the dialog — always use locator("li").nth(n) or inputValue() instead.
 */
import { test, expect, type Locator, type Page } from "@playwright/test";

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
 * Return the ordered list of item texts by reading each textarea's value.
 * Works regardless of hover state or opacity.
 */
async function itemOrder(page: Page): Promise<string[]> {
  const dialog = page.locator('[role="dialog"]');
  const textareas = dialog.locator("li textarea");
  const count = await textareas.count();
  const texts: string[] = [];
  for (let i = 0; i < count; i++) {
    texts.push(await textareas.nth(i).inputValue());
  }
  return texts;
}

/**
 * Drag via low-level pointer events to reliably pass @formkit/drag-and-drop's
 * activation threshold.  targetYFraction 0.8 = "release 80 % down" = drop after.
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
  await page.mouse.move(srcX + 2, srcY + 6, { steps: 5 });
  await page.mouse.move(tgtX, tgtY, { steps: 30 });
  await page.mouse.up();
}

// ── tests ─────────────────────────────────────────────────────────────────────

test.describe("item movement", () => {
  // DnD + move API + close/reopen modal: allow 25 s before declaring a hang.
  test.setTimeout(25_000);

  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanup) {
      await apiDelete(page, `/api/checklist/${id}`);
    }
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("dragging an item reorders it within the checklist and persists after reopening the modal", async ({
    page,
  }) => {
    const tag = Date.now();
    const clName = `DnD-Items-${tag}`;
    const item1Text = `Alpha-${tag}`;
    const item2Text = `Beta-${tag}`;

    // Item 1 created first → lowest position.index → renders at top.
    // Item 2 created second → higher index → renders below item 1.
    // (Items sort ASCENDING by position.index, opposite of checklists.)
    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    await apiPost(page, `/api/checklist/${cl.id}/item`, { text: item1Text });
    await apiPost(page, `/api/checklist/${cl.id}/item`, { text: item2Text });

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");

    // Click the card title to open the edit modal.
    // We click the title element specifically, not the card center, because
    // items rendered in preview mode have checkboxes with @click.stop which
    // absorb a center click and prevent the modal from opening.
    const card = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: clName });
    await expect(card).toBeVisible();
    await card.locator(".text-lg.font-semibold").click();

    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // Wait until the two textareas have their values populated.
    // (getByText / hasText do NOT match textarea values — use inputValue.)
    await expect(dialog.locator("li textarea").nth(0)).toHaveValue(
      new RegExp(item1Text),
      { timeout: 5_000 }
    );
    await expect(dialog.locator("li textarea").nth(1)).toHaveValue(
      new RegExp(item2Text),
      { timeout: 5_000 }
    );

    // Confirm initial order: item1 first, item2 second.
    const orderBefore = await itemOrder(page);
    const idx1Before = orderBefore.findIndex((t) => t.includes(item1Text));
    const idx2Before = orderBefore.findIndex((t) => t.includes(item2Text));
    expect(idx1Before, "item1 should start before item2").toBeLessThan(idx2Before);

    // Drag item1 below item2.
    // Use nth() because hasText does not match textarea values in edit mode.
    const item1Row = dialog.locator("li").nth(idx1Before);
    const item2Row = dialog.locator("li").nth(idx2Before);

    await item1Row.hover();
    const handle1 = item1Row.locator(".list-item-drag-handle");
    await expect(handle1).toBeVisible({ timeout: 3_000 });

    const moveResponsePromise = page.waitForResponse(
      (r) => r.url().includes("/move/") && r.request().method() === "PUT",
      { timeout: 10_000 }
    );
    await drag(page, handle1, item2Row);
    await moveResponsePromise;

    // After drag: item2 should now be above item1.
    const orderAfter = await itemOrder(page);
    const idx1After = orderAfter.findIndex((t) => t.includes(item1Text));
    const idx2After = orderAfter.findIndex((t) => t.includes(item2Text));
    expect(idx2After, "item2 should be above item1 after drag").toBeLessThan(idx1After);

    // Close the modal and reopen to confirm persistence.
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible({ timeout: 3_000 });

    await card.locator(".text-lg.font-semibold").click();
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // Wait for textareas to repopulate, then verify order persisted.
    await expect(dialog.locator("li textarea").nth(0)).toHaveValue(/.+/, { timeout: 5_000 });
    const orderReloaded = await itemOrder(page);
    const idx1Reloaded = orderReloaded.findIndex((t) => t.includes(item1Text));
    const idx2Reloaded = orderReloaded.findIndex((t) => t.includes(item2Text));
    expect(
      idx2Reloaded,
      "item2 should still be above item1 after modal close/reopen"
    ).toBeLessThan(idx1Reloaded);
  });
});
