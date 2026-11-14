/**
 * Note-only cards on a phone (issue #22). Runs in the "mobile" project (Pixel 7).
 *
 * - A note-only board card shows 8 lines of its note instead of 2, with the
 *   line breaks kept.
 * - In the open card the reminders block is folded into a bell with a count
 *   badge; tapping it unfolds the details in place (no second dialog), and the
 *   kebab's "Set a reminder" still opens the form.
 *
 * Desktop behaviour is note-cards.spec.ts.
 */
import { test, expect, type Locator, type Page } from "@playwright/test";

async function apiPost(page: Page, path: string, body: object) {
  const res = await page.request.post(path, {
    data: body,
    headers: { "Content-Type": "application/json" },
  });
  expect(res.ok(), `POST ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

const editorDialog = (page: Page) => page.locator('[role="dialog"]:has(.checklist)');
const LONG_NOTE = Array.from({ length: 14 }, (_, i) => `Line ${i + 1}`).join("\n");

test.describe("note-only cards (phone)", () => {
  const cleanup: string[] = [];
  test.afterEach(async ({ page }) => {
    for (const id of cleanup) await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  async function card(page: Page, name: string, text = "", items: string[] = []) {
    const cl = await apiPost(page, "/api/checklist", { name, text });
    cleanup.push(cl.id);
    for (const t of items) await apiPost(page, `/api/checklist/${cl.id}/item`, { text: t });
    return cl.id as string;
  }

  async function openCard(page: Page, name: string): Promise<Locator> {
    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await page.locator("[data-testid=card-title]", { hasText: name }).first().tap();
    const dialog = editorDialog(page);
    await expect(dialog).toBeVisible({ timeout: 5_000 });
    await dialog.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
    return dialog;
  }

  test("a note-only board card shows 8 lines, a card with items 2", async ({ page }) => {
    const tag = Date.now();
    const noteOnly = `PhoneNote-${tag}`;
    const mixed = `PhoneMixed-${tag}`;
    await card(page, noteOnly, LONG_NOTE);
    await card(page, mixed, LONG_NOTE, [`item-${tag}`]);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");

    const metrics = (name: string) =>
      page
        .locator("[data-testid=checklist-board] .checklist-preview")
        .filter({ hasText: name })
        .getByTestId("card-notes-preview")
        .evaluate((el) => {
          const style = getComputedStyle(el);
          return {
            clamp: style.getPropertyValue("-webkit-line-clamp"),
            lines: Math.round(el.clientHeight / parseFloat(style.lineHeight)),
          };
        });

    expect(await metrics(noteOnly)).toEqual({ clamp: "8", lines: 8 });
    expect(await metrics(mixed)).toEqual({ clamp: "2", lines: 2 });
  });

  test("reminders fold into a bell with a count, and unfold on tap", async ({ page }) => {
    const name = `PhoneBell-${Date.now()}`;
    const id = await card(page, name, "A note");
    for (const note of ["first", "second"]) {
      await apiPost(page, `/api/checklist/${id}/reminders`, {
        remind_at: "2090-03-01T09:00:00+00:00",
        recurrence: "none",
        note,
      });
    }

    const dialog = await openCard(page, name);
    const bell = dialog.getByTestId("card-reminders-bell");
    await expect(bell).toBeVisible();
    await expect(dialog.getByTestId("card-reminders")).toHaveCount(0);
    // The count arrives with the section's own fetch.
    await expect(bell.getByTestId("card-reminders-bell-count")).toHaveText("2", { timeout: 5_000 });
    await expect(bell).toHaveAttribute("aria-label", "Reminders, 2 set");
    const box = (await bell.boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeGreaterThanOrEqual(44);

    // Tap: the details unfold inside the same dialog.
    await bell.tap();
    const section = dialog.getByTestId("card-reminders");
    await expect(section).toBeVisible();
    await expect(section.getByTestId("card-reminder-row")).toHaveCount(2);
    await expect(dialog.getByTestId("card-reminders-bell")).toHaveCount(0);
    await expect(page.locator('[role="dialog"]')).toHaveCount(1);

    // And fold back.
    await section.getByTestId("card-reminders-fold").tap();
    await expect(dialog.getByTestId("card-reminders")).toHaveCount(0);
    await expect(dialog.getByTestId("card-reminders-bell")).toBeVisible();
  });

  test("without reminders the bell has no badge, and the kebab still opens the form", async ({
    page,
  }) => {
    const name = `PhoneBellEmpty-${Date.now()}`;
    await card(page, name, "A note");

    const dialog = await openCard(page, name);
    const bell = dialog.getByTestId("card-reminders-bell");
    await expect(bell).toBeVisible();
    await expect(bell).toHaveAttribute("aria-label", "Reminders, none set");
    await expect(bell.getByTestId("card-reminders-bell-count")).toHaveCount(0);

    await dialog.locator('button:has([class*="ellipsis-vertical"])').first().tap();
    await page.getByRole("menuitem", { name: "Set a reminder" }).tap();
    await expect(dialog.getByTestId("card-reminder-form")).toBeVisible({ timeout: 5_000 });
    await expect(dialog.getByTestId("card-reminders-bell")).toHaveCount(0);
  });
});
