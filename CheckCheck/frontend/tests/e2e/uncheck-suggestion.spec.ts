/**
 * Keep-style "uncheck existing item instead of duplicating".
 *
 * When a card has a *checked* item and the user types a new item with the same
 * text, an inline suggestion (`[data-testid=uncheck-suggestion]`) offers to
 * uncheck the existing one. Accepting it unchecks the match and drops the
 * just-typed duplicate — no second item is created. Detection is client-side;
 * the only write is the uncheck. See CheckListItem.vue / CheckListItemCollection.
 *
 * Items render as <textarea> elements inside the editor dialog. With
 * `checked_items_seperated` on (the default), the checked "Milk" starts
 * collapsed, so the only visible textarea is the one being typed; after
 * accepting, the now-unchecked "Milk" is the single remaining item.
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

async function apiPatch(page: Page, path: string, body: object) {
  const res = await page.request.patch(path, {
    data: body,
    headers: { "Content-Type": "application/json" },
  });
  expect(res.ok(), `PATCH ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

async function apiDelete(page: Page, path: string) {
  await page.request.delete(path).catch(() => {});
}

test.describe("uncheck-existing suggestion", () => {
  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanup) {
      await apiDelete(page, `/api/checklist/${id}`);
    }
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("typing a checked item's text offers to uncheck it instead of duplicating", async ({
    page,
  }) => {
    const clName = `Dedup-${Date.now()}`;
    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    // Seed a checked "Milk".
    const item = await apiPost(page, `/api/checklist/${cl.id}/item`, { text: "Milk" });
    await apiPatch(page, `/api/checklist/${cl.id}/item/${item.id}/state`, { checked: true });

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");

    const card = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: clName });
    await expect(card).toBeVisible();
    await card.locator("[data-testid=card-title]").click();

    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // Add a new item and type the same text (different case, to prove the match
    // is case-insensitive). The seeded checked "Milk" stays mounted (collapsed
    // checked section), so there are now two textareas; the new empty one is the
    // unchecked-section entry rendered first.
    await dialog.locator("[data-testid=add-item]").click();
    const items = dialog.locator("[data-testid=item-row]");
    await expect(items).toHaveCount(2, { timeout: 5_000 });
    const newItem = dialog.locator("[data-testid=item-text-editor]");
    await expect(newItem).toHaveValue("");
    // Type only a prefix ("mi") to prove suggestions appear live, before the full
    // word is entered — the autocomplete feel, not just an exact-match check.
    await newItem.fill("mi");

    // The suggestion for the checked "Milk" appears; accept it.
    const suggestions = dialog.locator("[data-testid=uncheck-suggestion]");
    await expect(suggestions.first()).toBeVisible({ timeout: 5_000 });
    await expect(suggestions.first()).toContainText("Milk");
    await suggestions.first().click();

    // Suggestions clear, the typed duplicate is gone, and the single remaining
    // item is the now-unchecked "Milk" (no duplicate created).
    await expect(dialog.locator("[data-testid=uncheck-suggestions]")).toHaveCount(0);
    await expect(items).toHaveCount(1, { timeout: 5_000 });
    await expect(dialog.locator("[data-testid=item-text-editor]")).toHaveValue("Milk");
  });
});
