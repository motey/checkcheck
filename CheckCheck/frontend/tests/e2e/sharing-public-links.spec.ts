import { test, expect, type Page } from "@playwright/test";

// Public-URL link management (Frontend Phase F3).
//
// Inside the owner's ShareModal there's a "Public links" section
// (components/ShareModal/PublicLinks.vue), gated server-side on
// SHARING_PUBLIC_LINKS_ENABLED (default ON in the e2e server). The owner can
// create anonymous links (level / optional expiry / optional password), is shown
// the full /p/<token> URL exactly once, sees redacted links in a list, can toggle
// them enabled/disabled, and delete them. Tokens are never re-fetchable.
//
// The footer renders on the grid *preview* card, so we drive the dialog straight
// from the board — sidestepping the known double-[role=dialog] flake.

// REQUIRED: the board opens a persistent SSE connection (/api/sync) that blocks
// Playwright teardown if left open. Navigate to about:blank to close it.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

test.describe("F3 public-link management", () => {
  test.setTimeout(30_000);

  const cleanupChecklists: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanupChecklists)
      await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanupChecklists.length = 0;
  });

  async function createCard(page: Page): Promise<{ id: string; title: string }> {
    const tag = Date.now();
    const title = `PublicLinks-${tag}`;
    const cl = await (
      await page.request.post("/api/checklist", {
        data: { name: title },
        headers: { "Content-Type": "application/json" },
      })
    ).json();
    cleanupChecklists.push(cl.id);
    await page.request.post(`/api/checklist/${cl.id}/item`, {
      data: { text: `Item-${tag}` },
      headers: { "Content-Type": "application/json" },
    });
    return { id: cl.id, title };
  }

  async function openShareModal(page: Page, title: string) {
    await page.waitForSelector("[data-testid=checklist-board]");
    const card = page.locator(".checklist-preview").filter({ hasText: title });
    await expect(card, "card should be on the board").toBeVisible({ timeout: 8_000 });
    await card.locator("[data-testid=share-button]").click();
  }

  test("owner creates a link, sees the URL once, toggles and deletes it", async ({ page }) => {
    const { title } = await createCard(page);
    // Lets us assert the copy button's success state without a flaky clipboard read.
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);

    await page.goto("/");
    await openShareModal(page, title);

    const dialog = page.locator('[role="dialog"]').filter({ hasText: "Share this list" });
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // The Public links section starts empty.
    await expect(dialog.getByText("No public links yet.")).toBeVisible();

    // Create a link at the default (view) level.
    await dialog.locator("[data-testid=public-link-create]").click();

    // The full shareable URL is surfaced exactly once.
    const urlField = dialog.locator("[data-testid=public-link-url]");
    await expect(urlField, "the one-time URL should appear").toBeVisible({ timeout: 5_000 });
    const url = await urlField.inputValue();
    expect(url).toContain("/p/");
    expect(url.length).toBeGreaterThan(`${new URL(url).origin}/p/`.length);

    // Copy works (icon flips to a checkmark on success).
    await dialog.locator("[data-testid=public-link-copy]").click();
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toBe(url);

    // The link now shows in the list (redacted — no token rendered as text).
    const row = dialog.locator("[data-testid=public-link-row]");
    await expect(row).toHaveCount(1);
    await expect(row).not.toContainText(new URL(url).pathname.replace("/p/", ""));

    // A link created this session stays copyable from its row (the token is held
    // in memory). Dirty the clipboard first so the assertion is meaningful.
    await page.evaluate(() => navigator.clipboard.writeText("dirty"));
    await row.locator("[data-testid=public-link-row-copy]").click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(url);

    // Toggle disabled then back enabled (no error toast on either).
    const toggle = row.locator("[data-testid=public-link-toggle]");
    await toggle.click();
    await toggle.click();

    // Delete it.
    await row.locator("[data-testid=public-link-delete]").click();
    await expect(row, "row should disappear after delete").toHaveCount(0, { timeout: 5_000 });
    await expect(dialog.getByText("No public links yet.")).toBeVisible();

    // No surprise 4xx surfaced through the central toast handler.
    await expect(page.getByText(/Error 4\d\d/)).toHaveCount(0);

    await page.keyboard.press("Escape");
  });

  test("a password-protected link with an expiry shows the lock + expiry", async ({ page }) => {
    const { title } = await createCard(page);

    await page.goto("/");
    await openShareModal(page, title);

    const dialog = page.locator('[role="dialog"]').filter({ hasText: "Share this list" });
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // Pick the check level, set a future expiry and a passphrase.
    const future = new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString().slice(0, 10);
    await dialog.locator("[data-testid=public-link-expiry]").fill(future);
    await dialog.locator("[data-testid=public-link-password]").fill("hunter2");
    await dialog.locator("[data-testid=public-link-create]").click();

    const row = dialog.locator("[data-testid=public-link-row]");
    await expect(row).toHaveCount(1, { timeout: 5_000 });
    // 🔒 indicator for password protection, and a non-"Never" expiry line.
    await expect(row).toContainText("🔒");
    await expect(row).toContainText("Expires");
    await expect(row).not.toContainText("Never expires");

    await expect(page.getByText(/Error 4\d\d/)).toHaveCount(0);

    // Clean the link up through the UI too.
    await row.locator("[data-testid=public-link-delete]").click();
    await expect(row).toHaveCount(0, { timeout: 5_000 });

    await page.keyboard.press("Escape");
  });

  // ── link names (issue #9) ────────────────────────────────────────────────
  //
  // Several links on one card used to be literally indistinguishable: same
  // badge, same "Never expires", token redacted. The name is what tells them
  // apart, and the automatic one is what stops an unnamed link from being a
  // blank row.

  test("a link takes the name it was given, and names itself when not", async ({ page }) => {
    const { title } = await createCard(page);

    await page.goto("/");
    await openShareModal(page, title);

    const dialog = page.locator('[role="dialog"]').filter({ hasText: "Share this list" });
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    await dialog.locator("[data-testid=public-link-name]").fill("Groceries");
    await dialog.locator("[data-testid=public-link-create]").click();

    const names = dialog.locator("[data-testid=public-link-row-name]");
    await expect(names).toHaveText(["Groceries"], { timeout: 5_000 });
    // The one moment the URL is on screen says which link it belongs to.
    await expect(dialog.locator("[data-testid=public-link-fresh-name]")).toContainText(
      'Copy the link for "Groceries" now'
    );
    // The field is cleared, so the next link does not inherit the last name.
    await expect(dialog.locator("[data-testid=public-link-name]")).toHaveValue("");

    // Two more without a name: the server numbers them from the highest it
    // finds, and a named link is not part of that numbering.
    await dialog.locator("[data-testid=public-link-create]").click();
    await expect(names).toHaveCount(2, { timeout: 5_000 });
    await dialog.locator("[data-testid=public-link-create]").click();
    await expect(names).toHaveCount(3, { timeout: 5_000 });
    // Newest first in the list.
    await expect(names).toHaveText(["Link-2", "Link-1", "Groceries"]);

    await expect(page.getByText(/Error 4\d\d/)).toHaveCount(0);
    await page.keyboard.press("Escape");
  });

  test("a link can be renamed in place, and blanking it gets an automatic name back", async ({
    page,
  }) => {
    const { title } = await createCard(page);

    await page.goto("/");
    await openShareModal(page, title);

    let dialog = page.locator('[role="dialog"]').filter({ hasText: "Share this list" });
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    await dialog.locator("[data-testid=public-link-create]").click();
    const name = dialog.locator("[data-testid=public-link-row-name]");
    await expect(name).toHaveText("Link-1", { timeout: 5_000 });

    // Escape abandons an edit rather than committing it.
    await name.click();
    let field = dialog.locator("[data-testid=public-link-row-name-input]");
    await expect(field).toBeFocused();
    await field.fill("Typed by mistake");
    await field.press("Escape");
    await expect(name).toHaveText("Link-1");

    // Click the name to swap in the input, type over the selected text, commit
    // with Enter.
    await name.click();
    field = dialog.locator("[data-testid=public-link-row-name-input]");
    await expect(field).toBeFocused();
    await page.keyboard.type("Contractors");
    await page.keyboard.press("Enter");
    await expect(name).toHaveText("Contractors", { timeout: 5_000 });

    // The rename is on the server, not just in this dialog's state.
    await page.keyboard.press("Escape");
    await page.goto("/");
    await openShareModal(page, title);
    dialog = page.locator('[role="dialog"]').filter({ hasText: "Share this list" });
    await expect(dialog.locator("[data-testid=public-link-row-name]")).toHaveText("Contractors", {
      timeout: 5_000,
    });

    // Blanking it is "I do not want to call it anything", which the server
    // answers with a fresh automatic name rather than a nameless row.
    const renamed = dialog.locator("[data-testid=public-link-row-name]");
    await renamed.click();
    const blank = dialog.locator("[data-testid=public-link-row-name-input]");
    await blank.fill("");
    await blank.press("Enter");
    await expect(renamed).toHaveText("Link-1", { timeout: 5_000 });

    await expect(page.getByText(/Error 4\d\d/)).toHaveCount(0);

    await dialog.locator("[data-testid=public-link-delete]").click();
    await expect(dialog.locator("[data-testid=public-link-row]")).toHaveCount(0, {
      timeout: 5_000,
    });
    await page.keyboard.press("Escape");
  });
});
