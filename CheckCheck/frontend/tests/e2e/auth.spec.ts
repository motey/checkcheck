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

    // UAlert in Nuxt UI 4 renders without role="alert"; target via data-testid
    await expect(page.locator("[data-testid=login-error]")).toBeVisible({ timeout: 5_000 });
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

    await page.getByRole("button", { name: "Logout" }).click();
    await page.waitForURL("/login", { timeout: 10_000 });
    await expect(page).toHaveURL("/login");
  });
});
