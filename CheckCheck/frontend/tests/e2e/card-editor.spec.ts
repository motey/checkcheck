/**
 * Card editor modal — open/close behaviour.
 *
 * Guards the fix for the "card reopens / needs multiple clicks to close" bug:
 * the editor used to double-mount (two [role=dialog] roots) via the imperative
 * useOverlay wiring. It's now a single declarative <UModal v-model:open> driven
 * by the /card/<id> route, so exactly one dialog renders and one click closes.
 *
 * Runs in the "chromium" project with the pre-loaded admin session.
 */
import { test, expect } from "@playwright/test";

// Close the board's SSE stream before teardown (see checklist.spec.ts).
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

test.describe("card editor modal", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  // The editor is identified by its content — only the editor renders the
  // `.checklist` container inside a dialog.
  const editorDialog = (page: import("@playwright/test").Page) =>
    page.locator('[role="dialog"]:has(.checklist)');

  test("opening a new card renders exactly one editor dialog", async ({ page }) => {
    await page.getByRole("button", { name: "New Check List" }).click();

    await expect(page).toHaveURL(/\/card\//, { timeout: 5_000 });
    // The core regression guard: never more than one dialog instance.
    await expect(editorDialog(page)).toHaveCount(1);
    await expect(editorDialog(page)).toBeVisible();
  });

  // Regression: the title and the notes used to share ONE debounce timer, so
  // touching the notes within 500ms of the title replaced the queued title write
  // and it never reached the server. Each field now has its own timer
  // (`useDebouncedCardFields`, pinned deterministically in
  // tests/unit/debouncedCardFields.spec.ts); this is the same thing through the
  // real editor.
  test("editing the notes right after the title persists both fields", async ({ page }) => {
    const title = `DEB-${Date.now()}`;
    const res = await page.request.post("/api/checklist", {
      data: { name: title, text: "notes before" },
      headers: { "Content-Type": "application/json" },
    });
    expect(res.ok(), "card create should succeed").toBeTruthy();
    const cl = await res.json();

    try {
      await page.goto("/");
      await page.locator("[data-testid=card-title]", { hasText: title }).first().click();
      const dialog = editorDialog(page);
      await expect(dialog).toBeVisible();

      // Retype the title, then move straight on to the notes without pausing, so
      // the two edits land in the same debounce window.
      await dialog.locator('textarea[placeholder="Enter a checklist title..."]').fill(`${title}-edited`);
      await dialog.locator("[data-testid=card-notes-rendered]").click();
      await dialog.locator("[data-testid=card-notes-textarea]").fill("notes after");

      await expect
        .poll(
          async () => {
            const got = await page.request.get(`/api/checklist/${cl.id}`);
            if (!got.ok()) return null;
            const card = await got.json();
            return `${card.name}|${card.text}`;
          },
          { timeout: 10_000, intervals: [200, 400, 800] }
        )
        .toBe(`${title}-edited|notes after`);
    } finally {
      await page.request.delete(`/api/checklist/${cl.id}`).catch(() => {});
    }
  });

  test("close button dismisses the editor in one click and returns to the board", async ({ page }) => {
    await page.getByRole("button", { name: "New Check List" }).click();
    const dialog = editorDialog(page);
    await expect(dialog).toBeVisible();

    await dialog.getByRole("button", { name: "Close" }).click();

    await expect(editorDialog(page)).toHaveCount(0, { timeout: 5_000 });
    await expect(page).not.toHaveURL(/\/card\//);
  });
});
