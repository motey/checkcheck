/**
 * Label reordering test.
 *
 * Regression for "Re-ordering labels does not work": the label editor lets you
 * drag labels around, but the new order was only applied client-side and never
 * persisted (the store never called PUT /api/label/sort), so it had no effect
 * on the actual label list and was lost on reload.
 *
 * The label editor is opened via the ?editlabels=true query param. Each label
 * renders as a <li> inside the manager's <ul> with a drag handle
 * (.label-item-drag-handle) and a single name <input>. Labels are displayed by
 * descending sort_order, and a freshly created label gets the highest
 * sort_order, so the initial top→bottom order is newest → oldest.
 *
 * DOM note: the name input's value is set reactively (DOM property), so
 * attribute selectors like input[value="…"] do NOT match — always read order
 * via inputValue().
 */
import { test, expect, type Locator, type Page } from "@playwright/test";

// ── helpers ──────────────────────────────────────────────────────────────────

async function apiPost(page: Page, path: string, body: object) {
  const res = await page.request.post(path, {
    data: body,
    headers: { "Content-Type": "application/json" },
  });
  expect(res.ok(), `POST ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

async function apiDelete(page: Page, path: string) {
  await page.request.delete(path).catch(() => {});
}

/** Ordered list of label names in the manager, read from each row's input. */
async function labelOrder(page: Page): Promise<string[]> {
  const inputs = page.locator('[role="dialog"] ul li input');
  const count = await inputs.count();
  const values: string[] = [];
  for (let i = 0; i < count; i++) values.push(await inputs.nth(i).inputValue());
  return values;
}

/**
 * Drag via low-level pointer events to reliably pass @formkit/drag-and-drop's
 * activation threshold. targetYFraction 0.8 = "release 80 % down" = drop after.
 */
async function drag(page: Page, source: Locator, target: Locator, targetYFraction = 0.8) {
  const srcBox = await source.boundingBox();
  const tgtBox = await target.boundingBox();
  if (!srcBox || !tgtBox) throw new Error("Could not read bounding boxes for drag");

  const srcX = srcBox.x + srcBox.width / 2;
  const srcY = srcBox.y + srcBox.height / 2;
  const tgtX = tgtBox.x + tgtBox.width / 2;
  const tgtY = tgtBox.y + tgtBox.height * targetYFraction;

  await page.mouse.move(srcX, srcY);
  await page.mouse.down();
  await page.mouse.move(srcX + 2, srcY + 6, { steps: 5 });
  await page.mouse.move(tgtX, tgtY, { steps: 30 });
  await page.mouse.up();
}

// ── tests ─────────────────────────────────────────────────────────────────────

test.describe("label reorder", () => {
  // DnD + sort API + reload: allow 25 s before declaring a hang.
  test.setTimeout(25_000);

  const cleanupLabels: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanupLabels) await apiDelete(page, `/api/label/${id}`);
    cleanupLabels.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("dragging a label reorders the list and persists after reload", async ({ page }) => {
    const tag = Date.now();
    const nameA = `LblA-${tag}`;
    const nameB = `LblB-${tag}`;
    const nameC = `LblC-${tag}`;

    // Created oldest → newest. Newest (C) has the highest sort_order and is
    // displayed at the top, so the initial top→bottom order is C, B, A.
    const a = await apiPost(page, "/api/label", { display_name: nameA });
    cleanupLabels.push(a.id);
    const b = await apiPost(page, "/api/label", { display_name: nameB });
    cleanupLabels.push(b.id);
    const c = await apiPost(page, "/api/label", { display_name: nameC });
    cleanupLabels.push(c.id);

    await page.goto("/?editlabels=true");
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // Wait until all three of our labels are rendered, then confirm C is above B.
    await expect
      .poll(async () => (await labelOrder(page)).filter((n) => [nameA, nameB, nameC].includes(n)))
      .toEqual([nameC, nameB, nameA]);

    const orderBefore = await labelOrder(page);
    const idxC = orderBefore.indexOf(nameC);
    const idxB = orderBefore.indexOf(nameB);

    // Drag the top label (C) down past B. Use the drag handle and wait for the
    // sort request — that PUT is exactly what was missing before the fix.
    const rows = dialog.locator("ul li");
    const handleC = rows.nth(idxC).locator(".label-item-drag-handle");
    await expect(handleC).toBeVisible({ timeout: 3_000 });

    const sortResponse = page.waitForResponse(
      (r) => r.url().includes("/api/label/sort") && r.request().method() === "PUT",
      { timeout: 10_000 }
    );
    await drag(page, handleC, rows.nth(idxB));
    await sortResponse;

    // After the drag, B should be above C.
    await expect
      .poll(async () => {
        const order = await labelOrder(page);
        return order.indexOf(nameB) < order.indexOf(nameC);
      })
      .toBe(true);

    // Reload the editor from scratch — the new order must come back from the
    // backend (this is what failed before: the order was client-side only).
    await page.goto("/?editlabels=true");
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    await expect
      .poll(async () => {
        const order = (await labelOrder(page)).filter((n) =>
          [nameA, nameB, nameC].includes(n)
        );
        return order.indexOf(nameB) < order.indexOf(nameC);
      })
      .toBe(true);
  });

  // Chunk E: label CRUD is online-only (WI-12). Offline, the editor must degrade
  // gracefully — a visible "you're offline" notice + disabled controls — rather
  // than a silent no-op click (the store's assertOnline guard is the backstop).
  test("label editor shows an offline notice and disables its controls offline", async ({
    page,
    context,
  }) => {
    const tag = Date.now();
    const name = `LblOff-${tag}`;
    const label = await apiPost(page, "/api/label", { display_name: name });
    cleanupLabels.push(label.id);

    await page.goto("/?editlabels=true");
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // Wait for our label row to render before flipping offline.
    await expect
      .poll(async () => (await labelOrder(page)).includes(name))
      .toBe(true);

    // Online first: the create input is usable and no notice is shown. (The Add
    // button stays disabled until a name is typed, so assert the input, not it.)
    await expect(dialog.locator("[data-testid=label-offline-notice]")).toHaveCount(0);
    await expect(dialog.locator("form input")).toBeEnabled();

    // Go offline — the window `offline` event drives the connectivity signal.
    await context.setOffline(true);

    // The notice appears and every mutating control disables.
    await expect(dialog.locator("[data-testid=label-offline-notice]")).toBeVisible({
      timeout: 5_000,
    });
    await expect(dialog.locator('form button[type="submit"]')).toBeDisabled();
    await expect(dialog.locator("form input")).toBeDisabled();
    await expect(dialog.locator("ul li input").first()).toBeDisabled();

    // Back online: the notice clears and the create control re-enables.
    await context.setOffline(false);
    await expect(dialog.locator("[data-testid=label-offline-notice]")).toHaveCount(0, {
      timeout: 5_000,
    });
    await expect(dialog.locator("form input")).toBeEnabled();
  });
});
