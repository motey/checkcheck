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
// The account tests/e2e/auth.setup.ts persists the shared storage state for.
const ADMIN = { username: "admin3", password: "password123" };

// REQUIRED: the board opens a persistent SSE connection (/api/sync) that blocks
// Playwright teardown if left open. Navigate every page to about:blank.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

async function openSettings(page: Page) {
  const dialog = page.locator("[data-testid=notification-settings]");
  // Since S1 the pane is a place, so a reload of /settings/notifications brings
  // it back on its own and there is no user menu to reach behind it. Decided on
  // the URL rather than on the dialog being visible: right after a reload it is
  // not painted yet, and a menu click would race the modal's own backdrop.
  if (!page.url().includes("/settings/notifications")) {
    // A previous close may still be in flight: its backdrop swallows the menu
    // click, and reopening before the router has settled the closing URL makes
    // the open a redundant navigation the router drops. Wait for both halves of
    // "closed" (no dialog, no pane in the URL) before reaching for the menu.
    await expect(dialog).toBeHidden();
    await expect(page).not.toHaveURL(/\/settings\/notifications/);
    await page.locator("[data-testid=user-menu]").click();
    // Dropdown items teleport to the body, so locate at page level.
    await page.locator("[data-testid=menu-notification-settings]").click();
  }
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  // Opening must leave a URL behind, or the back button and a shared link both
  // stop working.
  await expect(page).toHaveURL(/\/settings\/notifications$/);
  return dialog;
}

/** Log in through the UI in this page's own context and land on the board. */
async function uiLogin(page: Page, creds: { username: string; password: string }) {
  await page.goto("/login");
  await page.waitForSelector("form");
  await page.locator("[data-testid=login-username]").fill(creds.username);
  await page.locator("[data-testid=login-password]").fill(creds.password);
  await page.locator('form button[type="submit"]').click();
  await page.waitForURL("/");
}

