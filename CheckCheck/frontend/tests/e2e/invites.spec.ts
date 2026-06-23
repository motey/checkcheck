import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";

// Invite inbox (Frontend Phase F6).
//
// ⚠️ INVITE-MODE PASS — this spec only runs when the backend boots with
// SHARING_REQUIRE_INVITE_ACCEPT=1 (mirroring the backend's second pytest pass).
// The default E2E backend runs with the flag OFF, where a share is accepted
// instantly and NO pending invite is ever created — so accept/decline can't be
// exercised. Run this pass with:
//
//     SHARING_REQUIRE_INVITE_ACCEPT=1 ./run_e2e_tests.sh invites
//
// The `invites` filename filter limits the run to THIS file so the other specs
// don't run in invite mode (where a normal share wouldn't land instantly). The
// describe-level test.skip below makes the default full run skip this file
// cleanly instead of failing.
//
// Flow: the default `page` fixture is the admin (the sharing owner). The invitee
// (testuser01) logs in in a fresh context so we can watch their bell / grid
// update live over SSE. In invite mode, sharing a card fires a `share_invited`
// SSE (→ the bell's Invites section) AND a `card_invited` notification.

const TEST_USER = { username: "testuser01", password: "testuserpw_secure1" };
const INVITE_MODE = !!process.env.SHARING_REQUIRE_INVITE_ACCEPT;

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

test.describe("F6 invite inbox", () => {
  // Only meaningful when the backend is in invite mode.
  test.skip(
    !INVITE_MODE,
    "requires SHARING_REQUIRE_INVITE_ACCEPT=1 (run: SHARING_REQUIRE_INVITE_ACCEPT=1 ./run_e2e_tests.sh invites)"
  );
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

  // Admin (the `page` fixture) creates a card with one item; returns id + title.
  async function createCard(page: Page): Promise<{ id: string; title: string }> {
    const tag = Date.now();
    const title = `Invite-${tag}`;
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

  // Admin shares the card with testuser01 over the API. In invite mode this
  // creates a PENDING invite and fans out a `share_invited` SSE to testuser01.
  async function inviteTestUser(page: Page, clId: string, level: "view" | "check" | "edit" = "edit") {
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

  // Open the bell dropdown and return the Invites section locator.
  async function openInvites(userPage: Page) {
    await userPage.locator("[data-testid=notification-bell]").click();
    const panel = userPage.locator("[data-testid=notification-panel]");
    await expect(panel).toBeVisible({ timeout: 5_000 });
    return panel.locator("[data-testid=invite-section]");
  }

  test("a shared card lands as a pending invite and Accept adds it to the grid", async ({
    page,
    browser,
  }) => {
    // Recipient's board must be open (SSE connected) BEFORE we share.
    const { ctx, page: userPage } = await loginAsTestUser(browser);
    secondCtx = ctx;
    await userPage.waitForSelector("[data-testid=notification-bell]");

    const { id, title } = await createCard(page);

    // Before the invite: the card is NOT in the recipient's grid.
    const card = userPage.locator(".checklist-preview").filter({ hasText: title });
    await expect(card).toHaveCount(0);

    await inviteTestUser(page, id, "edit");

    // The `share_invited` SSE bumps the invite inbox live; open the dropdown.
    const section = await openInvites(userPage);
    await expect(section, "Invites section should appear live").toBeVisible({ timeout: 8_000 });
    const row = section.locator("[data-testid=invite-row]").filter({ hasText: title });
    await expect(row).toHaveCount(1, { timeout: 5_000 });

    // Still pending → card not in the grid yet.
    await expect(card, "card stays out of the grid until accepted").toHaveCount(0);

    // Accept → the card animates into the grid; the invite row is removed.
    await row.locator("[data-testid=invite-accept]").click();
    await expect(card, "accepted card should appear in the grid").toHaveCount(1, {
      timeout: 8_000,
    });
    await expect(row, "invite row should be removed after accept").toHaveCount(0, {
      timeout: 5_000,
    });

    // Happy path: no error toasts.
    await expect(userPage.getByText(/Error 4\d\d/)).toHaveCount(0);
  });

  test("Decline removes the invite and the card never enters the grid", async ({ page, browser }) => {
    const { ctx, page: userPage } = await loginAsTestUser(browser);
    secondCtx = ctx;
    await userPage.waitForSelector("[data-testid=notification-bell]");

    const { id, title } = await createCard(page);
    await inviteTestUser(page, id, "edit");

    const section = await openInvites(userPage);
    const row = section.locator("[data-testid=invite-row]").filter({ hasText: title });
    await expect(row).toHaveCount(1, { timeout: 8_000 });

    // Decline → the row is removed and no card is added.
    await row.locator("[data-testid=invite-decline]").click();
    await expect(row, "invite row should be removed after decline").toHaveCount(0, {
      timeout: 5_000,
    });

    const card = userPage.locator(".checklist-preview").filter({ hasText: title });
    await expect(card, "declined card must never enter the grid").toHaveCount(0);

    // The owner now sees the share as 'declined' in their share list.
    const shares = await (await page.request.get(`/api/checklist/${id}/shares`)).json();
    const declined = (shares as any[]).find((s) => s.status === "declined");
    expect(declined, "owner's share list should show the declined invite").toBeTruthy();

    await expect(userPage.getByText(/Error 4\d\d/)).toHaveCount(0);
  });
});
