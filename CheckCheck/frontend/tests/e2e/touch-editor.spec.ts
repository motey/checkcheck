/**
 * Full-screen card editor on phones (docs/plans/MOBILE_EDITOR.md, M2 and M3).
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
 *
 * M3: the uncheck-suggestion list under a focused item scrolls into the part
 * of the editor above the (simulated) keyboard, without pushing the item
 * itself out of the top.
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

async function apiPatch(page: Page, path: string, body: object) {
  const res = await page.request.patch(path, {
    data: body,
    headers: { "Content-Type": "application/json" },
  });
  expect(res.ok(), `PATCH ${path} failed: ${res.status()}`).toBeTruthy();
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

  async function openCard(page: Page, name: string, items = 0, checked: string[] = []) {
    const cl = await apiPost(page, "/api/checklist", { name });
    cleanup.push(cl.id);
    for (const text of checked) {
      const it = await apiPost(page, `/api/checklist/${cl.id}/item`, { text });
      await apiPatch(page, `/api/checklist/${cl.id}/item/${it.id}/state`, { checked: true });
    }
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
    // Checked rows stay mounted (collapsed section), so they count too.
    if (items) await expect(itemRows(dialog)).toHaveCount(items + checked.length, { timeout: 10_000 });
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

  // Focus the last unchecked item, shrink the viewport (simulated keyboard) and
  // park the row flush against the bottom edge, the worst case the browser's
  // own caret scrolling leaves behind. Then type a prefix of a checked item.
  async function typeAtKeyboardEdge(page: Page, dialog: Locator, height: number, text: string) {
    // Row 30 is the last unchecked item; the checked rows render after it. Not
    // filtered by text: the text leaves the DOM once the textarea swaps in.
    const lastRow = itemRows(dialog).nth(29);
    await expect(lastRow).toContainText("Item 30");
    await lastRow.scrollIntoViewIfNeeded();
    await lastRow.getByTestId("item-text-rendered").tap();
    const editor = lastRow.getByTestId("item-text-editor");
    await expect(editor).toBeFocused();

    const full = page.viewportSize()!;
    await page.setViewportSize({ width: full.width, height });
    await expect.poll(async () => Math.round((await dialog.boundingBox())!.height)).toBe(height);

    await lastRow.evaluate((el) => el.scrollIntoView({ block: "end", behavior: "instant" }));
    await expect(editor).toBeInViewport({ ratio: 1 });
    await editor.fill(text);
    return { lastRow, editor };
  }

  test("a suggestion under the last item shows above the keyboard and unchecks", async ({ page }) => {
    const dialog = await openCard(page, `TE-Suggest-${Date.now()}`, 30, ["Milk"]);
    const full = page.viewportSize()!;
    const { editor } = await typeAtKeyboardEdge(page, dialog, Math.round(full.height / 2), "Mi");

    const suggestion = dialog.getByTestId("uncheck-suggestion");
    await expect(suggestion).toHaveCount(1);
    await expect(suggestion).toContainText("Milk");
    await expect(suggestion).toBeInViewport({ ratio: 1 });
    await expect(editor).toBeInViewport();
    await expect(editor).toBeFocused();

    await suggestion.tap();
    await expect(dialog.getByTestId("uncheck-suggestions")).toHaveCount(0);
    // The typed duplicate is gone and the revived "Milk" took its slot, unchecked
    // and focused (so its text is in the textarea, not the row's text content).
    await expect(itemRows(dialog)).toHaveCount(30);
    const milkRow = itemRows(dialog).nth(29);
    await expect(milkRow.getByTestId("item-text-editor")).toHaveValue("Milk");
    await expect(milkRow.getByRole("checkbox")).not.toBeChecked();
  });

  test("a long suggestion list never pushes the typed item out of the top", async ({ page }) => {
    const checked = ["Milk 1", "Milk 2", "Milk 3", "Milk 4", "Milk 5"];
    const dialog = await openCard(page, `TE-SuggestTop-${Date.now()}`, 30, checked);
    // So little room that row plus five suggestions cannot fit at once.
    const { editor } = await typeAtKeyboardEdge(page, dialog, 300, "Mi");

    await expect(dialog.getByTestId("uncheck-suggestion")).toHaveCount(5);
    // The config runs with reducedMotion "reduce", so the reveal is instant.
    await expect(editor).toBeInViewport({ ratio: 1 });
    await expect(editor).toBeFocused();
    // And the list did move up into view as far as that allows.
    await expect(dialog.getByTestId("uncheck-suggestion").first()).toBeInViewport();
  });
});