/** Remove every push subscription of whoever *page* is authenticated as. */
async function clearPushSubscriptions(page: Page): Promise<void> {
  const subs: Array<{ id: string }> = await page.request
    .get("/api/user/me/push-subscriptions")
    .then((r) => (r.ok() ? r.json() : []))
    .catch(() => []);
  for (const sub of subs) {
    await page.request.delete(`/api/user/me/push-subscriptions/${sub.id}`).catch(() => {});
  }
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
    // Nothing chosen yet: the cell inherits, and the control itself says what it
    // inherits ("Default (Off)"). S2 dropped the line that used to repeat that
    // under every untouched cell, so an inheriting cell now explains nothing.
    await expect(select).toContainText("Default");
    await expect(
      dialog.locator("[data-testid=notification-hint-card_shared-email]")
    ).toHaveCount(0);

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
    // Nothing to explain here either: only a lock or a type restriction is.
    await expect(dialog.locator("[data-testid=notification-hint-card_shared-email]")).toHaveCount(0);

    // Back to the default entry: that drops the override server-side (null),
    // which is a different state from picking today's default by hand.
    await reloaded.click();
    await page.getByRole("option", { name: /^Default/ }).click();
    await expect.poll(async () => (await storedPrefs(page)).card_shared!.email).toBeNull();
  });

  test("S2: the grid names each channel once, not once per notification type", async ({ page }) => {
    // The finding this chunk exists for. The E2E instance has email, webhooks
    // and push all on, so before S2 a user read the same four channel titles
    // (and the same four hint lines) four times over, once inside each type's
    // block, to find the one control they came for.
    await page.goto("/");
    const dialog = await openSettings(page);

    const matrix = dialog.locator("[data-testid=notification-matrix]");
    await expect(matrix).toBeVisible();
    for (const [channel, title] of [
      ["in_app", "In the app"],
      ["email", "Email"],
      ["webhook", "Webhook"],
      ["push", "Push"],
    ]) {
      await expect(matrix.locator(`[data-testid=notification-channel-${channel}]`)).toHaveCount(1);
      await expect(matrix.getByText(title!, { exact: true })).toHaveCount(1);
    }

    // Every row still has a control on every channel: the titles collapsed, the
    // matrix did not. `card_invited` is not in this list since K2 merged it into
    // `card_shared`; the row below asserts it has gone.
    for (const type of ["card_shared", "public_link_opened", "reminder_due"]) {
      await expect(matrix.locator(`[data-testid=notification-type-${type}]`)).toHaveCount(1);
      for (const channel of ["in_app", "email", "webhook", "push"]) {
        await expect(
          matrix.locator(`[data-testid=notification-mode-${type}-${channel}]`)
        ).toHaveCount(1);
      }
    }
  });

  test("K2: the two share types are one row, and it writes both", async ({ page }) => {
    // `SHARING_REQUIRE_INVITE_ACCEPT` decides which of `card_shared` and
    // `card_invited` an instance ever emits, so a dialog with a row for each had
    // one row that could not fire. The E2E instance shares instantly (the flag is
    // off unless the invite pass sets it), so the live half here is `card_shared`.
    await page.goto("/");
    const dialog = await openSettings(page);

    await expect(dialog.locator("[data-testid=notification-type-card_shared]")).toHaveCount(1);
    await expect(dialog.locator("[data-testid=notification-type-card_invited]")).toHaveCount(0);
    await expect(dialog.locator("[data-testid=notification-mode-card_invited-in_app]")).toHaveCount(
      0
    );
    await expect(dialog.locator("[data-testid=notification-type-card_shared]")).toContainText(
      "shared with me"
    );

    // One change, both halves. That is what makes flipping the server flag later
    // a no-op for the user: their choice already applies to the type that
    // becomes live.
    const select = dialog.locator("[data-testid=notification-mode-card_shared-email]");
    await select.click();
    await page.getByRole("option", { name: "Hourly summary" }).click();
    await expect(dialog.locator("[data-testid=notification-settings-saved]")).toBeVisible({
      timeout: 5_000,
    });
    await expect
      .poll(async () => {
        const prefs = await storedPrefs(page);
        return [prefs.card_shared!.email, prefs.card_invited!.email];
      })
      .toEqual(["hourly", "hourly"]);
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

  test("the webhook channel has a column, a URL field and a test button", async ({
    page,
  }) => {
    // Chunk E6. The E2E instance runs with NOTIFY_WEBHOOK_ENABLED, so this is
    // the whole channel: a mode select per type, somewhere to put the URL, and
    // a way to prove the target answers.
    await page.goto("/");
    const dialog = await openSettings(page);

    await expect(
      dialog.locator("[data-testid=notification-mode-card_shared-webhook]")
    ).toBeVisible();

    const block = dialog.locator("[data-testid=notification-webhook]");
    await expect(block).toBeVisible();

    // Nothing to send while no URL is saved, so the test button stays out of
    // the way rather than producing a 409 the user could have been spared.
    await expect(block.locator("[data-testid=notification-webhook-test]")).toBeDisabled();

    const url = `https://hooks.example.org/e2e-${Date.now()}`;
    await block.locator("[data-testid=notification-webhook-url]").fill(url);
    await block.locator("[data-testid=notification-webhook-save]").click();
    await expect(dialog.locator("[data-testid=notification-settings-saved]")).toBeVisible({
      timeout: 5_000,
    });

    // It survives a close and reopen, which means it reached the server. The
    // reopen is a load of the pane's own URL rather than another trip through
    // the user menu: since S1 that is what reopening this pane *is*, and it
    // proves the value came back from the server rather than from a dialog that
    // was never really torn down.
    await page.keyboard.press("Escape");
    await expect(page).toHaveURL(/\/$/);
    await page.goto("/settings/notifications");
    const reopened = await openSettings(page);
    await expect(reopened.locator("[data-testid=notification-webhook-url]")).toHaveValue(url, {
      timeout: 5_000,
    });

    // Queued, not delivered: this URL goes nowhere, and the dialog must not
    // claim otherwise.
    await reopened.locator("[data-testid=notification-webhook-test]").click();
    const result = reopened.locator("[data-testid=notification-webhook-result]");
    await expect(result).toContainText(/Queued a request to|less than a minute ago/, {
      timeout: 10_000,
    });

    // Clearing the field clears the stored URL.
    await reopened.locator("[data-testid=notification-webhook-url]").fill("");
    await reopened.locator("[data-testid=notification-webhook-save]").click();
    await expect(reopened.locator("[data-testid=notification-webhook-test]")).toBeDisabled({
      timeout: 5_000,
    });

    await expect(page.getByText(/Error 4\d\d/)).toHaveCount(0);
    await page.keyboard.press("Escape");
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

  test("S1: the pane is a place: a cold URL opens it, and closing goes back to the board", async ({
    page,
  }) => {
    // A link somebody was sent, or a reload: nothing clicked a menu here.
    await page.goto("/settings/notifications");
    const dialog = page.locator("[data-testid=notification-settings]");
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    // On top of a real board, not instead of it: the pane is an overlay.
    await expect(page.locator("[data-testid=user-menu]")).toBeAttached();
    // And it is loaded, not an empty shell: the cold mount has to fetch too.
    await expect(dialog.locator("[data-testid=notification-mode-card_shared-in_app]")).toBeVisible({
      timeout: 10_000,
    });

    // Escape closes it, and the URL goes with it. A dialog that closes while the
    // URL still names it cannot be reopened without navigating away first.
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden({ timeout: 5_000 });
    await expect(page).toHaveURL(/\/$/);

    // Reopening from the menu still works, which is what the stale-URL bug broke.
    await openSettings(page);
  });

  test("S1: Back closes the pane, and does not reopen it afterwards", async ({ page }) => {
    await page.goto("/");
    const dialog = await openSettings(page);

    // Opening pushed a history entry, so Back is the close button.
    await page.goBack();
    await expect(dialog).toBeHidden({ timeout: 5_000 });
    await expect(page).toHaveURL(/\/$/);

    // Closing replaces instead of pushing, so the entry the pane occupied is
    // gone and Back afterwards leaves the board alone rather than reopening it.
    await openSettings(page);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden({ timeout: 5_000 });
    await page.goBack();
    await expect(dialog).toBeHidden();
    await expect(page).toHaveURL(/\/$/);
  });
});

test.describe("P2 push notifications", () => {
  test.setTimeout(30_000);

  // The E2E backend runs with NOTIFY_PUSH_ENABLED and a throwaway VAPID pair
  // (start_e2e_server.py), so the push column and its device-management block
  // are real. A real push round trip needs an actual push service nothing in
  // CI can reach, so — per plan section 7 — this mocks `navigator.serviceWorker`
  // and `PushManager` at the JS level (a fake subscription object) rather than
  // subscribing for real. The true wire protocol (VAPID JWT shape, encrypted
  // payload) is covered by the backend's own tests_notification_push.py, not
  // here.
  //
  // The fake subscription is kept in `localStorage`, not on `window`: a real
  // one outlives the page, and N5's whole subject is what happens to it across
  // a reload, a logout and an account switch. An init script re-runs on every
  // navigation, so a window-scoped fake would quietly vanish exactly where
  // these tests need it to persist.
  async function mockPush(page: Page): Promise<void> {
    await page.addInitScript(() => {
      const STORE_KEY = "__e2eFakePushSub";
      const readStored = (): string | null => {
        try {
          return localStorage.getItem(STORE_KEY);
        } catch {
          return null; // opaque origin (about:blank)
        }
      };
      const writeStored = (endpoint: string | null): void => {
        try {
          if (endpoint) localStorage.setItem(STORE_KEY, endpoint);
          else localStorage.removeItem(STORE_KEY);
        } catch {
          /* opaque origin */
        }
      };
      class FakePushSubscription {
        endpoint: string;
        constructor(endpoint: string) {
          this.endpoint = endpoint;
        }
        // The real property the VAPID-key check reads. `null` means "this
        // browser does not expose the key", which N5 treats as "no evidence of
        // a mismatch": the fake has no key to expose.
        get options() {
          return { applicationServerKey: null };
        }
        toJSON() {
          return {
            endpoint: this.endpoint,
            keys: {
              p256dh: "BNfakeP256dhAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
              auth: "fakeAuthAAAAAAAAAAAAAAAA",
            },
          };
        }
        unsubscribe(): Promise<boolean> {
          writeStored(null);
          return Promise.resolve(true);
        }
      }
      const fakePushManager = {
        subscribe: async () => {
          const endpoint = `https://fake.push.example/${Math.random().toString(36).slice(2)}`;
          writeStored(endpoint);
          return new FakePushSubscription(endpoint);
        },
        getSubscription: async () => {
          const endpoint = readStored();
          return endpoint ? new FakePushSubscription(endpoint) : null;
        },
      };
      const fakeRegistration = { pushManager: fakePushManager };
      Object.defineProperty(navigator.serviceWorker, "ready", {
        configurable: true,
        get: () => Promise.resolve(fakeRegistration),
      });
      (navigator.serviceWorker as unknown as { getRegistration: () => Promise<unknown> }).getRegistration =
        async () => fakeRegistration;
      Object.defineProperty(window.Notification, "permission", {
        configurable: true,
        get: () => (window as unknown as { __notifPermission?: string }).__notifPermission ?? "default",
      });
      (window.Notification as unknown as { requestPermission: () => Promise<string> }).requestPermission =
        async () => {
          (window as unknown as { __notifPermission?: string }).__notifPermission = "granted";
          return "granted";
        };
    });
  }

  // Every test here enables at least one device; remove it so the suite stays
  // order-independent (the admin user is shared across every test in this file).
  test.afterEach(async ({ page }) => {
    await clearPushSubscriptions(page);
  });

  test("the push column and device block render on an instance with push on", async ({ page }) => {
    await mockPush(page);
    await page.goto("/");
    const dialog = await openSettings(page);

    await expect(dialog.locator("[data-testid=notification-mode-card_shared-push]")).toBeVisible();
    const block = dialog.locator("[data-testid=notification-push]");
    await expect(block).toBeVisible();
    await expect(block.locator("[data-testid=notification-push-devices]")).toHaveCount(0);
    await expect(block.locator("[data-testid=notification-test-push]")).toBeDisabled();
  });

  test("enabling registers this device, and it survives a reload", async ({ page }) => {
    await mockPush(page);
    await page.goto("/");
    let dialog = await openSettings(page);
    let block = dialog.locator("[data-testid=notification-push]");

    await block.locator("[data-testid=notification-push-enable]").click();
    const device = block.locator("[data-testid=notification-push-device]");
    await expect(device).toHaveCount(1, { timeout: 10_000 });
    await expect(device).toContainText("this device");
    await expect(block.locator("[data-testid=notification-push-enable]")).toContainText(
      "Enabled on this device"
    );

    await page.reload();
    dialog = await openSettings(page);
    block = dialog.locator("[data-testid=notification-push]");
    await expect(block.locator("[data-testid=notification-push-device]")).toHaveCount(1, {
      timeout: 5_000,
    });
  });

  test("send test push reports how many devices it was queued to", async ({ page }) => {
    await mockPush(page);
    await page.goto("/");
    const dialog = await openSettings(page);
    const block = dialog.locator("[data-testid=notification-push]");

    await block.locator("[data-testid=notification-push-enable]").click();
    await expect(block.locator("[data-testid=notification-push-device]")).toHaveCount(1, {
      timeout: 10_000,
    });

    await block.locator("[data-testid=notification-test-push]").click();
    const result = block.locator("[data-testid=notification-test-push-result]");
    await expect(result).toContainText(/Queued to 1 device|less than a minute ago/, {
      timeout: 10_000,
    });
  });

  test("removing a device drops it from the list and disables the test button again", async ({
    page,
  }) => {
    await mockPush(page);
    await page.goto("/");
    const dialog = await openSettings(page);
    const block = dialog.locator("[data-testid=notification-push]");

    await block.locator("[data-testid=notification-push-enable]").click();
    await expect(block.locator("[data-testid=notification-push-device]")).toHaveCount(1, {
      timeout: 10_000,
    });

    await block.locator("[data-testid=notification-push-remove]").click();
    await expect(block.locator("[data-testid=notification-push-device]")).toHaveCount(0, {
      timeout: 5_000,
    });
    await expect(block.locator("[data-testid=notification-test-push]")).toBeDisabled();
  });

  // ── N5: the browser's subscription follows the session it belongs to ───────

  test("a subscription the previous user left behind does not dead-end the next one", async ({
    browser,
  }) => {
    // Finding 5.1: B used to find the Enable button disabled and unexplained,
    // because `getSubscription()` still returned A's subscription while the
    // device list (correctly) showed none of B's. This context logs in itself,
    // so it must not start from the shared admin auth state.
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    try {
      await mockPush(page);
      await uiLogin(page, ADMIN);
      let block = (await openSettings(page)).locator("[data-testid=notification-push]");
      await block.locator("[data-testid=notification-push-enable]").click();
      await expect(block.locator("[data-testid=notification-push-device]")).toHaveCount(1, {
        timeout: 10_000,
      });

      // A's session ends without a clean logout: an expired cookie, a closed
      // tab, a crash. The browser keeps the subscription, and the row on the
      // server stays A's, which is the state the fix has to survive.
      await ctx.clearCookies();
      await uiLogin(page, TEST_USER);

      block = (await openSettings(page)).locator("[data-testid=notification-push]");
      await expect(block.locator("[data-testid=notification-push-device]")).toHaveCount(0);
      const enable = block.locator("[data-testid=notification-push-enable]");
      await expect(enable).toBeEnabled();
      await expect(enable).toContainText("Enable notifications on this device");

      // And it actually works: B gets a row of their own rather than N4's 409
      // for re-registering A's endpoint.
      await enable.click();
      await expect(block.locator("[data-testid=notification-push-device]")).toHaveCount(1, {
        timeout: 10_000,
      });
      await expect(block.locator("[data-testid=notification-push-error]")).toHaveCount(0);
    } finally {
      // B's row, removed with B's own session before the context goes away.
      await clearPushSubscriptions(page).catch(() => {});
      await page.goto("about:blank").catch(() => {});
      await ctx.close();
    }
  });

  test("logging out takes this device's push subscription with it", async ({ page, browser }) => {
    // Finding 5.2: A's card names kept arriving on the lock screen of a browser
    // A had logged out of. The row must be gone from the server, not merely
    // ignored by the client. Asserted through the *shared* admin session, which
    // is the same account and survives this context's logout (the endpoint
    // deletes one session, not all of the user's).
    const ctx = await browser.newContext();
    const other = await ctx.newPage();
    const rowCount = async () =>
      page.request
        .get("/api/user/me/push-subscriptions")
        .then((r) => (r.ok() ? r.json() : []))
        .then((rows: unknown[]) => rows.length);
    try {
      await mockPush(other);
      await uiLogin(other, ADMIN);
      const block = (await openSettings(other)).locator("[data-testid=notification-push]");
      await block.locator("[data-testid=notification-push-enable]").click();
      await expect(block.locator("[data-testid=notification-push-device]")).toHaveCount(1, {
        timeout: 10_000,
      });
      await expect.poll(rowCount, { timeout: 10_000 }).toBe(1);

      await other.keyboard.press("Escape");
      await other.locator("[data-testid=user-menu]").click();
      await other.locator("[data-testid=logout-button]").click();
      await other.waitForURL(/\/login/, { timeout: 10_000 });

      await expect.poll(rowCount, { timeout: 10_000 }).toBe(0);
    } finally {
      await other.goto("about:blank").catch(() => {});
      await ctx.close();
    }
  });

  test("an iPhone outside standalone mode is told to add the app to its home screen, not offered a button", async ({
    browser,
  }) => {
    const iPhoneUA =
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";
    const ctx = await browser.newContext({
      storageState: "tests/e2e/.auth/state.json",
      userAgent: iPhoneUA,
    });
    const page = await ctx.newPage();
    try {
      await page.goto("/");
      const dialog = await openSettings(page);
      const block = dialog.locator("[data-testid=notification-push]");
      await expect(block.locator("[data-testid=notification-push-ios-hint]")).toBeVisible();
      await expect(block.locator("[data-testid=notification-push-enable]")).toHaveCount(0);
    } finally {
      await page.goto("about:blank").catch(() => {});
      await ctx.close();
    }
  });

  test("K2: a page that is not a secure context names https, instead of blaming the browser", async ({
    browser,
  }) => {
    // The real case is the app on plain http behind a LAN address or a hostname,
    // which the E2E harness cannot serve: `http://localhost` is a secure context
    // by definition, which is exactly why a developer never runs into this and
    // why the old wording survived so long. So the flag itself is stubbed, which
    // is the one thing the browser decides here.
    const ctx = await browser.newContext({ storageState: "tests/e2e/.auth/state.json" });
    const page = await ctx.newPage();
    try {
      await page.addInitScript(() => {
        Object.defineProperty(window, "isSecureContext", {
          configurable: true,
          get: () => false,
        });
      });
      await page.goto("/");
      const dialog = await openSettings(page);
      const block = dialog.locator("[data-testid=notification-push]");
      const hint = block.locator("[data-testid=notification-push-insecure-hint]");
      await expect(hint).toBeVisible();
      await expect(hint).toContainText("https");
      // Not the sentence that used to appear here, which named the wrong culprit.
      await expect(
        block.locator("[data-testid=notification-push-unsupported-hint]")
      ).toHaveCount(0);
      await expect(block.locator("[data-testid=notification-push-enable]")).toHaveCount(0);
    } finally {
      await page.goto("about:blank").catch(() => {});
      await ctx.close();
    }
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

  // Finding 10: `card` reaches a router path, so a crafted value cannot leave
  // the origin, but it must not produce a route either.
  test("a ?card= value that is not a card id leaves the user on the board", async ({ page }) => {
    await page.goto(`/?card=${encodeURIComponent("../../admin")}`);
    await page.waitForSelector("[data-testid=checklist-board]");
    expect(page.url()).not.toContain("card=");
    expect(page.url()).not.toContain("admin");
    await expect(page.getByText(/Error 4\d\d/)).toHaveCount(0);
  });

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

test.describe("T time zone re-sync on login", () => {
  test.setTimeout(30_000);

  // The stored zone belongs to the shared admin account here, and a leftover
  // value would follow the reminder specs around (a new reminder defaults to the
  // user's stored zone). Put it back.
  test.afterEach(async ({ page }) => {
    await page.request
      .put("/api/user/me/notification-settings", {
        data: { timezone: null },
        headers: { "Content-Type": "application/json" },
      })
      .catch(() => {});
  });

  const storedTimezone = (page: Page) =>
    page.request
      .get("/api/user/me/notification-settings")
      .then((r) => r.json())
      .then((settings) => settings.timezone ?? null);

  test("a device in another zone updates the stored one at login, without opening the dialog", async ({
    browser,
  }) => {
    // A browser that thinks it is in Auckland, logging in fresh: the board's
    // boot compares `detectTimezone()` to the stored value and writes the
    // difference. Nothing here touches the settings dialog, which is the whole
    // point of the chunk (the digest hour used to be 08:00 UTC until somebody
    // found the picker).
    const ctx = await browser.newContext({ timezoneId: "Pacific/Auckland" });
    const page = await ctx.newPage();
    try {
      await uiLogin(page, ADMIN);
      await expect.poll(() => storedTimezone(page), { timeout: 10_000 }).toBe("Pacific/Auckland");

      // And the dialog shows what was stored behind the user's back, rather than
      // a picker still claiming UTC.
      const dialog = await openSettings(page);
      await expect(dialog.locator("[data-testid=notification-timezone]")).toContainText(
        "Pacific/Auckland"
      );
    } finally {
      await page.goto("about:blank").catch(() => {});
      await ctx.close();
    }
  });

  test("the picker says the zone follows the device, and holds a pick until it moves", async ({
    page,
  }) => {
    // S2: the sync only writes when this *device* changes zone, so the note has
    // to promise exactly that, and a zone picked here has to survive a reload of
    // the same machine. That reload was the finding (review section 2): the
    // control reported "Saved" and reverted.
    await page.goto("/");
    let dialog = await openSettings(page);
    await expect(dialog.locator("[data-testid=notification-timezone-sync-note]")).toContainText(
      "Follows this device when it moves"
    );

    // Wait for this boot's sync to have recorded the device's zone before
    // changing anything. That marker is what makes the next boot leave the pick
    // alone, and it is written asynchronously: picking a zone before it lands
    // makes the reload below a coin toss.
    await expect
      .poll(() =>
        page.evaluate(() =>
          Object.keys(localStorage).some((key) => key.startsWith("checkcheck.tzsync."))
        )
      )
      .toBe(true);

    // Pick a zone this device is not in. Through the API rather than the select
    // menu, which has its own search box and several hundred entries: what is
    // under test is whether the choice survives the next boot, not the widget.
    await page.request.put("/api/user/me/notification-settings", {
      data: { timezone: "Pacific/Auckland" },
      headers: { "Content-Type": "application/json" },
    });

    await page.reload();
    dialog = await openSettings(page);
    await expect(dialog.locator("[data-testid=notification-timezone]")).toContainText(
      "Pacific/Auckland"
    );
    expect(await storedTimezone(page)).toBe("Pacific/Auckland");
  });
});
