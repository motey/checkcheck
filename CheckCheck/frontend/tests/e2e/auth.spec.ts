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

test.describe("login page", () => {
  test("is accessible and shows the Login heading", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Login" })).toBeVisible();
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

    // The UAlert error banner should appear
    await expect(page.locator('[role="alert"]')).toBeVisible({ timeout: 5_000 });
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
  // This test needs to start authenticated, so we restore the saved state.
  test.use({ storageState: "tests/e2e/.auth/state.json" });

  test("logout button signs the user out and redirects to /login", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Logout" }).click();
    await page.waitForURL("/login", { timeout: 10_000 });
    await expect(page).toHaveURL("/login");
  });
});
