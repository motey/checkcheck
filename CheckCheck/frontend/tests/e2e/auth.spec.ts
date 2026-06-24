/**
 * Tests that exercise the authentication flow (unauthenticated context).
 *
 * These run inside the "chromium" project which normally pre-loads stored
 * auth state.  Each test overrides that with an empty state so the browser
 * starts fresh, letting us verify the login page and its error paths.
 */
import { test, expect } from "@playwright/test";

// Clear any stored auth so these tests always start unauthenticated.
test.use({ storageState: { cookies: [], origins: [] } });

// Navigate away after every test so any open SSE connection (/api/sync) is
// aborted before Playwright closes the page.  Without this, tests that end on
// the board page block the worker indefinitely during page teardown.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

test.describe("login page", () => {
  test("is accessible and shows the Login heading", async ({ page }) => {
    await page.goto("/login");
    // Wait for the page to fully render before asserting (the heading is inside UCard)
    await page.waitForSelector("form");
    await expect(page.getByRole("heading", { name: "Login", exact: true })).toBeVisible();
  });

  test("shows the basic-auth form after auth methods load", async ({ page }) => {
    await page.goto("/login");
    await page.waitForSelector("form");
    await expect(page.locator("[data-testid=login-username]")).toBeVisible();
    await expect(page.locator("[data-testid=login-password]")).toBeVisible();
    await expect(page.locator('form button[type="submit"]')).toBeVisible();
  });

  test("shows an error message for invalid credentials", async ({ page }) => {
    await page.goto("/login");
    await page.waitForSelector("form");

    await page.locator("[data-testid=login-username]").fill("nobody");
    await page.locator("[data-testid=login-password]").fill("wrongpassword");
    await page.locator('form button[type="submit"]').click();

    // UAlert in Nuxt UI 4 renders without role="alert"; target via data-testid.
    // The alert must also render the detail message text (regression: the
    // error ref used to be mis-named so the description was swallowed).
    const alert = page.locator("[data-testid=login-error]");
    await expect(alert).toBeVisible({ timeout: 5_000 });
    await expect(alert).not.toHaveText(/^\s*$/);
  });

  test("shows a fallback error when auth methods fail to load", async ({ page }) => {
    // Abort the auth-methods fetch so the onMounted handler's catch fires.
    // This path was previously dead (it referenced an undefined `error` ref
    // and threw instead of surfacing the message).
    await page.route("**/api/auth/list", (route) => route.abort());

    await page.goto("/login");
    await expect(page.locator("[data-testid=login-error]")).toBeVisible({ timeout: 5_000 });
    await expect(page.locator("[data-testid=login-error]")).toContainText("Failed to load login options.");
  });

  test("redirects to / after a successful login", async ({ page }) => {
    await page.goto("/login");
    await page.waitForSelector("form");

    await page.locator("[data-testid=login-username]").fill("admin3");
    await page.locator("[data-testid=login-password]").fill("password123");
    await page.locator('form button[type="submit"]').click();

    await page.waitForURL("/", { timeout: 10_000 });
    await expect(page).toHaveURL("/");
  });
});

test.describe("logout", () => {
  // Log in as testuser01 (not admin3) so this test does not invalidate the
  // admin3 session that checklist.spec.ts depends on.  Both spec files run
  // in parallel on separate workers and share the same backend.
  test("logout button signs the user out and redirects to /login", async ({ page }) => {
    await page.goto("/login");
    await page.waitForSelector("form");
    await page.locator("[data-testid=login-username]").fill("testuser01");
    await page.locator("[data-testid=login-password]").fill("testuserpw_secure1");
    await page.locator('form button[type="submit"]').click();
    await page.waitForURL("/", { timeout: 10_000 });

    // Logout now lives inside the avatar user menu (UDropdownMenu items
    // teleport to the body, so the trigger is opened first and the item is
    // located at the page level by its testid).
    await page.locator("[data-testid=user-menu]").click();
    await page.locator("[data-testid=logout-button]").click();
    await page.waitForURL("/login", { timeout: 10_000 });
    await expect(page).toHaveURL("/login");
  });
});
