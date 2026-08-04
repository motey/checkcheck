/**
 * The shots for what the notification branch built.
 *
 * Writes: DesktopLightNotifications.png
 *         DesktopDarkNotifications.png
 *         DesktopCompactShowReminder.png
 *         DesktopCompactShowLinkByEmail.png
 *
 * Two things make these reproducible and are easy to lose:
 *
 * 1. **The clock is pinned.** The push device list renders "Added <date>" and a
 *    reminder renders either a relative phrase ("In 3 hours") or an absolute one
 *    ("12 Jun 2031 at 09:00") depending on how far away it is. Both read the
 *    wall clock, so without `clock.setFixedTime` every regeneration on a new day
 *    produces a diff.
 * 2. **The time zone is pinned** in playwright.screenshots.config.ts, and the
 *    channels are switched on in backend/screenshots/start_screenshot_server.py.
 *    Without the first, the picker in the dialog shows whichever zone the
 *    machine taking the picture is in; without the second, the dialog
 *    photographs as a single column above "This server does not send email".
 *
 * Runs after the board, editor and menu shots (alphabetical file order within
 * the desktop project): the reminder and link shots create throwaway cards,
 * which would otherwise appear on the board.
 */
import { test, expect, type Page } from "@playwright/test";
import { applyTheme, stabilize, shootPage, shootUnion, closeSse } from "./helpers";

// The instant every shot in this file is taken at. Any fixed value works; this
// one is the day the specs were written.
const FIXED_NOW = new Date("2026-08-04T09:00:00Z");

// A reminder far enough out that it renders as a date rather than "In 3 hours",
// which is what makes the shot stable. It is a fixed instant, so it will one day
// fall into the past and render "Due now": if that happens, move it, do not
// switch to a relative offset (that reintroduces the wall clock).
const REMINDER_AT = "2031-06-12T07:00:00+00:00"; // 09:00 in Europe/Berlin

const cleanup: string[] = [];

