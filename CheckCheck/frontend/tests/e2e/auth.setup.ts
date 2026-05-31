/**
 * Logs in as the admin user once and persists the browser session to
 * tests/e2e/.auth/state.json so every test in the "chromium" project can
 * start already authenticated without repeating the login flow.
 */
import { test as setup, expect } from "@playwright/test";
import { resolve } from "path";

const AUTH_STATE_FILE = resolve(__dirname, ".auth/state.json");

// Credentials match start_e2e_server.py / provisioning_data/test_users.yaml
export const ADMIN = { username: "admin3", password: "password123" };
export const TEST_USER = { username: "testuser01", password: "testuserpw_secure1" };

setup("persist admin auth state", async ({ page }) => {
  await page.goto("/login");

  // The page first fetches /api/auth/list; wait for the form to render.
  await page.waitForSelector("form");

  await page.locator("[data-testid=login-username]").fill(ADMIN.username);
  await page.locator("[data-testid=login-password]").fill(ADMIN.password);
  await page.locator('form button[type="submit"]').click();

  await page.waitForURL("/");
  await expect(page).toHaveURL("/");

  await page.context().storageState({ path: AUTH_STATE_FILE });
});
