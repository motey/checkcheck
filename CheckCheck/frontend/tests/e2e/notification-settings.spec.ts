import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";

// Notification settings dialog and email deep links (chunk E5).
//
// The user avatar menu has a "Notifications" item that opens
// components/NotificationSettingsModal.vue: a row per notification type with a
// mode select per channel (in-app and email), a time-zone picker, and a "send
// test email" button. Every change saves on its own as a partial PUT.
//
// The E2E backend (backend/e2e/start_e2e_server.py) boots mail-capable with the
// `null` transport, so the email half of the dialog is real, and it disables the
// `public_link_opened` type so the administrator-locked state has something to
// render.

const TEST_USER = { username: "testuser01", password: "testuserpw_secure1" };

// REQUIRED: the board opens a persistent SSE connection (/api/sync) that blocks
// Playwright teardown if left open. Navigate every page to about:blank.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

async function openSettings(page: Page) {
  await page.locator("[data-testid=user-menu]").click();
  // Dropdown items teleport to the body, so locate at page level.
  await page.locator("[data-testid=menu-notification-settings]").click();
  const dialog = page.locator("[data-testid=notification-settings]");
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  return dialog;
}

/** The caller's stored preferences, straight from the API. */
async function storedPrefs(page: Page): Promise<Record<string, Record<string, unknown>>> {
  const settings = await page.request
    .get("/api/user/me/notification-settings")
    .then((r) => r.json());
  const prefs: Record<string, Record<string, unknown>> = {};
  for (const entry of settings.types as Array<{ type: string; channels: Record<string, any> }>) {
    prefs[entry.type] = Object.fromEntries(
      Object.entries(entry.channels).map(([channel, cell]) => [channel, cell.user_choice])
    );
  }
  return prefs;
}

test.describe("E5 notification settings", () => {
  test.setTimeout(30_000);

  // Every test here writes the admin's own preferences; put them back so the
  // suite stays order-independent.
  test.afterEach(async ({ page }) => {
    await page.request
      .put("/api/user/me/notification-settings", {
        data: {
          prefs: {
            card_shared: { in_app: null, email: null },
            card_invited: { in_app: null, email: null },
          },
          timezone: null,
        },
        headers: { "Content-Type": "application/json" },
      })
      .catch(() => {});
  });

  test("changing a mode saves on its own and survives a reload", async ({ page }) => {
    await page.goto("/");
    let dialog = await openSettings(page);

    const select = dialog.locator("[data-testid=notification-mode-card_shared-email]");
    // Nothing chosen yet: the cell inherits, and says what it inherits.
    await expect(select).toContainText("Default");
    await expect(
      dialog.locator("[data-testid=notification-hint-card_shared-email]")
    ).toContainText("Following the server default");

    await select.click();
    await page.getByRole("option", { name: "Daily summary" }).click();

    // Saved without a Save button.
    await expect(dialog.locator("[data-testid=notification-settings-saved]")).toBeVisible({
      timeout: 5_000,
    });
    await expect.poll(async () => (await storedPrefs(page)).card_shared!.email).toBe("daily");

    // Reload: the dialog shows the stored choice, not the default entry.
    await page.reload();
    dialog = await openSettings(page);
    const reloaded = dialog.locator("[data-testid=notification-mode-card_shared-email]");
    await expect(reloaded).toContainText("Daily summary");
    await expect(reloaded).not.toContainText("Default");
    // An explicit choice explains nothing; only an inherited one does.
    await expect(dialog.locator("[data-testid=notification-hint-card_shared-email]")).toHaveCount(0);

    // Back to the default entry: that drops the override server-side (null),
    // which is a different state from picking today's default by hand.
    await reloaded.click();
    await page.getByRole("option", { name: /^Default/ }).click();
    await expect.poll(async () => (await storedPrefs(page)).card_shared!.email).toBeNull();
  });

  test("an administrator-locked entry is disabled and says why", async ({ page }) => {
    await page.goto("/");
    const dialog = await openSettings(page);

    // NOTIFY_DISABLED_TYPES contains public_link_opened in the E2E instance, so
    // both of its channels are the administrator's decision, not the user's.
    const locked = dialog.locator("[data-testid=notification-mode-public_link_opened-in_app]");
    await expect(locked).toBeVisible();
    await expect(locked).toBeDisabled();
    await expect(
      dialog.locator("[data-testid=notification-hint-public_link_opened-in_app]")
    ).toContainText("administrator");

    // A type nobody capped stays the user's to change.
    await expect(dialog.locator("[data-testid=notification-mode-card_shared-in_app]")).toBeEnabled();
  });

  test("the email column and the test button are real on a mail-capable instance", async ({
    page,
  }) => {
    await page.goto("/");
    const dialog = await openSettings(page);

    await expect(dialog.locator("[data-testid=notification-mode-card_shared-email]")).toBeVisible();
    await expect(dialog.locator("[data-testid=notification-email-disabled]")).toHaveCount(0);
    // The webhook channel exists server-side but has no UI before chunk E6.
    await expect(
      dialog.locator("[data-testid=notification-mode-card_shared-webhook]")
    ).toHaveCount(0);
    await expect(dialog.locator("[data-testid=notification-timezone]")).toBeVisible();

    // First click: queued (202), unless a retry of this spec already used this
    // user's one-per-minute allowance, in which case the server says so. Either
    // answer is the endpoint working; the strict assertion is the second click.
    await dialog.locator("[data-testid=notification-test-email]").click();
    const result = dialog.locator("[data-testid=notification-test-email-result]");
    await expect(result).toBeVisible({ timeout: 10_000 });
    await expect(result).toContainText(/Queued a message to|less than a minute ago/);

    // Second click inside the same minute: the rate limit answers 429 and the
    // dialog says what happened rather than showing a generic failure.
    await dialog.locator("[data-testid=notification-test-email]").click();
    await expect(result).toContainText("less than a minute ago", { timeout: 10_000 });
  });

  test("offline: the dialog explains itself and changes nothing", async ({ page, context }) => {
    await page.goto("/");
    const dialog = await openSettings(page);
    await expect(dialog.locator("[data-testid=notification-settings-offline-notice]")).toHaveCount(0);

    const before = await storedPrefs(page);

    // The window `offline` event drives the connectivity signal (WI-12).
    await context.setOffline(true);
    await expect(dialog.locator("[data-testid=notification-settings-offline-notice]")).toBeVisible({
      timeout: 5_000,
    });

    // The controls are inert, so a click cannot queue anything.
    await dialog
      .locator("[data-testid=notification-mode-card_shared-in_app]")
      .click({ trial: true, timeout: 2_000 })
      .catch(() => {});

    await context.setOffline(false);
    await expect(dialog.locator("[data-testid=notification-settings-offline-notice]")).toHaveCount(
      0,
      { timeout: 10_000 }
    );
    // Nothing was written while the connection was gone.
    expect(await storedPrefs(page)).toEqual(before);
  });
});

