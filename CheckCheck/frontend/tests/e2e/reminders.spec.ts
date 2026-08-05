/**
 * Date reminders in the card editor (chunk R4).
 *
 * The open card carries a "Reminders" section listing the caller's own pending
 * reminders on that card, plus an inline form to add one. The form is opened
 * either by the section's own Add button or by the kebab (⋮) menu's "Set a
 * reminder" item, and it is deliberately NOT a nested dialog: the card editor
 * is already a modal, and stacking a second one is the double-dialog bug this
 * codebase has paid for once.
 *
 * Reminders are online-only (plan decision 6 / WI-12): nothing is ever queued in
 * the offline outbox, because a queued reminder whose fire time passes before
 * the queue drains is worse than useless. Offline the section shows a notice and
 * every control goes inert.
 *
 * Every reminder set here is in 2090, so nothing the suite creates can come due
 * while the dispatcher is running (the same habit as tests_reminder_api.py).
 * The kebab menu content is teleported to the body, so its items are located at
 * page level rather than inside the dialog.
 */
import { test, expect, type Page } from "@playwright/test";

// A date far enough out that no dispatcher tick will ever claim it.
const FAR_FUTURE = { date: "2090-03-01", time: "09:00" };

// REQUIRED: the board opens a persistent SSE connection (/api/sync) that blocks
// Playwright teardown if left open. Navigate every page to about:blank.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

async function apiPost(page: Page, path: string, body: object) {
  const res = await page.request.post(path, {
    data: body,
    headers: { "Content-Type": "application/json" },
  });
  expect(res.ok(), `POST ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

/** The caller's reminders on a card, straight from the API. */
async function storedReminders(page: Page, clId: string): Promise<any[]> {
  const res = await page.request.get(`/api/checklist/${clId}/reminders`);
  expect(res.ok(), `GET reminders failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

async function openCardByTitle(page: Page, clName: string) {
  const card = page
    .locator("[data-testid=checklist-board] .checklist-preview")
    .filter({ hasText: clName });
  await expect(card).toBeVisible();
  await card.locator("[data-testid=card-title]").click();
  const dialog = page.locator('[role="dialog"]:has(.checklist)');
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  return dialog;
}

/** Open the card's kebab (⋮) menu (the ellipsis-vertical button in the footer). */
async function openKebab(dialog: ReturnType<Page["locator"]>) {
  await dialog.locator('button:has([class*="ellipsis-vertical"])').first().click();
}

/**
 * Whether `selector` lies fully inside the visible area of its nearest
 * scrollable ancestor. Playwright's own toBeVisible() does not answer this:
 * an element clipped by an overflow container still "is visible" to it, and
 * its auto-scroll would hide the bug under test here anyway.
 */
async function fullyInsideScrollerView(page: Page, selector: string): Promise<boolean> {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel) as HTMLElement | null;
    if (!el) return false;
    let scroller: HTMLElement | null = el;
    while (scroller && scroller !== document.body) {
      const { overflowY } = getComputedStyle(scroller);
      if (overflowY === "auto" || overflowY === "scroll") break;
      scroller = scroller.parentElement;
    }
    const box = el.getBoundingClientRect();
    const view = (scroller ?? document.documentElement).getBoundingClientRect();
    return box.top >= view.top - 1 && box.bottom <= view.bottom + 1;
  }, selector);
}

async function fillReminderForm(
  dialog: ReturnType<Page["locator"]>,
  when: { date: string; time: string },
  opts: { note?: string; repeat?: string } = {}
) {
  // Located inside the card editor's own dialog on purpose: "the form is not a
  // second dialog" is the property this suite is asserting, not a detail.
  const form = dialog.locator("[data-testid=card-reminder-form]");
  await expect(form).toBeVisible({ timeout: 5_000 });
  await form.locator('input[type="date"]').fill(when.date);
  await form.locator('input[type="time"]').fill(when.time);
  if (opts.repeat) {
    await form.locator("[data-testid=card-reminder-recurrence]").click();
    await form.page().getByRole("option", { name: opts.repeat }).click();
  }
  if (opts.note) {
    await form.getByPlaceholder(/Note \(optional\)/).fill(opts.note);
  }
  return form;
}

