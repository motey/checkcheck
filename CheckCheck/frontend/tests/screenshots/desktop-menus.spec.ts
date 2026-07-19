/**
 * The three cropped feature shots used in docs/screenshots.md.
 *
 * Writes: DesktopCompactShowItemOptions.png
 *         DesktopCompactShowItemSuggestions.png
 *         DesktopCompactShowShareMenu.png
 *
 * These are tight crops rather than full viewports, so they use element /
 * union screenshots — the dropdown and dialog render in portals outside the
 * card's DOM subtree, hence shootUnion for the "card + its open menu" framing.
 *
 * Runs after the board and editor shots (alphabetical file order within the
 * desktop project): the suggestion shot creates a throwaway card via the API,
 * which would otherwise appear in the board screenshots.
 */
import { test, expect, type Page } from "@playwright/test";
import {
  openBoard,
  stabilize,
  shootElement,
  shootUnion,
  revealCardFooter,
  closeSse,
} from "./helpers";

// Cards created via the API for shots that need an exact, contrived state.
const cleanup: string[] = [];

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

test.afterEach(async ({ page }) => {
  for (const id of cleanup) {
    await page.request.delete(`/api/checklist/${id}`).catch(() => {});
  }
  cleanup.length = 0;
  await closeSse(page);
});

test("DesktopCompactShowItemOptions", async ({ page }) => {
  await openBoard(page, "dark");

  // The footer holding the kebab is hover-only; pin it open so it stays visible
  // in the shot once the dropdown takes the pointer-events lock.
  await revealCardFooter(page);

  // First card on the board, and the kebab menu in its footer.
  const card = page.locator("[data-testid=checklist-board] .checklist-preview").first();
  await expect(card).toBeVisible();
  await card.locator("[data-testid=card-options-menu]").click();

  const menu = page.locator("[role=menu]");
  await expect(menu).toBeVisible();
  // The menu is the subject; confirm it rendered its bulk actions before
  // shooting, so a half-mounted dropdown can never be captured.
  await expect(menu.locator("[data-testid=card-untick-all]")).toBeVisible();

  await stabilize(page);
  // stabilize() injects its own style tag, so re-assert the footer override
  // survived and the menu is still anchored to something visible.
  await revealCardFooter(page);
  await expect(card.locator(".checklist-footer")).toBeVisible();

  await shootUnion(page, [card, menu], "DesktopCompactShowItemOptions");
});

test("DesktopCompactShowItemSuggestions", async ({ page }) => {
  await openBoard(page, "dark");

  // A purpose-built card: one checked item, so typing its prefix triggers the
  // "uncheck this instead of duplicating" suggestion. Contriving it here beats
  // hunting the seeded board for a card that happens to be in this state.
  const clName = "Groceries";
  const cl = await apiPost(page, "/api/checklist", { name: clName });
  cleanup.push(cl.id);
  const item = await apiPost(page, `/api/checklist/${cl.id}/item`, { text: "Milk" });
  await apiPatch(page, `/api/checklist/${cl.id}/item/${item.id}/state`, { checked: true });

  await page.reload();
  await expect(page.locator("[data-testid=checklist-board]")).toBeVisible();

  const card = page
    .locator("[data-testid=checklist-board] .checklist-preview")
    .filter({ hasText: clName })
    .first();
  await expect(card).toBeVisible();
  await card.locator("[data-testid=card-title]").click();

  const dialog = page.locator("[role=dialog]");
  await expect(dialog).toBeVisible();

  await dialog.locator("[data-testid=add-item]").click();
  const editor = dialog.locator("[data-testid=item-text-editor]");
  await expect(editor).toHaveValue("");
  // A prefix rather than the full word: shows the autocomplete behaviour, which
  // is what the docs caption describes.
  await editor.fill("mi");

  const suggestions = dialog.locator("[data-testid=uncheck-suggestions]");
  await expect(suggestions).toBeVisible();
  await stabilize(page);

  await shootElement(dialog, "DesktopCompactShowItemSuggestions");
});

test("DesktopCompactShowShareMenu", async ({ page }) => {
  await openBoard(page, "dark");

  // Must be a card the admin OWNS: a collaborator sees a read-only variant of
  // this dialog with just "Leave list", not the invite / group / public-link /
  // transfer-ownership controls the docs caption describes. The seeded board
  // leads with lists shared *with* admin, so create one instead of guessing.
  const clName = "Weekend trip";
  const cl = await apiPost(page, "/api/checklist", { name: clName });
  cleanup.push(cl.id);

  await page.reload();
  await expect(page.locator("[data-testid=checklist-board]")).toBeVisible();

  // The footer renders on the grid preview card too, so the share dialog opens
  // straight from the board without going through the card editor.
  const card = page
    .locator("[data-testid=checklist-board] .checklist-preview")
    .filter({ hasText: clName })
    .first();
  await expect(card).toBeVisible();
  await card.locator("[data-testid=share-button]").click();

  const dialog = page.locator("[role=dialog]");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("[data-testid=share-user-search]")).toBeVisible();
  await stabilize(page);

  await shootElement(dialog, "DesktopCompactShowShareMenu");
});
