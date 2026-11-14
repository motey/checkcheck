/**
 * Copy card notes to the clipboard (issue #31).
 *
 * The copy buttons sit in the notes hint row (shared CardParts/NotesField.vue):
 *  - "Copy notes" writes readable plain text plus the rendered HTML,
 *  - "Copy as Markdown" writes the stored source and only shows when the notes
 *    use Markdown,
 *  - a view-only public link gets the row with just the copy buttons.
 *
 * Runs in the "chromium" project with the pre-loaded admin session. Cards are
 * minted through the API and cleaned up afterwards.
 */
import { test, expect, type Page } from "@playwright/test";

test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

test.describe("Copy card notes", () => {
  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanup) await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanup.length = 0;
  });

  async function makeCard(page: Page, text: string): Promise<{ id: string; title: string }> {
    const tag = Date.now() + Math.floor(Math.random() * 1000);
    const title = `CopyNotes-${tag}`;
    const res = await page.request.post("/api/checklist", {
      data: { name: title, text },
      headers: { "Content-Type": "application/json" },
    });
    expect(res.ok(), "card create should succeed").toBeTruthy();
    const cl = await res.json();
    cleanup.push(cl.id);
    return { id: cl.id, title };
  }

  const editorDialog = (page: Page) => page.locator('[role="dialog"]:has(.checklist)');

  async function openCard(page: Page, title: string) {
    await page.goto("/");
    await page.locator("[data-testid=card-title]", { hasText: title }).first().click();
    const dialog = editorDialog(page);
    await expect(dialog).toBeVisible();
    return dialog;
  }

  // The text/html flavor of the clipboard, or null when there is none.
  const readClipboardHtml = (page: Page) =>
    page.evaluate(async () => {
      for (const item of await navigator.clipboard.read()) {
        if (item.types.includes("text/html")) return await (await item.getType("text/html")).text();
      }
      return null;
    });

  test("copies Markdown notes as plain text with HTML, and as Markdown source", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const source = "# Trip\n\n**Bring** the [map](https://example.com/map)\n\n- tent\n- stove";
    const { title } = await makeCard(page, source);
    const dialog = await openCard(page, title);

    await dialog.locator("[data-testid=notes-copy-text]").click();
    await expect(page.getByText("Notes copied", { exact: true })).toBeVisible({ timeout: 5_000 });
    const plain = await page.evaluate(() => navigator.clipboard.readText());
    expect(plain).toBe("Trip\n\nBring the map (https://example.com/map)\n\n- tent\n- stove");
    const html = await readClipboardHtml(page);
    expect(html).toContain("<strong>Bring</strong>");
    expect(html).toContain("<li>tent</li>");

    await dialog.locator("[data-testid=notes-copy-markdown]").click();
    await expect(page.getByText("Notes copied as Markdown", { exact: true })).toBeVisible({ timeout: 5_000 });
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(source);
    // The Markdown copy is plain text only, so a rich editor pastes the source.
    expect(await readClipboardHtml(page)).toBeNull();
  });

  test("plain notes offer no Markdown copy; typed Markdown does, without leaving the editor", async ({
    page,
    context,
  }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const { title } = await makeCard(page, "just plain words");
    const dialog = await openCard(page, title);

    await expect(dialog.locator("[data-testid=notes-copy-text]")).toBeVisible();
    await expect(dialog.locator("[data-testid=notes-copy-markdown]")).toHaveCount(0);

    // Start editing and type Markdown: the Markdown button shows up.
    await dialog.locator("[data-testid=card-notes-rendered]").click();
    const textarea = dialog.locator("[data-testid=card-notes-textarea]");
    await textarea.fill("just *plain* words");
    await expect(dialog.locator("[data-testid=notes-copy-markdown]")).toBeVisible();

    // Copying while editing keeps the textarea focused and copies what was typed.
    await dialog.locator("[data-testid=notes-copy-markdown]").click();
    await expect(page.getByText("Notes copied as Markdown", { exact: true })).toBeVisible({ timeout: 5_000 });
    await expect(textarea).toBeFocused();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("just *plain* words");
  });

  test("a card without notes shows no copy buttons", async ({ page }) => {
    const { title } = await makeCard(page, "");
    const dialog = await openCard(page, title);
    await expect(dialog.locator("[data-testid=markdown-help-trigger]")).toBeVisible();
    await expect(dialog.locator("[data-testid=notes-copy-actions]")).toHaveCount(0);
  });

  test("a view-only public link gets the copy buttons without the Markdown hint", async ({ page, browser }) => {
    const { id } = await makeCard(page, "**public** notes");
    const linkRes = await page.request.post(`/api/checklist/${id}/public-links`, {
      data: { permission: "view" },
      headers: { "Content-Type": "application/json" },
    });
    expect(linkRes.ok()).toBeTruthy();
    const { token } = await linkRes.json();

    const ctx = await browser.newContext({ permissions: ["clipboard-read", "clipboard-write"] });
    const anon = await ctx.newPage();
    await anon.goto(`/p/${token}`);

    await expect(anon.locator("[data-testid=notes-copy-text]")).toBeVisible();
    await expect(anon.locator("[data-testid=notes-copy-markdown]")).toBeVisible();
    await expect(anon.locator("[data-testid=markdown-help-trigger]")).toHaveCount(0);

    await anon.locator("[data-testid=notes-copy-text]").click();
    await expect(anon.getByText("Notes copied", { exact: true })).toBeVisible({ timeout: 5_000 });
    expect(await anon.evaluate(() => navigator.clipboard.readText())).toBe("public notes");

    await anon.goto("about:blank").catch(() => {});
    await ctx.close();
  });
});
