import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";

// Permission-aware card UI gating (Frontend Phase F1).
//
// The card-read model carries `my_permission` (backend P0.1). A `view`
// collaborator must see the card but get read-only affordances; an `edit`
// collaborator gets full item/text editing (share-management/transfer is owner
// only — out of scope here). We drive this with the second provisioned user
// (testuser01) and create the share directly via the owner's API session,
// since the share-management UI doesn't exist until F2.

const TEST_USER = { username: "testuser01", password: "testuserpw_secure1" };

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

test.describe("F1 permission-aware gating", () => {
  test.setTimeout(30_000);

  const cleanupChecklists: string[] = [];
  let secondCtx: BrowserContext | null = null;

  test.afterEach(async ({ page }) => {
    // Owner (admin) session deletes the shared cards.
    for (const id of cleanupChecklists)
      await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanupChecklists.length = 0;

    // Never `await close()` on a context with an open SSE — navigate away first.
    if (secondCtx) {
      await Promise.all(secondCtx.pages().map((p) => p.goto("about:blank").catch(() => {})));
      await secondCtx.close().catch(() => {});
      secondCtx = null;
    }
  });

  // Shared setup: admin creates a card with one item and shares it with
  // testuser01 at the given level. Returns the card id and title.
  async function shareCardWithTestUser(page: Page, level: "view" | "check" | "edit") {
    const tag = Date.now();
    const title = `Shared-${level}-${tag}`;

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

    // Resolve testuser01's id via the user-search endpoint, then share.
    const results = await (
      await page.request.get("/api/user/search", { params: { q: TEST_USER.username } })
    ).json();
    const target = results.find((u: any) => u.user_name === TEST_USER.username) ?? results[0];
    expect(target, "testuser01 should be findable via user search").toBeTruthy();

    const shareRes = await page.request.put(`/api/checklist/${cl.id}/shares/${target.id}`, {
      data: { permission: level },
      headers: { "Content-Type": "application/json" },
    });
    expect(shareRes.ok(), `sharing at ${level} should succeed`).toBeTruthy();

    return { id: cl.id, title };
  }

  // Opens the shared card in testuser01's board and returns the editor locator.
  // The collaborator lands on "/" straight from login, so the board is already
  // mounted — re-navigating spawns a second overlay instance.
  async function openSharedCard(userPage: Page, title: string) {
    await userPage.waitForSelector("[data-testid=checklist-board]");
    const card = userPage.locator(".checklist-preview").filter({ hasText: title });
    await expect(card, "shared card should appear in the collaborator's grid").toBeVisible({
      timeout: 8_000,
    });
    await card.locator(".text-lg.font-semibold").click();
    // Identify the card editor by its content (the `.checklist` container only
    // the editor renders) and scope assertions to it. `.first()` is belt-and-
    // suspenders against any transient duplicate overlay root.
    const dialog = userPage.locator('[role="dialog"]:has(.checklist)').first();
    await expect(dialog).toBeVisible({ timeout: 5_000 });
    await expect(dialog.locator("li textarea").first()).toBeVisible({ timeout: 5_000 });
    return dialog;
  }

  test("view collaborator gets a read-only card", async ({ page, browser }) => {
    const { title } = await shareCardWithTestUser(page, "view");

    const { ctx, page: userPage } = await loginAsTestUser(browser);
    secondCtx = ctx;

    const dialog = await openSharedCard(userPage, title);

    // Checkbox cannot be toggled. (Nuxt UI renders a <button role="checkbox">;
    // Playwright's getByRole misses it, so match the attribute directly.)
    await expect(dialog.locator('[role="checkbox"]').first()).toBeDisabled();
    // Item text is read-only.
    await expect(dialog.locator("li textarea").first()).toBeDisabled();
    // No "add new item" affordance.
    await expect(dialog.getByText("Add New Checklist Item")).toHaveCount(0);

    await userPage.keyboard.press("Escape");
  });

  test("edit collaborator can modify items", async ({ page, browser }) => {
    const { title } = await shareCardWithTestUser(page, "edit");

    const { ctx, page: userPage } = await loginAsTestUser(browser);
    secondCtx = ctx;

    const dialog = await openSharedCard(userPage, title);

    // Full item editing is available.
    await expect(dialog.locator('[role="checkbox"]').first()).toBeEnabled();
    await expect(dialog.locator("li textarea").first()).toBeEnabled();
    await expect(dialog.getByText("Add New Checklist Item")).toBeVisible();

    await userPage.keyboard.press("Escape");
  });
});
