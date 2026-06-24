/**
 * Mobile-viewport navbar chrome (Phase 2).
 *
 * The rest of the suite only runs at desktop width, so the responsive navbar
 * behaviour (hamburger, collapsed search, icon-only "New Check List") is
 * otherwise untested. This spec drives a phone-sized viewport and asserts the
 * mobile affordances appear and work.
 *
 * Runs in the authenticated "chromium" project (stored auth state).
 */
import { test, expect, type Page } from "@playwright/test";

const MOBILE = { width: 390, height: 844 };

// Close the board's SSE (/api/sync) before Playwright tears down each page.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

async function deleteChecklist(page: Page, id: string) {
  await page.request.delete(`/api/checklist/${id}`).catch(() => {});
}

test.describe("mobile navbar", () => {
  test.setTimeout(20_000);

  test("hamburger is visible at phone width", async ({ page }) => {
    await page.setViewportSize(MOBILE);
    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");

    await expect(page.getByRole("button", { name: "Open menu" })).toBeVisible();
  });

  test("search collapses behind a toggle and expands on tap", async ({ page }) => {
    await page.setViewportSize(MOBILE);
    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");

    // Collapsed: the input is hidden, only the toggle button shows.
    await expect(page.locator("[data-testid=search-input]")).not.toBeVisible();
    const toggle = page.locator("[data-testid=search-toggle]");
    await expect(toggle).toBeVisible();

    // Tapping the toggle reveals the input.
    await toggle.click();
    await expect(page.locator("[data-testid=search-input]")).toBeVisible();
  });

  test("the new-card button opens a fresh card editor", async ({ page }) => {
    await page.setViewportSize(MOBILE);
    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");

    const newCard = page.locator("[data-testid=new-card-button]");
    await expect(newCard).toBeVisible();
    await newCard.click();

    // CheckListEditModal wraps a UModal which renders with role="dialog".
    await expect(page.locator('[role="dialog"]')).toBeVisible({ timeout: 5_000 });

    // Clean up the checklist this just created (id is in the /card/<id> URL).
    const match = page.url().match(/\/card\/([0-9a-fA-F-]+)/);
    if (match) await deleteChecklist(page, match[1]);
  });
});