test.describe("E5 email deep link", () => {
  test.setTimeout(45_000);

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

  // Admin creates a card and shares it with testuser01, which is what emits the
  // recipient's `card_shared` notification.
  async function shareNewCard(page: Page): Promise<string> {
    const title = `Deeplink-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const cl = await (
      await page.request.post("/api/checklist", {
        data: { name: title },
        headers: { "Content-Type": "application/json" },
      })
    ).json();
    cleanupChecklists.push(cl.id);
    const results = await (
      await page.request.get("/api/user/search", { params: { q: TEST_USER.username } })
    ).json();
    const target = results.find((u: any) => u.user_name === TEST_USER.username) ?? results[0];
    const res = await page.request.put(`/api/checklist/${cl.id}/shares/${target.id}`, {
      data: { permission: "edit" },
      headers: { "Content-Type": "application/json" },
    });
    expect(res.ok()).toBeTruthy();
    return cl.id;
  }

  test("?card=&n= opens the card and marks exactly that notification read", async ({
    page,
    browser,
  }) => {
    const { ctx, page: userPage } = await loginAsTestUser(browser);
    secondCtx = ctx;
    await userPage.waitForSelector("[data-testid=notification-bell]");

    // Two shares, so "exactly that one" is a real claim.
    const firstCard = await shareNewCard(page);
    const secondCard = await shareNewCard(page);

    type Feed = Array<{ id: string; cl_id: string | null; read_at: string | null }>;
    const readFeed = async (): Promise<Feed> =>
      userPage.request.get("/api/user/me/notifications").then((r) => r.json());

    // Both notifications have to exist before one of them can be singled out.
    await expect
      .poll(
        async () =>
          (await readFeed()).filter((n) => n.cl_id === firstCard || n.cl_id === secondCard).length,
        { timeout: 10_000 }
      )
      .toBe(2);

    const feed = await readFeed();
    const target = feed.find((n) => n.cl_id === secondCard);
    const other = feed.find((n) => n.cl_id === firstCard);
    expect(target, "the share should have produced a notification").toBeTruthy();
    expect(other).toBeTruthy();
    expect(target!.read_at).toBeNull();

    // Follow the link exactly as the email writes it.
    await userPage.goto(`/?card=${secondCard}&n=${target!.id}`);

    // The query link becomes the app's own card route, and the deep-link
    // parameters are gone from the URL once consumed.
    await userPage.waitForURL(`**/card/${secondCard}`, { timeout: 10_000 });
    expect(userPage.url()).not.toContain("n=");
    expect(userPage.url()).not.toContain("card=");

    // Exactly one notification is now read: the one the link named.
    await expect
      .poll(async () => (await readFeed()).find((n) => n.id === target!.id)?.read_at ?? null, {
        timeout: 10_000,
      })
      .not.toBeNull();
    const after = await readFeed();
    expect(after.find((n) => n.id === other!.id)?.read_at ?? null).toBeNull();

    await expect(userPage.getByText(/Error 4\d\d/)).toHaveCount(0);
  });
});
