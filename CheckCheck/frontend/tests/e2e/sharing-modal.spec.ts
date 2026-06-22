import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";

// Share-management dialog (Frontend Phase F2).
//
// The footer "Collaborate" button (data-testid=share-button) opens a permission-
// scoped ShareModal. The owner can add/remove collaborators; a non-owner sees a
// read-only collaborator list and a prominent "Leave list" action.
//
// The footer renders on the grid *preview* card too, so we drive the dialog
// straight from the board — no need to open the card editor (which trips the
// known double-[role=dialog] flake). The dialog itself is the only dialog on
// screen here, so scoping by its title text is safe.

const TEST_USER = { username: "testuser01", password: "testuserpw_secure1" };
// testuser01 has a display_name, so collaborator rows render it (not the username).
const TEST_USER_DISPLAY = "Test User 01";

// REQUIRED: the board opens a persistent SSE connection (/api/sync) that blocks
// Playwright teardown if left open. Navigate to about:blank to close it.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

async function loginAsTestUser(browser: Browser): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto("/login");
  await page.waitForSelector("form");
  await page.locator("[data-testid=login-username]").fill(TEST_USER.username);
  await page.locator("[data-testid=login-password]").fill(TEST_USER.password);
  await page.locator('form button[type="submit"]').click();
  await page.waitForURL("/");
  return { ctx, page };
}

test.describe("F2 share-management dialog", () => {
  test.setTimeout(30_000);

  const cleanupChecklists: string[] = [];
  let secondCtx: BrowserContext | null = null;

  test.afterEach(async ({ page }) => {
    for (const id of cleanupChecklists)
      await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanupChecklists.length = 0;

    if (secondCtx) {
      await Promise.all(secondCtx.pages().map((p) => p.goto("about:blank").catch(() => {})));
      await secondCtx.close().catch(() => {});
      secondCtx = null;
    }
  });

  // Admin creates a card (one item) and returns its id + title.
  async function createCard(page: Page): Promise<{ id: string; title: string }> {
    const tag = Date.now();
    const title = `ShareModal-${tag}`;
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

  async function shareWithTestUser(page: Page, clId: string, level: "view" | "check" | "edit") {
    const results = await (
      await page.request.get("/api/user/search", { params: { q: TEST_USER.username } })
    ).json();
    const target = results.find((u: any) => u.user_name === TEST_USER.username) ?? results[0];
    expect(target, "testuser01 should be findable").toBeTruthy();
    const res = await page.request.put(`/api/checklist/${clId}/shares/${target.id}`, {
      data: { permission: level },
      headers: { "Content-Type": "application/json" },
    });
    expect(res.ok()).toBeTruthy();
  }

  // Open the ShareModal from a card preview on the board.
  async function openShareModal(userPage: Page, title: string) {
    await userPage.waitForSelector("[data-testid=checklist-board]");
    const card = userPage.locator(".checklist-preview").filter({ hasText: title });
    await expect(card, "card should be on the board").toBeVisible({ timeout: 8_000 });
    await card.locator("[data-testid=share-button]").click();
  }

  test("owner can add and remove a collaborator via the dialog", async ({ page }) => {
    const { title } = await createCard(page);

    await page.goto("/");
    await openShareModal(page, title);

    const dialog = page.locator('[role="dialog"]').filter({ hasText: "Share this list" });
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // Search for testuser01 and add at the default (edit) level.
    await dialog.getByPlaceholder("Search by name or username").fill(TEST_USER.username);
    const addBtn = dialog.getByRole("button", { name: "Add" }).first();
    await expect(addBtn, "search result should appear").toBeVisible({ timeout: 5_000 });
    await addBtn.click();

    // The collaborator now appears in "People with access".
    const row = dialog
      .locator("[data-testid=share-collaborator-row]")
      .filter({ hasText: TEST_USER_DISPLAY });
    await expect(row, "collaborator row should appear").toBeVisible({ timeout: 5_000 });

    // Revoke them again.
    await row.getByRole("button", { name: "Remove" }).click();
    await expect(row, "collaborator row should disappear after revoke").toHaveCount(0, {
      timeout: 5_000,
    });

    await page.keyboard.press("Escape");
  });

  test("owner can transfer ownership and is demoted to a collaborator", async ({ page }) => {
    const { id, title } = await createCard(page);
    // testuser01 must be an accepted collaborator before they can become owner.
    await shareWithTestUser(page, id, "edit");

    await page.goto("/");
    await openShareModal(page, title);

    const dialog = page.locator('[role="dialog"]').filter({ hasText: "Share this list" });
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // Pick the new owner, then confirm the guarded action.
    await dialog.locator("[data-testid=share-transfer-select]").click();
    await page.getByRole("option", { name: TEST_USER_DISPLAY }).click();
    await dialog.getByRole("button", { name: "Transfer", exact: true }).click();
    await dialog.getByRole("button", { name: "Transfer ownership" }).click();

    // Success — and crucially NO error toast (the owner-only GET /shares must not
    // be called now that we've been demoted).
    await expect(page.getByText("Ownership transferred", { exact: true })).toBeVisible({
      timeout: 5_000,
    });
    await expect(page.getByText(/Error 4\d\d/)).toHaveCount(0);

    // The dialog swaps to the non-owner (collaborator) view — its title changes
    // from "Share this list" to "List collaborators", so assert on the unique
    // collaborator notice rather than the now-stale title-scoped locator.
    await expect(
      page.getByText("You're a collaborator on this list")
    ).toBeVisible({ timeout: 5_000 });

    await page.keyboard.press("Escape");
  });

  test("non-owner sees a read-only list and can leave the list", async ({ page, browser }) => {
    const { id, title } = await createCard(page);
    await shareWithTestUser(page, id, "edit");

    const { ctx, page: userPage } = await loginAsTestUser(browser);
    secondCtx = ctx;

    await openShareModal(userPage, title);

    // Non-owner gets the collaborator (read-only) view, not the owner view.
    const dialog = userPage.locator('[role="dialog"]').filter({ hasText: "List collaborators" });
    await expect(dialog).toBeVisible({ timeout: 5_000 });
    // No "Add people" search for a non-owner.
    await expect(dialog.getByPlaceholder("Search by name or username")).toHaveCount(0);

    // Leave the list → backend pins checklist_deleted, the card leaves the grid.
    await dialog.getByRole("button", { name: "Leave list" }).click();

    const card = userPage.locator(".checklist-preview").filter({ hasText: title });
    await expect(card, "card should be removed from the collaborator's grid").toHaveCount(0, {
      timeout: 8_000,
    });

    await userPage.keyboard.press("Escape");
  });
});
