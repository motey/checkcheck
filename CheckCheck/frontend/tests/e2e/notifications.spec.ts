import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";

// Notifications feed (Frontend Phase F5).
//
// A navbar bell (data-testid=notification-bell) shows an unread badge
// (data-testid=notification-bell-chip) and a dropdown feed
// (data-testid=notification-panel) of rows (data-testid=notification-row). When
// the owner shares a card with a user, the backend emits a `notification` SSE
// event to that user, which bumps their bell live.
//
// The default `page` fixture is the admin (the sharing owner here); the
// recipient (testuser01) logs in in a fresh context so we can watch their bell
// update live over SSE.

const TEST_USER = { username: "testuser01", password: "testuserpw_secure1" };

// REQUIRED: the board opens a persistent SSE connection (/api/sync) that blocks
// Playwright teardown if left open. Navigate every page to about:blank.
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

test.describe("F5 notifications feed", () => {
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

  // Admin (the `page` fixture) creates a card and returns its id + title.
  async function createCard(page: Page): Promise<{ id: string; title: string }> {
    const tag = Date.now();
    const title = `Notify-${tag}`;
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

  // Admin shares the card with testuser01 over the API → fans out a
  // `notification` SSE event to testuser01.
  async function shareWithTestUser(page: Page, clId: string, level: "view" | "check" | "edit" = "edit") {
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

  test("bell badge updates live on share and mark-all-read clears it", async ({ page, browser }) => {
    // Recipient's board must be open (SSE connected) BEFORE we share.
    const { ctx, page: userPage } = await loginAsTestUser(browser);
    secondCtx = ctx;
    await userPage.waitForSelector("[data-testid=notification-bell]");

    const chip = userPage.locator("[data-testid=notification-bell-chip]");
    // No unread badge to start with for this card.
    await expect(chip).not.toContainText("1");

    const { id } = await createCard(page);
    await shareWithTestUser(page, id);

    // The `notification` SSE event bumps the badge live.
    await expect(chip, "unread badge should appear live").toContainText("1", { timeout: 8_000 });

    // Open the dropdown and see the row.
    await userPage.locator("[data-testid=notification-bell]").click();
    const panel = userPage.locator("[data-testid=notification-panel]");
    await expect(panel).toBeVisible({ timeout: 5_000 });
    await expect(panel.locator("[data-testid=notification-row]")).toHaveCount(1, { timeout: 5_000 });

    // Mark all read → badge clears.
    await panel.locator("[data-testid=notification-mark-all-read]").click();
    await expect(chip, "badge should clear after mark-all-read").not.toContainText("1", {
      timeout: 5_000,
    });

    // Happy path: no error toasts.
    await expect(userPage.getByText(/Error 4\d\d/)).toHaveCount(0);
  });

  test("clicking a card_shared row navigates to /card/<cl_id>", async ({ page, browser }) => {
    const { ctx, page: userPage } = await loginAsTestUser(browser);
    secondCtx = ctx;
    await userPage.waitForSelector("[data-testid=notification-bell]");

    const { id } = await createCard(page);
    await shareWithTestUser(page, id);

    const chip = userPage.locator("[data-testid=notification-bell-chip]");
    await expect(chip).toContainText("1", { timeout: 8_000 });

    await userPage.locator("[data-testid=notification-bell]").click();
    const panel = userPage.locator("[data-testid=notification-panel]");
    await expect(panel).toBeVisible({ timeout: 5_000 });

    // Clicking the row marks it read and opens the card overlay route.
    await panel.locator("[data-testid=notification-row]").first().click();
    await userPage.waitForURL(`**/card/${id}`, { timeout: 5_000 });
    expect(userPage.url()).toContain(`/card/${id}`);

    await expect(userPage.getByText(/Error 4\d\d/)).toHaveCount(0);
  });
});
