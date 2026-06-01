/**
 * Basic smoke tests for the checklist board.
 * These run in the "chromium" project which pre-loads the saved admin session,
 * so no login step is needed here.
 */
import { test, expect } from "@playwright/test";

// Navigate away after every test so the board's SSE (/api/sync EventSource) is
// closed before Playwright tears down the page.  Without this the browser hangs
// waiting for the stream to settle during browser teardown.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

test.describe("checklist board", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("renders the board grid", async ({ page }) => {
    await expect(page.locator("[data-testid=checklist-board]")).toBeVisible();
  });

  test('"New Check List" button is visible in the navbar', async ({ page }) => {
    await expect(
      page.getByRole("button", { name: "New Check List" })
    ).toBeVisible();
  });

  test("search input is visible", async ({ page }) => {
    await expect(page.getByPlaceholder("Search...")).toBeVisible();
  });

  test("clicking New Check List opens the editor modal", async ({ page }) => {
    await page.getByRole("button", { name: "New Check List" }).click();
    // CheckListEditModal wraps a UModal which renders with role="dialog"
    await expect(page.locator('[role="dialog"]')).toBeVisible({ timeout: 5_000 });
  });

  test("filtering by search query updates the URL", async ({ page }) => {
    const searchInput = page.getByPlaceholder("Search...");
    await searchInput.fill("my test query");
    await expect(page).toHaveURL(/search=my\+test\+query|search=my%20test%20query/, {
      timeout: 2_000,
    });
  });
});
