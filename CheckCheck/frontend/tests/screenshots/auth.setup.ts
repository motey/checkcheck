/**
 * Logs in as the seeded admin account once and persists the session so every
 * screenshot spec starts on an authenticated board.
 *
 * Credentials match CheckCheck/backend/screenshots/start_screenshot_server.py,
 * which provisions from the same test_users.yaml the E2E harness uses.
 */
import { test as setup, expect } from "@playwright/test";
import { resolve } from "path";

const AUTH_STATE_FILE = resolve(__dirname, ".auth/state.json");

export const ADMIN = { username: "admin3", password: "password123" };

setup("persist admin auth state", async ({ page }) => {
  await page.goto("/login");
  await page.waitForSelector("form");

  await page.locator("[data-testid=login-username]").fill(ADMIN.username);
  await page.locator("[data-testid=login-password]").fill(ADMIN.password);
  await page.locator('form button[type="submit"]').click();

  await page.waitForURL("/");
  await expect(page).toHaveURL("/");

  await page.context().storageState({ path: AUTH_STATE_FILE });
});
