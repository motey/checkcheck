/**
 * Shared plumbing for the docs-screenshot specs.
 *
 * The seeded dataset is deterministic (uuid5-derived ids, fixed RNG seed), so
 * the *data* in a shot is already reproducible. Everything here exists to make
 * the *rendering* reproducible too: fonts settled, no caret blink, no hover or
 * focus ring, scrolled to top. Without this, two runs against identical data
 * still produce different bytes and every regeneration churns the docs.
 */
import { expect, type Locator, type Page } from "@playwright/test";
import { mkdir } from "fs/promises";
import { resolve, dirname } from "path";

/** docs/screenshots/ resolved from CheckCheck/frontend/tests/screenshots/. */
export const SHOTS_DIR = resolve(__dirname, "../../../../docs/screenshots");

export type Theme = "light" | "dark";

/**
 * Force a colour theme before the app boots.
 *
 * @nuxtjs/color-mode reads its preference from localStorage under the key
 * configured in nuxt.config.ts (storageKey: "nuxt-color-mode") and falls back
 * to the system preference. Seeding the key via an init script covers the
 * app's own logic; emulateMedia covers any raw prefers-color-scheme CSS and
 * keeps the two from disagreeing on first paint.
 */
export async function applyTheme(page: Page, theme: Theme): Promise<void> {
  await page.emulateMedia({ colorScheme: theme });
  await page.addInitScript((value) => {
    try {
      window.localStorage.setItem("nuxt-color-mode", value);
    } catch {
      // Private-mode storage failures are not worth failing a screenshot over.
    }
  }, theme);
}

/**
 * Wait for the board to be fully painted and visually quiet.
 *
 * Must be called after navigation and after any interaction that changes what
 * is on screen (opening a dialog, typing) but before the screenshot.
 */
export async function stabilize(page: Page): Promise<void> {
  // 1. Web fonts. A shot taken before these resolve renders in the fallback
  //    face, which shifts every text baseline in the image.
  await page.evaluate(() => document.fonts.ready);

  // 2. Icons and card colours arrive with the data; give the network a beat to
  //    go quiet. The board holds an open SSE connection to /api/sync, so
  //    "networkidle" never fires — hence an explicit settle instead.
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(600);

  // 3. Park the pointer off-canvas so no card sits in :hover. Shots that need a
  //    hover-revealed element force it with CSS instead (see revealCardFooter);
  //    real hover is not usable while a dropdown holds the pointer-events lock.
  await page.mouse.move(-50, -50);

  // 4. Blinking carets and focus rings are the classic source of one-pixel
  //    diffs between otherwise identical runs.
  //
  //    Deliberately NOT zeroing animation/transition durations here: doing so
  //    while a modal's entrance animation is still in flight freezes it at its
  //    first keyframe, which produced washed-out half-transparent dialogs.
  //    Motion is already handled properly by reducedMotion:"reduce" in the
  //    config plus animations:"disabled" on each screenshot call, which
  //    fast-forwards animations to their final frame instead of dropping them.
  await page.addStyleTag({
    content: `
      *, *::before, *::after { caret-color: transparent !important; }
      *:focus, *:focus-visible { outline: none !important; box-shadow: none !important; }
      /* The scrollbar is rendered by the OS/browser theme and differs between
         machines; hide it so shots are portable. */
      ::-webkit-scrollbar { display: none !important; }
    `,
  });

  // 5. Guarantee a deterministic scroll offset.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(150);
}

/** Navigate to the board with a theme applied, then stabilize. */
export async function openBoard(page: Page, theme: Theme): Promise<void> {
  await applyTheme(page, theme);
  await page.goto("/");
  await expect(page.locator("[data-testid=checklist-board]")).toBeVisible();
  // The pinned section renders after the first data pull; waiting on it means
  // the grid is populated rather than an empty shell.
  await expect(page.locator("[data-testid=card-title]").first()).toBeVisible();
  await stabilize(page);
}

/**
 * The board opens a persistent SSE connection (/api/sync) that blocks Playwright
 * teardown if left open. Every spec navigates away in afterEach; this is the
 * same guard the E2E suite uses.
 */
export async function closeSse(page: Page): Promise<void> {
  await page.goto("about:blank").catch(() => {});
}

