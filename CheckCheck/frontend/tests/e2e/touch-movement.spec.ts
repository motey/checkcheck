/**
 * Touch (mobile) drag behaviour tests — runs in the "mobile" Playwright project
 * (Pixel 7 = hasTouch + mobile viewport/UA).
 *
 * Scope / why this shape:
 * These assert the behaviour our mobile DnD fix INTRODUCED and that is reliably
 * observable in automation:
 *   1. A plain tap on a card still opens the editor — the longPress we added to
 *      the board must NOT swallow taps.
 *   2. A press-and-hold ARMS the card drag (adds .dnd-longpress) after the
 *      250 ms threshold, while a quick tap does NOT — i.e. the longPress gate
 *      that lets a swipe scroll / a tap open, and only a hold pick up, works.
 *
 * What is deliberately NOT asserted here: the full drag-to-reorder-and-persist
 * on touch. @formkit/drag-and-drop's touch (synthetic) sort only advances for a
 * real device's pointer-before-touch event ordering, which neither hand-
 * dispatched PointerEvents nor CDP Input.dispatchTouchEvent reproduce faithfully
 * (the sort's remap/currentTargetValue state machine never lands). Desktop
 * reorder is covered by card-movement/item-movement; touch reorder is verified
 * manually on-device. If a future @formkit release makes synthetic touch-drag
 * automatable, restore a reorder assertion here.
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

async function centre(loc: Locator): Promise<{ x: number; y: number }> {
  const b = await loc.boundingBox();
  if (!b) throw new Error("Could not read bounding box");
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}

/**
 * Press a touch pointer down on `loc` for `holdMs`, then lift. Returns whether
 * the board's longPress pick-up class (.dnd-longpress) appeared while held — i.e.
 * whether the synthetic drag armed.
 *
 * Uses a hand-dispatched (cancelable) touch PointerEvent rather than CDP touch
 * input on purpose: @formkit/drag-and-drop only ADDS longPressClass when the
 * originating pointerdown is cancelable, and CDP-synthesised pointerdowns are
 * not — so the observable cue only appears for a cancelable event. A real
 * device's touchstart IS cancelable, so this mirrors the on-device cue.
 */
async function touchHold(page: Page, loc: Locator, holdMs: number): Promise<boolean> {
  const p = await centre(loc);
  await loc.evaluate((el, { x, y }) => {
    el.dispatchEvent(
      new PointerEvent("pointerdown", {
        pointerType: "touch",
        pointerId: 1,
        isPrimary: true,
        button: 0,
        clientX: x,
        clientY: y,
        bubbles: true,
        cancelable: true,
        composed: true,
      })
    );
  }, p);
  await page.waitForTimeout(holdMs);
  const armed = await page.evaluate(() => !!document.querySelector(".dnd-longpress"));
  // Release so the next interaction starts clean.
  await page.evaluate(({ x, y }) => {
    document.dispatchEvent(
      new PointerEvent("pointerup", {
        pointerType: "touch",
        pointerId: 1,
        isPrimary: true,
        clientX: x,
        clientY: y,
        bubbles: true,
        cancelable: true,
        composed: true,
      })
    );
  }, p);
  return armed;
}

// ── tests ─────────────────────────────────────────────────────────────────────

test.describe("touch movement", () => {
  test.setTimeout(30_000);

  const cleanup: string[] = [];
  test.afterEach(async ({ page }) => {
    for (const id of cleanup) await apiDelete(page, `/api/checklist/${id}`);
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("a plain tap on a card opens the editor (longPress does not swallow taps)", async ({
    page,
  }) => {
    const name = `Touch-Tap-${Date.now()}`;
    const cl = await apiPost(page, "/api/checklist", { name });
    cleanup.push(cl.id);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    const card = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: name });
    await expect(card).toBeVisible();

    // A real touch tap must open the edit modal, not start a drag.
    await card.locator("[data-testid=card-title]").tap();
    await expect(page.locator('[role="dialog"]')).toBeVisible({ timeout: 5_000 });
  });

  test("press-and-hold arms the card drag; a quick tap does not", async ({ page }) => {
    const name = `Touch-Hold-${Date.now()}`;
    const cl = await apiPost(page, "/api/checklist", { name });
    cleanup.push(cl.id);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    const card = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: name });
    await expect(card).toBeVisible();

    // A brief touch (below the 250 ms longPress threshold) must NOT pick the
    // card up — otherwise a normal tap/scroll would start a drag.
    expect(await touchHold(page, card, 120), "quick tap should not arm a drag").toBe(false);

    // A press-and-hold past the threshold arms the drag (adds .dnd-longpress).
    expect(await touchHold(page, card, 400), "press-hold should arm the drag").toBe(true);
  });
});
