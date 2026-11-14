/**
 * Note-only cards and readable notes on the board (issue #22), desktop.
 *
 * - The board preview keeps the line breaks of a note (all cards).
 * - A card without items shows far more of its note (20 lines instead of 3),
 *   has no empty item block and no minimum height.
 * - "0 checked items" never renders: not on the board, not in the open card.
 * - A view-only public link to a note-only card shows just the notes, while a
 *   card with neither notes nor items still says it has no items.
 *
 * The phone side (12 note lines, the reminder bell) is touch-note-cards.spec.ts.
 */
import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";

async function apiPost(page: Page, path: string, body: object) {
  const res = await page.request.post(path, {
    data: body,
    headers: { "Content-Type": "application/json" },
  });
  expect(res.ok(), `POST ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

/** Twenty-four short lines: more than the long clamp shows, so it must cut. */
const LONG_NOTE = Array.from({ length: 24 }, (_, i) => `Line ${i + 1}`).join("\n");
/** Six short lines: fits in the long clamp, not in the short one. */
const SIX_LINES = Array.from({ length: 6 }, (_, i) => `Row ${i + 1}`).join("\n");

const boardCard = (page: Page, name: string) =>
  page.locator("[data-testid=checklist-board] .checklist-preview").filter({ hasText: name });

/** Line clamp, rendered line count and clipping of a preview's notes block. */
async function notesMetrics(page: Page, name: string) {
  const notes = boardCard(page, name).getByTestId("card-notes-preview");
  await expect(notes).toBeVisible();
  return notes.evaluate((el) => {
    const style = getComputedStyle(el);
    const lineHeight = parseFloat(style.lineHeight);
    const brs = Array.from(el.querySelectorAll("br"));
    return {
      clamp: style.getPropertyValue("-webkit-line-clamp"),
      visibleLines: Math.round(el.clientHeight / lineHeight),
      clipped: el.scrollHeight > el.clientHeight + 1,
      brCount: brs.length,
      brHidden: brs.filter((br) => getComputedStyle(br).display === "none").length,
    };
  });
}

test.describe("note-only cards (desktop)", () => {
  const cleanup: string[] = [];
  const anonContexts: BrowserContext[] = [];

  test.afterEach(async ({ page }) => {
    for (const ctx of anonContexts) await ctx.close().catch(() => {});
    anonContexts.length = 0;
    for (const id of cleanup) await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  async function card(page: Page, name: string, text: string, items: string[] = []) {
    const cl = await apiPost(page, "/api/checklist", { name, text });
    cleanup.push(cl.id);
    for (const t of items) await apiPost(page, `/api/checklist/${cl.id}/item`, { text: t });
    return cl.id as string;
  }

  async function openAnon(browser: Browser, token: string): Promise<Page> {
    const ctx = await browser.newContext(); // logged-out visitor
    anonContexts.push(ctx);
    const anon = await ctx.newPage();
    await anon.goto(`/p/${token}`);
    await expect(anon.getByTestId("public-card")).toBeVisible({ timeout: 5_000 });
    return anon;
  }

  async function viewLink(page: Page, id: string): Promise<string> {
    const link = await apiPost(page, `/api/checklist/${id}/public-links`, { permission: "view" });
    return link.token as string;
  }

  test("the board preview keeps line breaks and gives a note-only card 20 lines", async ({ page }) => {
    const tag = Date.now();
    const noteOnly = `NoteOnly-${tag}`;
    const mixed = `NoteMixed-${tag}`;
    const fits = `NoteFits-${tag}`;
    await card(page, noteOnly, LONG_NOTE);
    await card(page, mixed, SIX_LINES, [`item-${tag}`]);
    await card(page, fits, SIX_LINES);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");

    // Note-only: the long clamp, every line on a line of its own, cut at 20.
    const long = await notesMetrics(page, noteOnly);
    expect(long.clamp).toBe("20");
    expect(long.brCount).toBe(23);
    expect(long.brHidden).toBe(0);
    expect(long.visibleLines).toBe(20);
    expect(long.clipped).toBe(true);

    // A shorter note-only card shows all of it.
    const short = await notesMetrics(page, fits);
    expect(short.visibleLines).toBe(6);
    expect(short.clipped).toBe(false);

    // With items the short clamp stays, but the line breaks are kept as well.
    const withItems = await notesMetrics(page, mixed);
    expect(withItems.clamp).toBe("3");
    expect(withItems.brHidden).toBe(0);
    expect(withItems.visibleLines).toBe(3);
    expect(withItems.clipped).toBe(true);
  });

  test("a note-only board card has no item block and no height floor", async ({ page }) => {
    const tag = Date.now();
    const noteOnly = `SlimNote-${tag}`;
    const mixed = `SlimMixed-${tag}`;
    await card(page, noteOnly, "Just a note");
    await card(page, mixed, "A note", [`item-${tag}`]);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");

    const slim = boardCard(page, noteOnly);
    await expect(slim).toBeVisible();
    await expect(slim.locator(".checklist-items-collection")).toHaveCount(0);
    await expect(slim).not.toContainText("checked items");

    const full = boardCard(page, mixed);
    await expect(full.locator(".checklist-items-collection")).toHaveCount(1);

    // The 8rem floor is for checklist cards only; the note card is as tall as
    // its content.
    const minHeight = (loc: typeof slim) =>
      loc.locator(".checklist").evaluate((el) => getComputedStyle(el).minHeight);
    expect(await minHeight(full)).toBe("128px");
    expect(["0px", "auto"]).toContain(await minHeight(slim));
    const slimBox = (await slim.locator(".checklist").boundingBox())!;
    const fullBox = (await full.locator(".checklist").boundingBox())!;
    expect(slimBox.height).toBeLessThan(fullBox.height);
  });

  test("the open card shows no checked section until something is checked", async ({ page }) => {
    const tag = Date.now();
    const noteOnly = `EditorNote-${tag}`;
    await card(page, noteOnly, "Only notes here");

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await boardCard(page, noteOnly).getByTestId("card-title").click();
    const dialog = page.locator('[role="dialog"]:has(.checklist)');
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // No "0 checked items", but the way to add the first item is still there.
    await expect(dialog.getByTestId("editor-checked-section")).toHaveCount(0);
    await expect(dialog).not.toContainText("checked items");
    await expect(dialog.getByTestId("add-item")).toBeVisible();
    // Desktop keeps the full reminders section; the bell is for phones.
    await expect(dialog.getByTestId("card-reminders")).toBeVisible();
    await expect(dialog.getByTestId("card-reminders-bell")).toHaveCount(0);

    // Add an item and check it: the section appears with its count.
    await dialog.getByTestId("add-item").click();
    await dialog.getByTestId("item-text-editor").fill(`first-${tag}`);
    await expect(dialog.getByTestId("item-row")).toHaveCount(1);
    await expect(dialog.getByTestId("editor-checked-section")).toHaveCount(0);
    await dialog.getByTestId("item-row").getByRole("checkbox").click();
    await expect(dialog.getByTestId("editor-checked-section")).toContainText(/1\s+checked items/, {
      timeout: 5_000,
    });
  });

  test("a view link to a note-only card shows the notes and no item area", async ({ page, browser }) => {
    const tag = Date.now();
    const noteId = await card(page, `PublicNote-${tag}`, "First line\nSecond line");
    const blankId = await card(page, `PublicBlank-${tag}`, "");

    const anon = await openAnon(browser, await viewLink(page, noteId));
    const notes = anon.getByTestId("card-notes-rendered");
    await expect(notes).toContainText("First line");
    await expect(notes.locator("br")).toHaveCount(1);
    await expect(anon.getByTestId("public-items")).toHaveCount(0);
    await expect(anon.getByTestId("public-card")).not.toContainText("no items yet");
    await expect(anon.getByTestId("public-card")).not.toContainText("checked items");

    // Neither notes nor items: the card still says something.
    const blank = await openAnon(browser, await viewLink(page, blankId));
    await expect(blank.getByTestId("public-items")).toContainText("This list has no items yet.");
  });
});
