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

  test("close button dismisses the editor in one click and returns to the board", async ({ page }) => {
    await page.getByRole("button", { name: "New Check List" }).click();
    const dialog = editorDialog(page);
    await expect(dialog).toBeVisible();

    await dialog.getByRole("button", { name: "Close" }).click();

    await expect(editorDialog(page)).toHaveCount(0, { timeout: 5_000 });
    await expect(page).not.toHaveURL(/\/card\//);
  });
});
