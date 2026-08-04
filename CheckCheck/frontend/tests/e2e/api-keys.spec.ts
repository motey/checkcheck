import { test, expect } from "@playwright/test";

// Self-service API-key manager (Improvements 2026-07, Chunk 2).
//
// The user avatar menu (components/Navbar.vue) has an "API keys" item that opens
// components/ApiKeysModal.vue. The modal lists the caller's keys, creates a new
// one (showing the plaintext token exactly once, copyable), and revokes keys via
// a small inline confirm. Backend is self-service under /api/user/me/api-keys.

// REQUIRED: the board opens a persistent SSE connection (/api/sync) that blocks
// Playwright teardown if left open. Navigate to about:blank to close it.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

test.describe("Chunk 2 API-key manager", () => {
  // Names created during a run, cleaned up through the API afterwards.
  const createdNames: string[] = [];

  test.afterEach(async ({ page }) => {
    if (!createdNames.length) return;
    const keys = await page.request
      .get("/api/user/me/api-keys")
      .then((r) => (r.ok() ? r.json() : []))
      .catch(() => []);
    for (const key of keys as Array<{ api_token_id: string; display_name?: string }>) {
      if (key.display_name && createdNames.includes(key.display_name)) {
        await page.request
          .delete(`/api/user/me/api-keys/${key.api_token_id}`)
          .catch(() => {});
      }
    }
    createdNames.length = 0;
  });

  async function openApiKeysModal(page: import("@playwright/test").Page) {
    const dialog = page.locator('[role="dialog"]').filter({ hasText: "API keys" });
    // Since S1 the pane is a place, so a reload of /settings/api-keys brings it
    // back on its own and there is no user menu to reach behind it. Decided on
    // the URL rather than on the dialog being visible: right after a reload it
    // is not painted yet, and a menu click would race the modal's own backdrop.
    if (!page.url().includes("/settings/api-keys")) {
      // A previous close may still be in flight: its backdrop swallows the menu
      // click, and reopening before the router has settled the closing URL makes
      // the open a redundant navigation the router drops. Wait for both halves
      // of "closed" (no dialog, no pane in the URL) before reaching for the menu.
      await expect(dialog).toBeHidden();
      await expect(page).not.toHaveURL(/\/settings\/api-keys/);
      await page.locator("[data-testid=user-menu]").click();
      // Dropdown items teleport to the body, so locate at page level.
      await page.locator("[data-testid=menu-api-keys]").click();
    }
    await expect(dialog).toBeVisible({ timeout: 5_000 });
    // Opening must leave a URL behind, or the back button and a shared link both
    // stop working.
    await expect(page).toHaveURL(/\/settings\/api-keys$/);
    return dialog;
  }

  test("create shows a one-time token, survives reload, and revokes", async ({ page }) => {
    // Assert the copy button's success state without a flaky clipboard read.
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);

    const name = `e2e-key-${Date.now()}`;
    createdNames.push(name);

    await page.goto("/");
    let dialog = await openApiKeysModal(page);

    // Create a key.
    await dialog.locator("[data-testid=api-key-name-input]").fill(name);
    await dialog.locator("[data-testid=api-key-create]").click();

    // The plaintext token is surfaced exactly once.
    const tokenField = dialog.locator("[data-testid=api-key-token]");
    await expect(tokenField, "the one-time token should appear").toBeVisible({ timeout: 5_000 });
    const token = await tokenField.inputValue();
    expect(token.length).toBeGreaterThan(0);

    // Copy works (icon flips to a checkmark on success).
    await dialog.locator("[data-testid=api-key-copy]").click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(token);

    // The key now shows in the list.
    const row = dialog.locator("[data-testid=api-key-row]").filter({ hasText: name });
    await expect(row).toHaveCount(1);
    // The list must never render the plaintext token.
    await expect(dialog.locator("[data-testid=api-key-row]")).not.toContainText(token);

    // Reload: the one-time token box is gone, but the key is still listed.
    await page.reload();
    dialog = await openApiKeysModal(page);
    await expect(dialog.locator("[data-testid=api-key-token]")).toHaveCount(0);
    const rowAfter = dialog.locator("[data-testid=api-key-row]").filter({ hasText: name });
    await expect(rowAfter).toHaveCount(1);

    // Revoke it (inline confirm: trash → Yes).
    await rowAfter.locator("[data-testid=api-key-revoke]").click();
    await rowAfter.locator("[data-testid=api-key-revoke-confirm]").click();
    await expect(rowAfter, "row should disappear after revoke").toHaveCount(0, { timeout: 5_000 });

    // No surprise 4xx surfaced through the central toast handler.
    await expect(page.getByText(/Error 4\d\d/)).toHaveCount(0);
  });

  test("expiry offers a concrete default (not 'server default') and a Never option", async ({ page }) => {
    const name = `e2e-never-${Date.now()}`;
    createdNames.push(name);

    await page.goto("/");
    const dialog = await openApiKeysModal(page);

    // The abstract "Server default" wording is gone — the real default duration
    // is surfaced and pre-selected instead.
    const expirySelect = dialog.locator("[data-testid=api-key-expiry]");
    await expect(expirySelect).not.toContainText("Server default");

    // Open the dropdown and pick "Never expires" (the server default config
    // allows never-expiring keys in the e2e server).
    await expirySelect.click();
    await page.getByRole("option", { name: "Never expires" }).click();

    await dialog.locator("[data-testid=api-key-name-input]").fill(name);
    await dialog.locator("[data-testid=api-key-create]").click();
    await expect(dialog.locator("[data-testid=api-key-token]")).toBeVisible({ timeout: 5_000 });

    // The backend must have stored the key with no expiry.
    const keys = await page.request
      .get("/api/user/me/api-keys")
      .then((r) => r.json());
    const created = (keys as Array<{ display_name?: string; expires_at_epoch_time: number | null }>).find(
      (k) => k.display_name === name
    );
    expect(created, "the never-expiring key should exist").toBeTruthy();
    expect(created!.expires_at_epoch_time).toBeNull();

    await expect(page.getByText(/Error 4\d\d/)).toHaveCount(0);
  });

  test("S1: the pane is a place: a cold URL opens it, and closing goes back to the board", async ({
    page,
  }) => {
    // A link somebody was sent, or a reload: nothing clicked a menu here.
    await page.goto("/settings/api-keys");
    const dialog = page.locator('[role="dialog"]').filter({ hasText: "API keys" });
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    // On top of a real board, not instead of it: the pane is an overlay.
    await expect(page.locator("[data-testid=user-menu]")).toBeAttached();
    // And it is loaded, not an empty shell: the cold mount has to fetch too.
    await expect(dialog.locator("[data-testid=api-key-name-input]")).toBeVisible({
      timeout: 10_000,
    });

    // Escape closes it, and the URL goes with it. A dialog that closes while the
    // URL still names it cannot be reopened without navigating away first.
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden({ timeout: 5_000 });
    await expect(page).toHaveURL(/\/$/);

    // Reopening from the menu still works, which is what the stale-URL bug broke.
    await openApiKeysModal(page);
  });
});
