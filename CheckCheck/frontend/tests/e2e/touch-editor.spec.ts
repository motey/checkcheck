/**
 * Full-screen card editor on phones (docs/plans/MOBILE_EDITOR.md, M2).
 *
 * Runs in the "mobile" Playwright project (Pixel 7). Below the `sm` breakpoint
 * the editor fills the screen and carries a header bar: a 44px back arrow
 * (aria-label "Close") on the left, pin and sync state on the right, the title
 * underneath. These tests guard that the close control and the title stay
 * reachable however long the card is and while the keyboard is open.
 *
 * The keyboard is simulated by halving the viewport height after focusing an
 * item. That is exactly what `interactive-widget=resizes-content` makes Android
 * Chrome do; iOS Safari (which goes through useVisualViewport instead) needs
 * the on-device check in the plan's section 5.
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
const titleField = (dialog: Locator) =>
  dialog.locator('textarea[placeholder="Enter a checklist title..."]');
const itemRows = (dialog: Locator) => dialog.getByTestId("item-row");

test.describe("touch editor (full screen)", () => {
  const cleanup: string[] = [];
  test.afterEach(async ({ page }) => {
    for (const id of cleanup) await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanup.length = 0;
    // Close the board's SSE stream before teardown (see checklist.spec.ts).
    await page.goto("about:blank").catch(() => {});
  });

  async function openCard(page: Page, name: string, items = 0) {
    const cl = await apiPost(page, "/api/checklist", { name });
    cleanup.push(cl.id);
    for (let i = 1; i <= items; i++) {
      await apiPost(page, `/api/checklist/${cl.id}/item`, { text: `Item ${i}` });
    }
    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await page.locator("[data-testid=card-title]", { hasText: name }).first().tap();
    const dialog = editorDialog(page);
    await expect(dialog).toBeVisible({ timeout: 5_000 });
    await expect(page).toHaveURL(/\/card\//);
    // Measure only after the modal's scale-in animation has settled.
    await dialog.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
    if (items) await expect(itemRows(dialog)).toHaveCount(items, { timeout: 10_000 });
    return dialog;
  }

  test("the editor fills the screen with a 44px back arrow clear of the pin", async ({ page }) => {
    const dialog = await openCard(page, `TE-Header-${Date.now()}`);
    const viewport = page.viewportSize()!;

    const box = (await dialog.boundingBox())!;
    expect(Math.round(box.x)).toBe(0);
    expect(Math.round(box.y)).toBe(0);
    expect(Math.round(box.width)).toBe(viewport.width);
    expect(Math.round(box.height)).toBe(viewport.height);

    const close = dialog.getByRole("button", { name: "Close" });
    // Exactly one close control: the desktop X is not rendered on phones.
    await expect(close).toHaveCount(1);
    await expect(close).toBeInViewport();
    const closeBox = (await close.boundingBox())!;
    expect(closeBox.width).toBeGreaterThanOrEqual(44);
    expect(closeBox.height).toBeGreaterThanOrEqual(44);

    const pin = dialog.getByTestId("pin-button");
    await expect(pin).toBeInViewport();
    const pinBox = (await pin.boundingBox())!;
    expect(pinBox.width).toBeGreaterThanOrEqual(44);
    expect(pinBox.height).toBeGreaterThanOrEqual(44);
    // Back arrow on the left, pin on the right, with a visible gap between.
    expect(closeBox.x + closeBox.width).toBeLessThan(pinBox.x - 8);

    // The title sits below the header bar, not under it.
    const titleBox = (await titleField(dialog).boundingBox())!;
    expect(titleBox.y).toBeGreaterThanOrEqual(closeBox.y + closeBox.height - 1);
  });

  test("tapping the back arrow closes the editor and returns to the board", async ({ page }) => {
    const dialog = await openCard(page, `TE-Close-${Date.now()}`);
    await dialog.getByRole("button", { name: "Close" }).tap();
    await expect(editorDialog(page)).toHaveCount(0, { timeout: 5_000 });
    await expect(page).not.toHaveURL(/\/card\//);
  });

  test("browser back closes the editor", async ({ page }) => {
    await openCard(page, `TE-Back-${Date.now()}`);
    await page.goBack();
    await expect(editorDialog(page)).toHaveCount(0, { timeout: 5_000 });
    await expect(page).not.toHaveURL(/\/card\//);
  });

  test("close and title stay visible at the bottom of a 40-item card", async ({ page }) => {
    const dialog = await openCard(page, `TE-Long-${Date.now()}`, 40);
    const last = itemRows(dialog).last();

    await last.scrollIntoViewIfNeeded();
    await expect(last).toBeInViewport();
    await expect(dialog.getByRole("button", { name: "Close" })).toBeInViewport();
    await expect(titleField(dialog)).toBeInViewport();
  });

  test("close and title stay visible with the keyboard open", async ({ page }) => {
    const dialog = await openCard(page, `TE-Keyboard-${Date.now()}`, 40);
    const lastRow = itemRows(dialog).last();

    // Items are focus-swap: tapping the rendered text swaps in the textarea.
    await lastRow.scrollIntoViewIfNeeded();
    await lastRow.getByTestId("item-text-rendered").tap();
    const last = lastRow.getByTestId("item-text-editor");
    await expect(last).toBeFocused();

    // Simulated keyboard: the layout viewport shrinks to half its height.
    const full = page.viewportSize()!;
    await page.setViewportSize({ width: full.width, height: Math.round(full.height / 2) });

    await expect
      .poll(async () => Math.round((await dialog.boundingBox())!.height))
      .toBe(Math.round(full.height / 2));
    await expect(dialog.getByRole("button", { name: "Close" })).toBeInViewport();
    await expect(titleField(dialog)).toBeInViewport();
    // The focused item is still reachable in the shrunken scroll region.
    await last.scrollIntoViewIfNeeded();
    await expect(last).toBeInViewport();
    await expect(last).toBeFocused();
  });

  test("a long title is capped and the list below stays usable", async ({ page }) => {
    const long = Array.from({ length: 12 }, (_, i) => `Very long title line ${i + 1}`).join(" ");
    const dialog = await openCard(page, `TE-Title-${Date.now()} ${long}`, 5);
    const title = titleField(dialog);
    const viewport = page.viewportSize()!;

    const titleBox = (await title.boundingBox())!;
    // Four rows of text-xl, then it scrolls inside itself.
    expect(titleBox.height).toBeLessThan(viewport.height / 4);
    expect(await title.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
    await expect(itemRows(dialog).first()).toBeInViewport();
  });
});