/**
 * Open the card editor on a card that shows the default layout well.
 *
 * Three constraints, all about the shot being representative:
 *
 * 1. **Separated checked items.** `checked_items_seperated` is the default, so
 *    the editor should show the collapsed "N checked items" divider rather than
 *    ticked items struck through inline. Seeded cards come in both modes, so
 *    candidates are filtered to those whose preview renders the divider.
 * 2. **Fits the dialog.** The fullest cards overflow, pushing that divider below
 *    the fold — exactly the part the shot is meant to show. Cards whose preview
 *    carries the "... + N items" overflow marker are dropped: rendering every
 *    item in the preview is a good proxy for fitting in the editor too.
 * 3. **Not empty.** Among what survives, the tallest card wins.
 *
 * All three are measured off the preview rather than hardcoded to a title, so
 * the shot survives the seeder renaming its content pool.
 *
 * Deliberately opens exactly ONE dialog. An earlier version probed candidates by
 * opening and dismissing editors, but a dismissed modal leaves its backdrop
 * mounted (the fade-out animation never fires under reducedMotion, so Reka never
 * unmounts it). Those backdrops stacked into a white veil over the shot and
 * swallowed later clicks.
 */
export async function openFullestCardEditor(page: Page): Promise<void> {
  const allCards = page.locator("[data-testid=checklist-board] .checklist-preview");
  await expect(allCards.first()).toBeVisible();

  // The "+ N checked items" divider only renders in separated mode.
  const separated = allCards.filter({ hasText: /\+\s*\d+\s*checked items/ });
  // "... + N items" means the preview truncated, i.e. a long list.
  const fitting = separated.filter({ hasNotText: /\.\.\.\s*\+\s*\d+\s*items/ });

  let cards = fitting;
  if ((await cards.count()) === 0) cards = separated;
  if ((await cards.count()) === 0) cards = allCards;

  const count = await cards.count();
  let bestIndex = 0;
  let bestHeight = -1;
  for (let i = 0; i < count; i++) {
    const box = await cards.nth(i).boundingBox();
    if (box && box.height > bestHeight) {
      bestHeight = box.height;
      bestIndex = i;
    }
  }

  await cards.nth(bestIndex).locator("[data-testid=card-title]").click();
  await expect(page.locator("[role=dialog]")).toBeVisible();
  await stabilize(page);
}

/**
 * Force every card's action footer visible.
 *
 * The footer is `opacity-0 group-hover/card:opacity-100` (CheckList.vue), so it
 * only appears on hover. Real hover cannot be used for shots that also have a
 * dropdown open: Reka UI puts a pointer-events lock on the body while a menu is
 * mounted, so the card underneath never enters :hover and the menu ends up
 * floating with no visible anchor. Overriding the opacity sidesteps the lock
 * and is deterministic besides.
 */
export async function revealCardFooter(page: Page): Promise<void> {
  await page.addStyleTag({
    content: `.checklist-footer { opacity: 1 !important; }`,
  });
}

/** Write a viewport screenshot to docs/screenshots/<name>.png. */
export async function shootPage(page: Page, name: string): Promise<void> {
  const path = resolve(SHOTS_DIR, `${name}.png`);
  await mkdir(dirname(path), { recursive: true });
  await page.screenshot({ path, animations: "disabled" });
}

/** Write a single element's bounding box to docs/screenshots/<name>.png. */
export async function shootElement(locator: Locator, name: string): Promise<void> {
  const path = resolve(SHOTS_DIR, `${name}.png`);
  await mkdir(dirname(path), { recursive: true });
  await locator.screenshot({ path, animations: "disabled" });
}

/**
 * Write the union of several elements' bounding boxes, plus padding.
 *
 * Needed for the "card + its open dropdown" style crops: the menu renders in a
 * portal outside the card's DOM subtree, so no single element encloses both.
 */
export async function shootUnion(
  page: Page,
  locators: Locator[],
  name: string,
  padding = 12
): Promise<void> {
  const boxes = [];
  for (const locator of locators) {
    const box = await locator.boundingBox();
    if (!box) throw new Error(`Cannot screenshot "${name}": a target has no bounding box`);
    boxes.push(box);
  }

  const left = Math.min(...boxes.map((b) => b.x));
  const top = Math.min(...boxes.map((b) => b.y));
  const right = Math.max(...boxes.map((b) => b.x + b.width));
  const bottom = Math.max(...boxes.map((b) => b.y + b.height));

  const viewport = page.viewportSize();
  const clip = {
    x: Math.max(0, left - padding),
    y: Math.max(0, top - padding),
    width: right - left + padding * 2,
    height: bottom - top + padding * 2,
  };
  // Clamp to the viewport; Playwright errors on a clip that runs past the edge.
  if (viewport) {
    clip.width = Math.min(clip.width, viewport.width - clip.x);
    clip.height = Math.min(clip.height, viewport.height - clip.y);
  }

  const path = resolve(SHOTS_DIR, `${name}.png`);
  await mkdir(dirname(path), { recursive: true });
  await page.screenshot({ path, clip, animations: "disabled" });
}
