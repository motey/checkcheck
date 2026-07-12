/**
 * Account switch on a shared browser (Chunk A1).
 *
 * The local-first cache (snapshot + cursor + outbox) lives in fixed-name
 * IndexedDB DBs shared by whoever uses this browser. Before the fix, logging in
 * as user B after user A left A's state in place, so B would:
 *   • first-paint A's board from the snapshot (privacy leak), and
 *   • inherit A's sync cursor — a *valid* high-water mark, so no `full_resync`
 *     ever heals it, and B's own cards whose rows predate that cursor are never
 *     delivered → B's board is permanently missing them.
 *
 * The fix stamps the cache with its owning user id and drops everything on a
 * mismatch (explicit logout clears it; a boot-time reconcile is the backstop).
 * This test drives the real two-account flow through the UI and asserts B sees
 * B's board — not A's cache, and not an empty board missing B's own card.
 *
 * Both cards are created up front (via API), so B's card already predates the
 * cursor A advances to — exactly the inherited-cursor trap. Runs flag-on.
 */
import {
  test,
  expect,
  type Page,
  type APIRequestContext,
} from "@playwright/test";
import { resolve } from "path";

// Credentials match auth.setup.ts / provisioning_data/test_users.yaml.
const ADMIN = { username: "admin3", password: "password123" };
const TEST_USER = { username: "testuser01", password: "testuserpw_secure1" };

// The shared admin auth state persisted by auth.setup.ts — used to get an
// authenticated admin request context without a login POST (the session-login
// endpoint answers a 303 the API request context can't follow).
const AUTH_STATE_FILE = resolve(__dirname, ".auth/state.json");
// Manually-created contexts don't inherit the project `use.baseURL`.
const BASE_URL = "http://localhost:8182";

async function apiPost(req: APIRequestContext, path: string, body: object) {
  const res = await req.post(path, { data: body, headers: { "Content-Type": "application/json" } });
  expect(res.ok(), `POST ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

const cardPreview = (page: Page, name: string) =>
  page.locator("[data-testid=checklist-board] .checklist-preview").filter({ hasText: name });

/** Log in through the UI in the current page/context and land on the board. */
async function uiLogin(page: Page, creds: { username: string; password: string }) {
  await page.goto("/login?localFirst=1");
  await page.waitForSelector("form");
  await page.locator("[data-testid=login-username]").fill(creds.username);
  await page.locator("[data-testid=login-password]").fill(creds.password);
  await page.locator('form button[type="submit"]').click();
  await page.waitForURL("/");
  await page.goto("/?localFirst=1");
  await page.waitForSelector("[data-testid=checklist-board]");
}

test.describe("account switch on a shared browser (A1)", () => {
  test.setTimeout(60_000);

  // This spec logs in through the UI itself, so it must NOT start from the
  // shared admin auth state (that would pre-authenticate the context).
  test.use({ storageState: { cookies: [], origins: [] } });

  const cleanup: { req: APIRequestContext; id: string }[] = [];

  test.afterEach(async ({ page }) => {
    for (const { req, id } of cleanup) await req.delete(`/api/checklist/${id}`).catch(() => {});
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("logging in as a different user shows their board, not the previous user's cache", async ({
    page,
    browser,
  }) => {
    const tag = Date.now();
    const nameA = `AcctA-${tag}`;
    const nameB = `AcctB-${tag}`;

    // Both cards exist BEFORE the switch, so B's card already predates the global
    // cursor A will advance to (the inherited-cursor trap). Card A via admin's
    // stored auth; card B via a throwaway UI-logged-in testuser01 context.
    const adminCtx = await browser.newContext({ storageState: AUTH_STATE_FILE, baseURL: BASE_URL });
    const cardA = await apiPost(adminCtx.request, "/api/checklist", { name: nameA });
    cleanup.push({ req: adminCtx.request, id: cardA.id });

    const userCtx = await browser.newContext({ baseURL: BASE_URL });
    const userSetup = await userCtx.newPage();
    await uiLogin(userSetup, TEST_USER);
    const cardB = await apiPost(userCtx.request, "/api/checklist", { name: nameB });
    cleanup.push({ req: userCtx.request, id: cardB.id });
    await userSetup.goto("about:blank");

    // ── User A (admin) logs in, loads their board, and syncs. ──
    await uiLogin(page, ADMIN);
    await expect(cardPreview(page, nameA)).toBeVisible({ timeout: 8_000 });
    await expect(cardPreview(page, nameB)).toHaveCount(0);
    // Let the boot delta pull land so A's cursor advances past B's card row.
    await page.waitForTimeout(1_500);

    // ── A logs out (clears the local cache), B logs in on the same browser. ──
    await page.locator("[data-testid=user-menu]").click();
    await page.locator("[data-testid=logout-button]").click();
    await page.waitForURL(/\/login/);

    await uiLogin(page, TEST_USER);

    // B must see B's own card (inherited-cursor bug would leave it missing)…
    await expect(cardPreview(page, nameB)).toBeVisible({ timeout: 10_000 });
    // …and must NOT see A's card lingering from the previous session's cache.
    await expect(cardPreview(page, nameA)).toHaveCount(0);

    // Tear the SSE stream down before teardown.
    await page.goto("about:blank");
  });
});
