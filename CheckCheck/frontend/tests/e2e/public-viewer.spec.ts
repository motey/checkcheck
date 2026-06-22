import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";

// Public/anonymous viewer page (Frontend Phase F4) — `pages/p/[token].vue`.
//
// A logged-out visitor opens `/p/<token>` and sees the card at the link's
// permission level, with live updates, an optional passphrase unlock, and a
// "log in to add to my deck" join. The page owns ALL 4xx handling: plugins/api.ts
// skips the global "Error 4xx" toast + the 401→/login redirect for /api/public
// requests, so a bad/locked link must surface the viewer's own state with ZERO
// error toasts and NO automatic /login bounce.
//
// Setup uses the admin (`page`, pre-authenticated) request context to mint cards
// + public links via the API; the anonymous viewer runs in a FRESH context with
// no storageState. The join-while-logged-in case reuses the testuser01 login
// pattern from sharing-modal.spec.ts.

const TEST_USER = { username: "testuser01", password: "testuserpw_secure1" };

// REQUIRED: every open page holds a live SSE (/api/sync) that blocks teardown.
// Navigate the admin page away; anonymous contexts are torn down per-test below.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

test.describe("F4 public viewer", () => {
  test.setTimeout(30_000);

  const cleanupChecklists: string[] = [];
  const anonContexts: BrowserContext[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanupChecklists)
      await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanupChecklists.length = 0;

    // Close anonymous/second contexts — navigate their pages away first so the
    // live SSE doesn't block close().
    for (const ctx of anonContexts) {
      await Promise.all(ctx.pages().map((p) => p.goto("about:blank").catch(() => {})));
      await ctx.close().catch(() => {});
    }
    anonContexts.length = 0;
  });

  // Admin creates a card (one item) and a public link at `level`. Returns the
  // card id/title, the one-time token, and the seed item text.
  async function createSharedLink(
    page: Page,
    level: "view" | "check" | "edit",
    opts: { password?: string; expires_at?: string } = {}
  ): Promise<{ id: string; title: string; token: string; itemText: string }> {
    const tag = Date.now() + Math.floor(Math.random() * 1000);
    const title = `Public-${level}-${tag}`;
    const itemText = `Item-${tag}`;

    const cl = await (
      await page.request.post("/api/checklist", {
        data: { name: title },
        headers: { "Content-Type": "application/json" },
      })
    ).json();
    cleanupChecklists.push(cl.id);

    await page.request.post(`/api/checklist/${cl.id}/item`, {
      data: { text: itemText },
      headers: { "Content-Type": "application/json" },
    });

    const linkRes = await page.request.post(`/api/checklist/${cl.id}/public-links`, {
      data: { permission: level, ...opts },
      headers: { "Content-Type": "application/json" },
    });
    expect(linkRes.ok(), "creating the public link should succeed").toBeTruthy();
    const link = await linkRes.json();
    expect(link.token, "create response should carry the one-time token").toBeTruthy();

    return { id: cl.id, title, token: link.token, itemText };
  }

  async function openAnon(browser: Browser, token: string): Promise<Page> {
    const ctx = await browser.newContext(); // NO storageState → logged-out visitor
    anonContexts.push(ctx);
    const anon = await ctx.newPage();
    await anon.goto(`/p/${token}`);
    return anon;
  }

  test("view-level link renders read-only for an anonymous visitor", async ({ page, browser }) => {
    const { token, title, itemText } = await createSharedLink(page, "view");

    const anon = await openAnon(browser, token);

    // Card + items render standalone (no board chrome).
    await expect(anon.locator("[data-testid=public-card-name]")).toHaveText(title);
    await expect(anon.locator("[data-testid=public-items]")).toContainText(itemText);

    // Read-only: checkbox disabled, no editable text, no add affordance.
    await expect(anon.locator('[role="checkbox"]').first()).toBeDisabled();
    await expect(anon.locator("[data-testid=public-item-text]")).toHaveCount(0);
    await expect(anon.locator("[data-testid=public-add-item]")).toHaveCount(0);

    // No global error toast slipped through for the public surface.
    await expect(anon.getByText(/Error 4\d\d/)).toHaveCount(0);
  });

  test("check-level link lets an anonymous visitor tick an item", async ({ page, browser }) => {
    const { token } = await createSharedLink(page, "check");

    const anon = await openAnon(browser, token);

    const checkbox = anon.locator('[role="checkbox"]').first();
    await expect(checkbox).toBeEnabled();
    // No edit affordances at the check level.
    await expect(anon.locator("[data-testid=public-add-item]")).toHaveCount(0);

    await checkbox.click();
    await expect(checkbox).toBeChecked({ timeout: 5_000 });

    await expect(anon.getByText(/Error 4\d\d/)).toHaveCount(0);
  });

  test("edit-level link lets an anonymous visitor add an item", async ({ page, browser }) => {
    const { token } = await createSharedLink(page, "edit");

    const anon = await openAnon(browser, token);

    // Edit affordances present; text rows are editable textareas.
    await expect(anon.locator('[role="checkbox"]').first()).toBeEnabled();
    await expect(anon.locator("[data-testid=public-item-text]")).toHaveCount(1);

    await anon.locator("[data-testid=public-add-item]").click();
    await expect(anon.locator("[data-testid=public-item-text]")).toHaveCount(2, { timeout: 5_000 });

    await expect(anon.getByText(/Error 4\d\d/)).toHaveCount(0);
  });

  test("a bad/disabled token shows the locked branch — no toast, no /login bounce", async ({
    browser,
  }) => {
    const ctx = await browser.newContext();
    anonContexts.push(ctx);
    const anon = await ctx.newPage();
    await anon.goto("/p/this-token-does-not-exist-1234567890");

    // The locked/passphrase branch is shown (can't distinguish bad from protected).
    await expect(anon.locator("[data-testid=public-locked]")).toBeVisible({ timeout: 5_000 });

    // CRITICAL: zero "Error 4xx" toasts and NO bounce to /login.
    await expect(anon.getByText(/Error 4\d\d/)).toHaveCount(0);
    expect(anon.url()).toContain("/p/this-token-does-not-exist");
    expect(anon.url()).not.toContain("/login");
  });

  test("password-protected link: wrong passphrase fails, right one unlocks", async ({
    page,
    browser,
  }) => {
    const { token, title } = await createSharedLink(page, "view", { password: "hunter2" });

    const anon = await openAnon(browser, token);

    // Protected → same 404 as a bad link → passphrase form.
    await expect(anon.locator("[data-testid=public-locked]")).toBeVisible({ timeout: 5_000 });

    // Wrong passphrase → error, still locked.
    await anon.locator("[data-testid=public-passphrase]").fill("wrong-pass");
    await anon.locator("[data-testid=public-unlock-submit]").click();
    await expect(anon.locator("[data-testid=public-unlock-error]")).toBeVisible({ timeout: 5_000 });
    await expect(anon.locator("[data-testid=public-card]")).toHaveCount(0);

    // Right passphrase → card loads.
    await anon.locator("[data-testid=public-passphrase]").fill("hunter2");
    await anon.locator("[data-testid=public-unlock-submit]").click();
    await expect(anon.locator("[data-testid=public-card-name]")).toHaveText(title, {
      timeout: 5_000,
    });

    await expect(anon.getByText(/Error 4\d\d/)).toHaveCount(0);
  });

  test("live update: an item added by the owner appears on the open viewer", async ({
    page,
    browser,
  }) => {
    const { id, token } = await createSharedLink(page, "view");

    const anon = await openAnon(browser, token);
    await expect(anon.locator("[data-testid=public-card]")).toBeVisible({ timeout: 5_000 });

    // Owner adds an item via the authed API → SSE fans out to the anon viewer.
    const liveText = `Live-${Date.now()}`;
    await page.request.post(`/api/checklist/${id}/item`, {
      data: { text: liveText },
      headers: { "Content-Type": "application/json" },
    });

    await expect(anon.locator("[data-testid=public-items]")).toContainText(liveText, {
      timeout: 8_000,
    });
  });

  test("join while logged in adds the card and navigates to /card/<id>", async ({
    page,
    browser,
  }) => {
    const { id, token } = await createSharedLink(page, "view");

    // Log in as a non-owner (testuser01) in a fresh context.
    const ctx = await browser.newContext();
    anonContexts.push(ctx);
    const userPage = await ctx.newPage();
    await userPage.goto("/login");
    await userPage.waitForSelector("form");
    await userPage.locator("[data-testid=login-username]").fill(TEST_USER.username);
    await userPage.locator("[data-testid=login-password]").fill(TEST_USER.password);
    await userPage.locator('form button[type="submit"]').click();
    await userPage.waitForURL("/");

    await userPage.goto(`/p/${token}`);
    await expect(userPage.locator("[data-testid=public-card]")).toBeVisible({ timeout: 5_000 });

    await userPage.locator("[data-testid=public-join]").click();

    // Logged-in join → real collaborator added → navigate into the main app.
    await userPage.waitForURL(`**/card/${id}`, { timeout: 8_000 });
    expect(userPage.url()).toContain(`/card/${id}`);
  });
});
