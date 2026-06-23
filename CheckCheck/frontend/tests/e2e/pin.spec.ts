/**
 * Checklist pinning tests.
 *
 * Verifies that:
 *   1. Pinning a card via the corner button moves it into the "Pinned" section
 *      and persists after a full page reload; unpinning moves it back.
 *   2. Dragging a card from the normal list into the Pinned section pins it
 *      (cross-list drag toggles `pinned`) and persists after reload.
 *
 * Uses a narrow viewport (420 px) so the grid collapses to a single column.
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

/** Whether a card with the given name is rendered inside the given board section. */
async function inBoard(page: Page, boardTestId: string, name: string): Promise<boolean> {
  return (
    (await page
      .locator(`[data-testid=${boardTestId}] .checklist-preview`)
      .filter({ hasText: name })
      .count()) > 0
  );
}

/** Low-level pointer drag (mirrors card-movement.spec.ts) to clear FormKit's
 *  drag-activation threshold reliably. */
async function drag(page: Page, source: Locator, target: Locator, targetYFraction = 0.5) {
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

const isPositionPatch = (r: { url(): string; request(): { method(): string } }) =>
  r.url().includes("/position") && r.request().method() === "PATCH";

// ── tests ─────────────────────────────────────────────────────────────────────

test.describe("pinning", () => {
  test.setTimeout(25_000);

  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanup) await apiDelete(page, `/api/checklist/${id}`);
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("pinning a card via the corner button moves it to the Pinned section and persists", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 420, height: 900 });
    const name = `Pin-Card-${Date.now()}`;
    const cl = await apiPost(page, "/api/checklist", { name });
    cleanup.push(cl.id);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(page.getByText(name)).toBeVisible();

    // Starts in the normal list, not the pinned one.
    expect(await inBoard(page, "checklist-board", name)).toBeTruthy();
    expect(await inBoard(page, "pinned-board", name)).toBeFalsy();

    // Pin it.
    const card = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: name });
    const patch = page.waitForResponse(isPositionPatch, { timeout: 10_000 });
    await card.locator("[data-testid=pin-button]").click();
    await patch;

    // Now lives in the Pinned section.
    await expect(page.locator("[data-testid=pinned-section]")).toBeVisible();
    await expect
      .poll(() => inBoard(page, "pinned-board", name))
      .toBeTruthy();

    // Persists across reload.
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect
      .poll(() => inBoard(page, "pinned-board", name))
      .toBeTruthy();

    // Unpin → back to the normal list.
    const pinnedCard = page
      .locator("[data-testid=pinned-board] .checklist-preview")
      .filter({ hasText: name });
    const patchOff = page.waitForResponse(isPositionPatch, { timeout: 10_000 });
    await pinnedCard.locator("[data-testid=pin-button]").click();
    await patchOff;
    await expect
      .poll(() => inBoard(page, "checklist-board", name))
      .toBeTruthy();
  });

  test("REPRO: pinning a card in a paginated (large) list shows it without reload", async ({
    page,
  }) => {
    // Short viewport so the load-more trigger stays off-screen → the board keeps
    // only the first page loaded and pagination (length < total) stays active,
    // matching the user's 20+ card scenario.
    await page.setViewportSize({ width: 420, height: 700 });
    const tag = Date.now();
    const ids: string[] = [];
    for (let i = 0; i < 13; i++) {
      const cl = await apiPost(page, "/api/checklist", { name: `Big-${tag}-${i}` });
      ids.push(cl.id);
      cleanup.push(cl.id);
    }
    // The last-created card has the highest index → top of the first page.
    const target = `Big-${tag}-12`;

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(page.getByText(target)).toBeVisible();
    expect(await inBoard(page, "checklist-board", target)).toBeTruthy();

    const patch = page.waitForResponse(isPositionPatch, { timeout: 10_000 });
    await page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: target })
      .locator("[data-testid=pin-button]")
      .click();
    await patch;

    // Must appear in the Pinned section WITHOUT a reload.
    await expect.poll(() => inBoard(page, "pinned-board", target), { timeout: 5_000 }).toBeTruthy();
  });

  test("REPRO2: pinning a card scrolled far down brings it into view (no reload needed)", async ({
    page,
  }) => {
    // Normal short viewport so the board's <main> scroll container actually
    // scrolls — this is the user's 20+ card scenario where the pinned section
    // (top of the container) is off-screen from where they pin.
    await page.setViewportSize({ width: 420, height: 700 });
    const errors: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    const tag = Date.now();
    for (let i = 0; i < 16; i++) {
      const cl = await apiPost(page, "/api/checklist", { name: `Many-${tag}-${i}` });
      cleanup.push(cl.id);
    }
    // Lowest index → bottom of the list (created first).
    const target = `Many-${tag}-0`;

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");

    // Scroll the board container to the bottom repeatedly to lazy-load all cards.
    await expect
      .poll(
        async () => {
          await page.locator("main").evaluate((el) => el.scrollTo(0, el.scrollHeight));
          return page.locator("[data-testid=checklist-board] .checklist-preview").count();
        },
        { timeout: 15_000 }
      )
      .toBeGreaterThanOrEqual(16);

    const targetCard = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: target });
    await targetCard.scrollIntoViewIfNeeded();
    // Pinned section is at the top of the scrolled container → currently off-screen.
    await expect(page.locator("[data-testid=pinned-section]")).not.toBeInViewport();

    const patch = page.waitForResponse(isPositionPatch, { timeout: 10_000 });
    await targetCard.locator("[data-testid=pin-button]").click();
    await patch;

    // State: card is now in the pinned section.
    await expect.poll(() => inBoard(page, "pinned-board", target), { timeout: 5_000 }).toBeTruthy();
    // UX fix: the pinned section (with the card) is scrolled into view — no reload.
    const pinnedCard = page
      .locator("[data-testid=pinned-board] .checklist-preview")
      .filter({ hasText: target });
    await expect(pinnedCard).toBeInViewport({ timeout: 5_000 });
    expect(errors, `console errors: ${errors.join(" | ")}`).toEqual([]);
  });

  test("dragging a card into the Pinned section pins it and persists", async ({ page }) => {
    await page.setViewportSize({ width: 420, height: 900 });
    const tag = Date.now();
    const anchor = `Pin-Anchor-${tag}`;
    const mover = `Pin-Mover-${tag}`;
    const clAnchor = await apiPost(page, "/api/checklist", { name: anchor });
    cleanup.push(clAnchor.id);
    const clMover = await apiPost(page, "/api/checklist", { name: mover });
    cleanup.push(clMover.id);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(page.getByText(anchor)).toBeVisible();
    await expect(page.getByText(mover)).toBeVisible();

    // Pin the anchor first so the Pinned section exists as a drop target.
    const anchorCard = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: anchor });
    const patch = page.waitForResponse(isPositionPatch, { timeout: 10_000 });
    await anchorCard.locator("[data-testid=pin-button]").click();
    await patch;
    await expect.poll(() => inBoard(page, "pinned-board", anchor)).toBeTruthy();

    // Drag the mover from the normal list onto the pinned anchor.
    const moverCard = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: mover });
    const pinnedAnchor = page
      .locator("[data-testid=pinned-board] .checklist-preview")
      .filter({ hasText: anchor });
    const patchMover = page.waitForResponse(isPositionPatch, { timeout: 10_000 });
    await drag(page, moverCard, pinnedAnchor);
    await patchMover;

    await expect.poll(() => inBoard(page, "pinned-board", mover)).toBeTruthy();

    // Persists across reload.
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => inBoard(page, "pinned-board", mover)).toBeTruthy();
  });
});