test.describe("R4 date reminders", () => {
  test.setTimeout(45_000);

  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanup) await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanup.length = 0;
  });

  async function cardWithEditorOpen(page: Page, label: string) {
    const tag = Date.now();
    const clName = `${label}-${tag}`;
    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCardByTitle(page, clName);
    return { id: cl.id as string, name: clName, dialog };
  }

  test("the kebab sets a reminder, which the card editor then lists", async ({ page }) => {
    const { id, dialog } = await cardWithEditorOpen(page, "Remind");

    // Nothing set yet, and the empty state says who a reminder is for.
    const section = dialog.locator("[data-testid=card-reminders]");
    await expect(section).toBeVisible();
    await expect(section.locator("[data-testid=card-reminder-empty]")).toContainText("Only you");

    // The kebab item opens the inline form. No second dialog: the form lives
    // inside the card editor that is already open, which is what locating it
    // under `dialog` in fillReminderForm asserts.
    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Set a reminder" }).click();

    await fillReminderForm(dialog, FAR_FUTURE, {
      note: "Call the plumber",
      repeat: "Every week",
    });
    await dialog.locator("[data-testid=card-reminder-save]").click();

    // The row appears with what was asked for, and the server holds the same.
    const row = section.locator("[data-testid=card-reminder-row]");
    await expect(row).toHaveCount(1, { timeout: 5_000 });
    await expect(row).toContainText("Call the plumber");
    await expect(row).toContainText("Every week");
    await expect(row.locator("[data-testid=card-reminder-when]")).toContainText("2090");

    const stored = await storedReminders(page, id);
    expect(stored).toHaveLength(1);
    expect(stored[0].note).toBe("Call the plumber");
    expect(stored[0].recurrence).toBe("weekly");
    expect(stored[0].status).toBe("pending");
    // 09:00 local was sent with its offset, so the stored UTC instant is that
    // wall clock converted, not the string re-read as UTC.
    expect(new Date(`${stored[0].remind_at}Z`).getTime()).toBe(
      new Date(`${FAR_FUTURE.date}T${FAR_FUTURE.time}:00`).getTime()
    );
  });

  test("the kebab entry scrolls the form into view on a tall card", async ({ page }) => {
    const tag = Date.now();
    const clName = `RemindScroll-${tag}`;
    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    // Enough items that the card outgrows the editor's scroll region and the
    // Reminders section lands below the fold while the editor sits at the top.
    for (let i = 1; i <= 25; i++) {
      await apiPost(page, `/api/checklist/${cl.id}/item`, { text: `Item ${i}` });
    }

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCardByTitle(page, clName);

    const section = "[data-testid=card-reminders]";
    await expect(dialog.locator(section)).toHaveCount(1);
    expect(await fullyInsideScrollerView(page, section)).toBe(false);

    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Set a reminder" }).click();

    const form = "[data-testid=card-reminder-form]";
    await expect(dialog.locator(form)).toBeVisible();
    // Poll because the reveal is a smooth scroll animation, not an instant jump.
    await expect
      .poll(() => fullyInsideScrollerView(page, form), { timeout: 5_000 })
      .toBe(true);
  });

  test("a reminder set earlier is listed when the card is reopened, and can be removed", async ({
    page,
  }) => {
    const tag = Date.now();
    const clName = `RemindList-${tag}`;
    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    // Set it through the API, so this test is about listing and removing rather
    // than about the form.
    await apiPost(page, `/api/checklist/${cl.id}/reminders`, {
      remind_at: "2090-03-01T09:00:00+00:00",
      recurrence: "daily",
      note: "Water the plants",
    });

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCardByTitle(page, clName);

    const row = dialog.locator("[data-testid=card-reminder-row]");
    await expect(row).toHaveCount(1, { timeout: 5_000 });
    await expect(row).toContainText("Water the plants");
    await expect(row).toContainText("Every day");

    await row.locator("[data-testid=card-reminder-remove]").click();
    await expect(row).toHaveCount(0, { timeout: 5_000 });
    await expect(dialog.locator("[data-testid=card-reminder-empty]")).toBeVisible();
    expect(await storedReminders(page, cl.id)).toHaveLength(0);
  });

  test("a time that has already passed is refused without asking the server", async ({ page }) => {
    const { id, dialog } = await cardWithEditorOpen(page, "RemindPast");

    await dialog.locator("[data-testid=card-reminder-add]").click();
    await fillReminderForm(dialog, { date: "2020-01-01", time: "09:00" });
    await dialog.locator("[data-testid=card-reminder-save]").click();

    await expect(dialog.locator("[data-testid=card-reminder-error]")).toContainText(
      "already passed"
    );
    // The form stays open on the value the user typed, and nothing was stored.
    await expect(dialog.locator("[data-testid=card-reminder-form]")).toBeVisible();
    expect(await storedReminders(page, id)).toHaveLength(0);
  });

  test("the reminder section shows an offline notice and disables its controls", async ({
    page,
    context,
  }) => {
    const { dialog } = await cardWithEditorOpen(page, "RemindOffline");
    const section = dialog.locator("[data-testid=card-reminders]");

    // Online first: no notice, and the Add control is usable.
    await expect(section.locator("[data-testid=card-reminder-offline-notice]")).toHaveCount(0);
    await expect(section.locator("[data-testid=card-reminder-add]")).toBeEnabled();

    // Offline: reminders are never queued (plan decision 6), so the whole
    // section goes inert and says why.
    await context.setOffline(true);
    await expect(section.locator("[data-testid=card-reminder-offline-notice]")).toBeVisible({
      timeout: 5_000,
    });
    await expect(section.locator("[data-testid=card-reminder-add]")).toBeDisabled();

    // Back online: the notice clears and the control returns.
    await context.setOffline(false);
    await expect(section.locator("[data-testid=card-reminder-offline-notice]")).toHaveCount(0, {
      timeout: 5_000,
    });
    await expect(section.locator("[data-testid=card-reminder-add]")).toBeEnabled();
  });

  test("the settings dialog offers a reminder no digest, and says why", async ({ page }) => {
    // Plan decision 8: a reminder held back for tomorrow morning's digest is not
    // a reminder, so the email cell for this type offers only off and immediate,
    // with the server's own wording underneath.
    await page.goto("/");
    await page.locator("[data-testid=user-menu]").click();
    await page.locator("[data-testid=menu-notification-settings]").click();
    const settings = page.locator("[data-testid=notification-settings]");
    await expect(settings).toBeVisible({ timeout: 5_000 });

    const row = settings.locator("[data-testid=notification-type-reminder_due]");
    await expect(row).toContainText("A reminder I set comes due");
    await expect(
      settings.locator("[data-testid=notification-hint-reminder_due-email]")
    ).toContainText("cannot go into a digest");

    await settings.locator("[data-testid=notification-mode-reminder_due-email]").click();
    await expect(page.getByRole("option", { name: "Daily summary" })).toHaveCount(0);
    await expect(page.getByRole("option", { name: "Hourly summary" })).toHaveCount(0);
    // Exact, because the inherit entry is labelled "Default (As it happens)".
    await expect(page.getByRole("option", { name: "As it happens", exact: true })).toBeVisible();
  });
});
