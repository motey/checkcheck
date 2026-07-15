/**
 * Deleting items in the card editor.
 *
 * Two affordances, both edit-mode only:
 *  - a hover-revealed × button (data-testid="delete-item") mirroring the drag
 *    handle on the left, and
 *  - Backspace on an already-empty item (Keep-style), which removes the item
 *    and moves focus to the end of the previous one.
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

async function openEditor(page: Page, clName: string) {
  await page.goto("/");
  await page.waitForSelector("[data-testid=checklist-board]");
  // Click the title specifically — item checkboxes absorb a centre click via
  // @click.stop (see item-movement.spec.ts).
  const card = page
    .locator("[data-testid=checklist-board] .checklist-preview")
    .filter({ hasText: clName });
  await expect(card).toBeVisible();
  await card.locator("[data-testid=card-title]").click();
  const dialog = page.locator('[role="dialog"]');
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  return dialog;
}

test.describe("delete item", () => {
  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanup) {
      await apiDelete(page, `/api/checklist/${id}`);
    }
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("clicking the × button removes an item from the open card", async ({
    page,
  }) => {
    const clName = `DelItem-${Date.now()}`;
    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    await apiPost(page, `/api/checklist/${cl.id}/item`, { text: "Alpha" });
    await apiPost(page, `/api/checklist/${cl.id}/item`, { text: "Beta" });

    const dialog = await openEditor(page, clName);
    const items = dialog.locator("li textarea");
    await expect(items).toHaveCount(2, { timeout: 5_000 });

    // Delete the first item; the row (and its × button) disappears.
    await dialog.locator("[data-testid=delete-item]").first().click();

    await expect(items).toHaveCount(1, { timeout: 5_000 });
    await expect(items.nth(0)).toHaveValue(/Beta/, { timeout: 5_000 });
  });

  test("backspace on an empty item deletes it and focuses the previous item", async ({
    page,
  }) => {
    const clName = `DelBksp-${Date.now()}`;
    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    await apiPost(page, `/api/checklist/${cl.id}/item`, { text: "First" });
    // Second item intentionally empty so a single Backspace removes it.
    await apiPost(page, `/api/checklist/${cl.id}/item`, { text: "" });

    const dialog = await openEditor(page, clName);
    const items = dialog.locator("li textarea");
    await expect(items).toHaveCount(2, { timeout: 5_000 });
    await expect(items.nth(0)).toHaveValue(/First/, { timeout: 5_000 });

    // Focus the empty second item and press Backspace.
    await items.nth(1).click();
    await page.keyboard.press("Backspace");

    // Item removed, and focus moved up to the previous ("First") item.
    await expect(items).toHaveCount(1, { timeout: 5_000 });
    await expect(items.nth(0)).toHaveValue(/First/);
    await expect(items.nth(0)).toBeFocused();
  });

  test("backspace on a non-empty item does not delete it", async ({ page }) => {
    const clName = `DelKeep-${Date.now()}`;
    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    await apiPost(page, `/api/checklist/${cl.id}/item`, { text: "x" });

    const dialog = await openEditor(page, clName);
    const items = dialog.locator("li textarea");
    await expect(items).toHaveCount(1, { timeout: 5_000 });
    await expect(items.nth(0)).toHaveValue(/x/, { timeout: 5_000 });

    // Caret at end; Backspace removes the char but keeps the (now-empty) item.
    await items.nth(0).click();
    await page.keyboard.press("End");
    await page.keyboard.press("Backspace");

    await expect(items).toHaveCount(1);
    await expect(items.nth(0)).toHaveValue("");
  });
});
