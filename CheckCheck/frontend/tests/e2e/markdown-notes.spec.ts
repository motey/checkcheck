/**
 * Markdown rendering for the card description (`text`) field.
 *
 * Covers the four surfaces from docs/plans/MARKDOWN_CARD_DESCRIPTION.md:
 *  - board preview renders formatting (not raw markers),
 *  - the open card's focus-swap edit surface (rendered ⇄ raw textarea),
 *  - the "Formatting help" hint popup,
 *  - the public share page renders Markdown for an anonymous visitor.
 *
 * Runs in the "chromium" project with the pre-loaded admin session. Cards are
 * minted through the API and cleaned up afterwards. If a DnD/sharing spec looks
 * flaky, re-run before blaming a Markdown change (known non-determinism).
 */
import { test, expect, type Browser, type Page } from "@playwright/test";

test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

test.describe("Markdown card notes", () => {
  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanup) await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanup.length = 0;
  });

  // Create a card carrying Markdown notes, return its id + title.
  async function makeCard(page: Page, text: string): Promise<{ id: string; title: string }> {
    const tag = Date.now() + Math.floor(Math.random() * 1000);
    const title = `MD-${tag}`;
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

  test("board preview renders formatting, not raw markers", async ({ page }) => {
    const { title } = await makeCard(page, "**bold** and _italic_");
    await page.goto("/");

    const card = page.locator(".checklist", { hasText: title }).first();
    await expect(card.locator(".md-notes strong")).toHaveText("bold");
    await expect(card.locator(".md-notes em")).toHaveText("italic");
    // Raw asterisks/underscores are gone.
    await expect(card.locator(".md-notes")).not.toContainText("**");
  });

  test("open card swaps between rendered Markdown and the raw source", async ({ page }) => {
    const { title } = await makeCard(page, "**bold**");
    await page.goto("/");
    await page.locator("[data-testid=card-title]", { hasText: title }).first().click();

    const dialog = editorDialog(page);
    await expect(dialog).toBeVisible();

    // Not editing → rendered Markdown, no textarea.
    const rendered = dialog.locator("[data-testid=card-notes-rendered]");
    await expect(rendered.locator("strong")).toHaveText("bold");
    await expect(dialog.locator("[data-testid=card-notes-textarea]")).toHaveCount(0);

    // Click swaps to the raw textarea, focused, showing the source.
    await rendered.click();
    const textarea = dialog.locator("[data-testid=card-notes-textarea]");
    await expect(textarea).toBeVisible();
    await expect(textarea).toHaveValue("**bold**");
    await expect(textarea).toBeFocused();

    // Type more, blur → swaps back to rendered with the new formatting. In the
    // open editor the title is a textarea (not the preview's card-title div), so
    // focusing it is what pulls focus off the notes and fires the blur swap.
    await textarea.fill("**bold** and _italic_");
    await dialog.locator('textarea[placeholder="Enter a checklist title..."]').click();
    await expect(dialog.locator("[data-testid=card-notes-rendered] em")).toHaveText("italic");
  });

  test("the Formatting help hint opens the cheat-sheet popup", async ({ page }) => {
    const { title } = await makeCard(page, "notes");
    await page.goto("/");
    await page.locator("[data-testid=card-title]", { hasText: title }).first().click();

    const dialog = editorDialog(page);
    await dialog.locator("[data-testid=markdown-help-trigger]").click();
    await expect(page.locator("[data-testid=markdown-help]")).toBeVisible();
    await expect(page.locator("[data-testid=markdown-help]")).toContainText("Markdown");
  });

  test("public share page renders Markdown for an anonymous visitor", async ({ page, browser }) => {
    const { id } = await makeCard(page, "**public bold**");
    const linkRes = await page.request.post(`/api/checklist/${id}/public-links`, {
      data: { permission: "view" },
      headers: { "Content-Type": "application/json" },
    });
    expect(linkRes.ok()).toBeTruthy();
    const { token } = await linkRes.json();

    const ctx = await browser.newContext(); // logged-out visitor
    const anon = await ctx.newPage();
    await anon.goto(`/p/${token}`);

    await expect(anon.locator(".md-notes strong")).toHaveText("public bold");
    await expect(anon.getByText(/Error 4\d\d/)).toHaveCount(0);

    await anon.goto("about:blank").catch(() => {});
    await ctx.close();
  });
});
