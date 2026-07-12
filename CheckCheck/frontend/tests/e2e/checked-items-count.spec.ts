/**
 * "Checked items" count reactivity (bug: count stays 0 after checking an item).
 *
 * Regression guard: CheckListItemCollection/Seperated.vue read the checked /
 * unchecked counts via `getItemCount` (which returns a plain number) into plain
 * consts at setup time. Those consts froze at their mount-time value, so an item
 * checked *after* the view mounted never updated the "N checked items" label —
 * the count stuck at 0. Making them `computed` fixes it.
 *
 * This proves BOTH surfaces update from the shared store: the count label inside
 * the editor, and the "+ N checked items" separator on the background board card.
 *
 * Runs online (localFirst is the default path); the reactivity is store-driven,
 * not transport-driven.
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

test.describe("checked-items count reactivity", () => {
  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanup) await apiDelete(page, `/api/checklist/${id}`);
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("checking an item updates the count in the editor AND on the board card", async ({ page }) => {
    const tag = Date.now();
    const clName = `CheckedCount-${tag}`;

    // A card with three unchecked items — the reported scenario.
    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    for (const t of ["one", "two", "three"]) {
      await apiPost(page, `/api/checklist/${cl.id}/item`, { text: `${t}-${tag}` });
    }

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");

    // The board card mounts with zero checked items → no "checked items" separator.
    const card = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: clName });
    await expect(card).toBeVisible();
    await expect(card).not.toContainText("checked items");

    // Open the editor — Seperated.vue mounts here with checkedItemCount = 0. This
    // is the mount at which the buggy const froze.
    await card.locator("[data-testid=card-title]").click();
    const dialog = page.locator('[role="dialog"]:has(.checklist)');
    await expect(dialog).toBeVisible({ timeout: 5_000 });
    await expect(dialog.locator("li textarea")).toHaveCount(3, { timeout: 5_000 });
    await expect(dialog).toContainText(/0\s+checked items/);

    // Check the first item AFTER mount — the crux of the bug.
    await dialog.locator("li").first().getByRole("checkbox").click();

    // Editor label must now reflect 1 (before the fix it stayed at 0).
    await expect(dialog).toContainText(/1\s+checked items/, { timeout: 5_000 });

    // Close the editor; the background board card must ALSO update to its
    // "+ 1 checked items" separator (before the fix the separator never appeared).
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(page.locator('[role="dialog"]:has(.checklist)')).toHaveCount(0, { timeout: 5_000 });
    await expect(card).toContainText(/1\s+checked items/, { timeout: 5_000 });
  });
});
