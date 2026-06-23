/**
 * Sidebar "Shared with me" / "Shared by me" filters (?shared=with_me|by_me).
 *
 * Semantics:
 *   - Shared by me   → cards the logged-in user OWNS and has shared with someone.
 *   - Shared with me → cards owned by SOMEONE ELSE that were shared with the user.
 *   - Both AND with the label filter.
 *
 * The default E2E backend runs with SHARING_REQUIRE_INVITE_ACCEPT OFF, so a
 * share is accepted instantly (no pending invite) — exactly what these filters
 * rely on. The default `page` fixture is the admin (the owner / sharer).
 *
 * Assertions target specific, uniquely-tagged card titles so they stay robust
 * against cards other specs may have shared with testuser01.
 */
import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";

const TEST_USER = { username: "testuser01", password: "testuserpw_secure1" };

// Close the board's SSE (/api/sync) before Playwright tears down each page.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

// ── helpers ──────────────────────────────────────────────────────────────────

async function apiPost(page: Page, path: string, body: object) {
  const res = await page.request.post(path, {
    data: body,
    headers: { "Content-Type": "application/json" },
  });
  expect(res.ok(), `POST ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

async function apiPut(page: Page, path: string, body?: object) {
  const res = await page.request.put(path, {
    data: body ?? {},
    headers: { "Content-Type": "application/json" },
  });
  expect(res.ok(), `PUT ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

async function apiDelete(page: Page, path: string) {
  await page.request.delete(path).catch(() => {});
}

// Admin shares a card with testuser01 (instant-accepted in flag-off mode).
async function shareWithTestUser(page: Page, checklistId: string) {
  const results = await (
    await page.request.get("/api/user/search", { params: { q: TEST_USER.username } })
  ).json();
  const target = results.find((u: any) => u.user_name === TEST_USER.username) ?? results[0];
  expect(target, "testuser01 should be findable").toBeTruthy();
  await apiPut(page, `/api/checklist/${checklistId}/shares/${target.id}`, { permission: "edit" });
}

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

// ── shared by me ───────────────────────────────────────────────────────────────

test.describe("shared by me", () => {
  test.setTimeout(20_000);

  const cleanupChecklists: string[] = [];
  const cleanupLabels: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanupChecklists) await apiDelete(page, `/api/checklist/${id}`);
    for (const id of cleanupLabels) await apiDelete(page, `/api/label/${id}`);
    cleanupChecklists.length = 0;
    cleanupLabels.length = 0;
  });

  test("clicking 'Shared by me' shows only cards I own and shared", async ({ page }) => {
    const tag = Date.now();
    const sharedName = `SbmShared-${tag}`;
    const unsharedName = `SbmUnshared-${tag}`;

    const shared = await apiPost(page, "/api/checklist", { name: sharedName });
    cleanupChecklists.push(shared.id);
    await shareWithTestUser(page, shared.id);

    const unshared = await apiPost(page, "/api/checklist", { name: unsharedName });
    cleanupChecklists.push(unshared.id);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(page.getByText(sharedName, { exact: true })).toBeVisible();
    await expect(page.getByText(unsharedName, { exact: true })).toBeVisible();

    // Activate the filter from the sidebar.
    await page.locator("[data-testid=shared-filter-by_me]").click();
    await expect(page).toHaveURL(/shared=by_me/, { timeout: 3_000 });

    await expect(page.getByText(sharedName, { exact: true })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(unsharedName, { exact: true })).not.toBeVisible();
  });

  test("'Shared by me' ANDs with the label filter", async ({ page }) => {
    const tag = Date.now();
    const labelName = `SbmLabel-${tag}`;
    const labeledName = `SbmLabeled-${tag}`;
    const unlabeledName = `SbmUnlabeled-${tag}`;

    const label = await apiPost(page, "/api/label", { display_name: labelName });
    cleanupLabels.push(label.id);

    const labeled = await apiPost(page, "/api/checklist", { name: labeledName });
    cleanupChecklists.push(labeled.id);
    await shareWithTestUser(page, labeled.id);
    await apiPut(page, `/api/checklist/${labeled.id}/label/${label.id}`);

    const unlabeled = await apiPost(page, "/api/checklist", { name: unlabeledName });
    cleanupChecklists.push(unlabeled.id);
    await shareWithTestUser(page, unlabeled.id);

    // Both filters active via URL — only the shared+labeled card survives.
    await page.goto(`/?shared=by_me&label=${label.id}`);
    await page.waitForSelector("[data-testid=checklist-board]");

    await expect(page.getByText(labeledName, { exact: true })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(unlabeledName, { exact: true })).not.toBeVisible();
  });
});

// ── shared with me ─────────────────────────────────────────────────────────────

test.describe("shared with me", () => {
  test.setTimeout(20_000);

  const cleanupChecklists: string[] = [];
  let secondCtx: BrowserContext | null = null;

  test.afterEach(async ({ page }) => {
    for (const id of cleanupChecklists) await apiDelete(page, `/api/checklist/${id}`);
    cleanupChecklists.length = 0;
    if (secondCtx) {
      await Promise.all(secondCtx.pages().map((p) => p.goto("about:blank").catch(() => {})));
      await secondCtx.close().catch(() => {});
      secondCtx = null;
    }
  });

  test("'Shared with me' shows others' shared cards but not my own", async ({ page, browser }) => {
    const tag = Date.now();
    const sharedName = `SwmShared-${tag}`;
    const ownName = `SwmOwn-${tag}`;

    // Admin owns a card and shares it with testuser01.
    const shared = await apiPost(page, "/api/checklist", { name: sharedName });
    cleanupChecklists.push(shared.id);
    await shareWithTestUser(page, shared.id);

    // testuser01 logs in and creates a card they own themselves.
    const { ctx, page: userPage } = await loginAsTestUser(browser);
    secondCtx = ctx;
    const own = await apiPost(userPage, "/api/checklist", { name: ownName });

    // Under "Shared with me": the admin-shared card shows, their own does not.
    await userPage.goto(`/?shared=with_me`);
    await userPage.waitForSelector("[data-testid=checklist-board]");
    await expect(userPage.getByText(sharedName, { exact: true })).toBeVisible({ timeout: 5_000 });
    await expect(userPage.getByText(ownName, { exact: true })).not.toBeVisible();

    // Without the filter, the user's own card is visible again (sanity check).
    await userPage.goto("/");
    await userPage.waitForSelector("[data-testid=checklist-board]");
    await expect(userPage.getByText(ownName, { exact: true })).toBeVisible({ timeout: 5_000 });

    await apiDelete(userPage, `/api/checklist/${own.id}`);
  });
});
