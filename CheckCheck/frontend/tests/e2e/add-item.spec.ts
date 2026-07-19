/**
 * "Add new item" affordance in the card editor.
 *
 * Phase 7 converted the hand-rolled interactive <div> (AddNewButton) into a
 * real <button data-testid="add-item">. This guards that the button is present
 * and that clicking it appends an item to the open card.
 *
 * Items render as <textarea> elements inside the editor dialog (see
 * item-movement.spec.ts for the DOM note), so we assert on the textarea count.
 */
import { test, expect, type Page } from "@playwright/test";

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

test.describe("add item", () => {
  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanup) {
      await apiDelete(page, `/api/checklist/${id}`);
    }
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("clicking the add-item button appends an item to the open card", async ({
    page,
  }) => {
    const clName = `AddItem-${Date.now()}`;
    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");

    // Open the editor by clicking the title (item checkboxes absorb a centre
    // click via @click.stop — see item-movement.spec.ts).
    const card = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: clName });
    await expect(card).toBeVisible();
    await card.locator("[data-testid=card-title]").click();

    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // Fresh card starts with no items.
    const items = dialog.locator("[data-testid=item-row]");
    await expect(items).toHaveCount(0);

    await dialog.locator("[data-testid=add-item]").click();

    // Clicking the button creates an item row (auto-focused into its editor).
    await expect(items).toHaveCount(1, { timeout: 5_000 });
  });
});