async function apiPost(page: Page, path: string, body: object) {
  const res = await page.request.post(path, {
    data: body,
    headers: { "Content-Type": "application/json" },
  });
  expect(res.ok(), `POST ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

/**
 * A fake PushManager, so the device list has a device in it.
 *
 * The same shim the E2E suite uses (tests/e2e/notification-settings.spec.ts),
 * trimmed to what a screenshot needs: subscribe and getSubscription, plus a
 * granted Notification permission so the enable button does not open a real
 * browser prompt. The endpoint never leaves the throwaway database.
 */
async function mockPush(page: Page): Promise<void> {
  await page.addInitScript(() => {
    class FakePushSubscription {
      endpoint = "https://fake.push.example/screenshot-device";
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
        (window as unknown as { __subscribed?: boolean }).__subscribed = false;
        return Promise.resolve(true);
      }
    }
    const fakePushManager = {
      subscribe: async () => {
        (window as unknown as { __subscribed?: boolean }).__subscribed = true;
        return new FakePushSubscription();
      },
      getSubscription: async () =>
        (window as unknown as { __subscribed?: boolean }).__subscribed
          ? new FakePushSubscription()
          : null,
    };
    const fakeRegistration = { pushManager: fakePushManager };
    Object.defineProperty(navigator.serviceWorker, "ready", {
      configurable: true,
      get: () => Promise.resolve(fakeRegistration),
    });
    (
      navigator.serviceWorker as unknown as { getRegistration: () => Promise<unknown> }
    ).getRegistration = async () => fakeRegistration;
    Object.defineProperty(window.Notification, "permission", {
      configurable: true,
      get: () => "granted",
    });
    (window.Notification as unknown as { requestPermission: () => Promise<string> })
      .requestPermission = async () => "granted";
  });
}

/** Remove every push subscription of the account these shots run as. */
async function clearPushSubscriptions(page: Page): Promise<void> {
  const subs: Array<{ id: string }> = await page.request
    .get("/api/user/me/push-subscriptions")
    .then((r) => (r.ok() ? r.json() : []))
    .catch(() => []);
  for (const sub of subs) {
    await page.request.delete(`/api/user/me/push-subscriptions/${sub.id}`).catch(() => {});
  }
}

test.afterEach(async ({ page }) => {
  for (const id of cleanup) {
    await page.request.delete(`/api/checklist/${id}`).catch(() => {});
  }
  cleanup.length = 0;
  // Otherwise the second theme's shot shows two devices, and the dialog's
  // "Enabled on this device" state leaks into whatever runs next.
  await clearPushSubscriptions(page).catch(() => {});
  await page.request
    .put("/api/user/me/notification-settings", {
      data: { timezone: null },
      headers: { "Content-Type": "application/json" },
    })
    .catch(() => {});
  await closeSse(page);
});

for (const theme of ["light", "dark"] as const) {
  const name = theme === "light" ? "DesktopLightNotifications" : "DesktopDarkNotifications";

  test(name, async ({ page }) => {
    await page.clock.setFixedTime(FIXED_NOW);
    await mockPush(page);
    await applyTheme(page, theme);
    // Pin the stored zone too. Left alone it is empty (rendered "UTC") until the
    // board's boot sync writes the device's zone, and whether that PUT lands
    // before the dialog reads its settings is a race: the picker would show UTC
    // in one run and Europe/Berlin in the next. Writing it first also settles
    // the sync, which leaves a matching zone alone.
    await page.request.put("/api/user/me/notification-settings", {
      data: { timezone: "Europe/Berlin" },
      headers: { "Content-Type": "application/json" },
    });

    // Straight to the URL: since S1 the pane is a place, so there is no user
    // menu to drive and no dropdown left hanging over the shot.
    await page.goto("/settings/notifications");
    const dialog = page.locator("[data-testid=notification-settings]");
    await expect(dialog.locator("[data-testid=notification-matrix]")).toBeVisible();

    // A device, or the push block is an empty list under a button.
    await dialog.locator("[data-testid=notification-push-enable]").click();
    await expect(dialog.locator("[data-testid=notification-push-device]")).toHaveCount(1);

    await stabilize(page);
    await shootPage(page, name);
  });
}

test("DesktopCompactShowReminder", async ({ page }) => {
  await page.clock.setFixedTime(FIXED_NOW);
  await applyTheme(page, "dark");

  // A card of its own: the seeded board has no reminders, and a shot that
  // depends on finding one would break the day the seeder changes.
  const clName = "Renew passport";
  const cl = await apiPost(page, "/api/checklist", { name: clName });
  cleanup.push(cl.id);
  await apiPost(page, `/api/checklist/${cl.id}/reminders`, {
    remind_at: REMINDER_AT,
    recurrence: "none",
    note: "Book the appointment",
  });

  await page.goto("/");
  await expect(page.locator("[data-testid=checklist-board]")).toBeVisible();
  const card = page
    .locator("[data-testid=checklist-board] .checklist-preview")
    .filter({ hasText: clName })
    .first();
  await expect(card).toBeVisible();
  await card.locator("[data-testid=card-title]").click();

  const dialog = page.locator("[role=dialog]");
  await expect(dialog).toBeVisible();
  // The panel only exists in the open editor, and only fills in after its own
  // fetch: waiting on the row means the shot cannot catch an empty section.
  const reminders = dialog.locator("[data-testid=card-reminders]");
  await expect(reminders.locator("[data-testid=card-reminder-row]")).toHaveCount(1);
  await stabilize(page);

  await shootUnion(page, [reminders], "DesktopCompactShowReminder");
});

test("DesktopCompactShowLinkByEmail", async ({ page }) => {
  await page.clock.setFixedTime(FIXED_NOW);
  await applyTheme(page, "dark");

  // Must be a card the admin OWNS, for the same reason the share-menu shot
  // creates one: a collaborator gets a read-only variant of this dialog.
  const clName = "Book club";
  const cl = await apiPost(page, "/api/checklist", { name: clName });
  cleanup.push(cl.id);

  await page.goto("/");
  await expect(page.locator("[data-testid=checklist-board]")).toBeVisible();
  const card = page
    .locator("[data-testid=checklist-board] .checklist-preview")
    .filter({ hasText: clName })
    .first();
  await expect(card).toBeVisible();
  await card.locator("[data-testid=share-button]").click();

  const dialog = page.locator("[role=dialog]");
  await expect(dialog).toBeVisible();

  // The field only exists once a link does, which is the whole design of it:
  // mailing a link can never be somebody's first move.
  await dialog.locator("[data-testid=public-link-create]").click();
  const block = dialog.locator("[data-testid=public-link-email]");
  await expect(block).toBeVisible();
  await stabilize(page);

  await shootUnion(page, [block], "DesktopCompactShowLinkByEmail");
});
