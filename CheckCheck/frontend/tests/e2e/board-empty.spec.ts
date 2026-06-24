/**
 * Board empty-state tests (Phase 3).
 *
 * The board renders one of three friendly empty states when there is nothing to
 * show:
 *   - board-empty         → a brand-new user with zero lists (CTA to create one)
 *   - board-empty-search  → a text search that matches nothing
 *   - (board-empty-shared → an empty "shared" view; not asserted here)
 * and they disappear once cards / results exist.
 *
 * The default suite runs as the shared admin user whose board other specs
 * populate, so the "no lists at all" case uses its OWN freshly-created user (via
 * the admin user-management API) in a separate browser context — never the
 * shared admin board. The search case creates one owned card so the board is
 * known to be non-empty, then searches for a token that can't match.
 */
import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";

// admin3 is the ADMIN_USER for the E2E backend (see start_e2e_server.py) and is
// therefore allowed to create users via the user-management API.
const ADMIN_PW = "password123";

// Close the board's SSE (/api/sync) before Playwright tears down each page.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

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

// Admin creates a fresh local user with a password; returns its credentials.
async function createFreshUser(adminPage: Page) {
  const tag = `${Date.now()}${Math.floor(Math.random() * 1000)}`;
  const userName = `empty.${tag}`;
  const password = "freshpw_secure1";
  const res = await adminPage.request.post(`/api/user?user_password=${password}`, {
    data: { user_name: userName, email: `${userName}@example.com` },
    headers: { "Content-Type": "application/json" },
  });
  expect(res.ok(), `create user failed: ${res.status()}`).toBeTruthy();
  return { userName, password };
}

// Logs the given credentials into a fresh browser context.
async function loginInNewContext(
  browser: Browser,
  creds: { userName: string; password: string }
): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto("/login");
  await page.waitForSelector("form");
  await page.locator("[data-testid=login-username]").fill(creds.userName);
  await page.locator("[data-testid=login-password]").fill(creds.password);
  await page.locator('form button[type="submit"]').click();
  await page.waitForURL("/");
  return { ctx, page };
}

// ── tests ─────────────────────────────────────────────────────────────────────

test.describe("board empty states", () => {
  test.setTimeout(30_000);

  let freshCtx: BrowserContext | null = null;

  test.afterEach(async () => {
    if (freshCtx) {
      await Promise.all(freshCtx.pages().map((p) => p.goto("about:blank").catch(() => {})));
      await freshCtx.close().catch(() => {});
      freshCtx = null;
    }
  });

  test("a brand-new user sees board-empty with a working CTA", async ({ page, browser }) => {
    // `page` is the admin context — use it only to mint an isolated user.
    const creds = await createFreshUser(page);
    const { ctx, page: userPage } = await loginInNewContext(browser, creds);
    freshCtx = ctx;

    // Empty board → the "create your first list" state, not the search variant.
    await expect(userPage.locator("[data-testid=board-empty]")).toBeVisible({ timeout: 10_000 });
    await expect(userPage.locator("[data-testid=board-empty-search]")).not.toBeVisible();
    const cta = userPage.locator("[data-testid=board-empty-cta]");
    await expect(cta).toBeVisible();

    // The CTA creates a list and opens it in the card editor.
    await cta.click();
    await expect(userPage.locator('[role="dialog"]')).toBeVisible({ timeout: 5_000 });

    // Once a list exists the empty state is gone.
    await expect(userPage.locator("[data-testid=board-empty]")).not.toBeVisible();

    // Clean up the list this just created (id is in the /card/<id> URL).
    const match = userPage.url().match(/\/card\/([0-9a-fA-F-]+)/);
    if (match) await apiDelete(userPage, `/api/checklist/${match[1]}`);
  });

  test("a search with no matches shows board-empty-search and clears", async ({ page }) => {
    const cleanup: string[] = [];
    try {
      const tag = Date.now();
      const name = `EmptyStateCard-${tag}`;
      const cl = await apiPost(page, "/api/checklist", { name });
      cleanup.push(cl.id);

      await page.goto("/");
      await page.waitForSelector("[data-testid=checklist-board]");
      await expect(page.getByText(name)).toBeVisible();

      // Search for a token that cannot match anything → no-results state.
      const noMatch = `zzz-no-such-list-${tag}`;
      const searchInput = page.locator("[data-testid=search-input]");
      await searchInput.fill(noMatch);

      await expect(page).toHaveURL(/search=/, { timeout: 2_000 });
      const emptySearch = page.locator("[data-testid=board-empty-search]");
      await expect(emptySearch).toBeVisible({ timeout: 5_000 });
      await expect(emptySearch).toContainText(noMatch);
      // Not the new-user variant.
      await expect(page.locator("[data-testid=board-empty]")).not.toBeVisible();

      // Clearing the search restores the board; the empty state disappears.
      await searchInput.fill("");
      await searchInput.press("Enter");
      await expect(page).not.toHaveURL(/search=/, { timeout: 2_000 });
      await expect(emptySearch).not.toBeVisible({ timeout: 5_000 });
      await expect(page.getByText(name)).toBeVisible({ timeout: 5_000 });
    } finally {
      for (const id of cleanup) await apiDelete(page, `/api/checklist/${id}`);
    }
  });
});
