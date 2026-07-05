/**
 * Sidebar count badges (Chunk 5).
 *
 * Every sidebar entry (Home / Shared / Archive / each label) carries a
 * right-aligned count of its non-archived cards (Archive counts archived ones).
 * The badges come from GET /api/checklist/counts, fetched on mount and
 * refreshed — debounced — on the board-mutating SSE events. A zero count hides
 * the badge entirely.
 *
 * The admin board accumulates cards across the suite, so these assert *relative*
 * movement (home drops by one, archive rises by one) rather than absolute totals.
 *
 * Runs in the "chromium" project with the pre-loaded admin session. See memory
 * `flaky-e2e-dnd-sharing`: re-run before blaming flakiness.
 */
import { test, expect, type Page } from "@playwright/test";

test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

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

const editorDialog = (page: Page) => page.locator('[role="dialog"]:has(.checklist)');

async function openCard(page: Page, name: string) {
  await page.locator("[data-testid=card-title]", { hasText: name }).first().click();
  await expect(page).toHaveURL(/\/card\//, { timeout: 5_000 });
  const dialog = editorDialog(page);
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  return dialog;
}

// Read a badge's numeric value; a hidden badge (count 0) reads as 0.
async function badge(page: Page, testid: string): Promise<number> {
  const el = page.locator(`[data-testid=${testid}]`);
  if ((await el.count()) === 0 || !(await el.isVisible())) return 0;
  const text = (await el.textContent())?.trim() ?? "0";
  return text.endsWith("+") ? Number.parseInt(text) : Number(text);
}

// Poll until a badge reaches the expected value (badges update via a debounced
// SSE-driven refetch, so give it a beat).
async function expectBadge(page: Page, testid: string, value: number) {
  await expect
    .poll(() => badge(page, testid), { timeout: 5_000 })
    .toBe(value);
}

test.describe("sidebar count badges", () => {
  test.setTimeout(30_000);

  const cleanupChecklists: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanupChecklists) await apiDelete(page, `/api/checklist/${id}`);
    cleanupChecklists.length = 0;
  });

  test("archiving a card moves the count from Home to Archive", async ({ page }) => {
    const name = `Counts-${Date.now()}`;
    const cl = await apiPost(page, "/api/checklist", { name });
    cleanupChecklists.push(cl.id);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(page.getByText(name, { exact: true })).toBeVisible();
    // Home badge must exist (the board is non-empty for the admin session).
    await expect(page.locator("[data-testid=sidebar-count-home]")).toBeVisible();

    const homeBefore = await badge(page, "sidebar-count-home");
    const archiveBefore = await badge(page, "sidebar-count-archive");

    // Archive via the editor toolbar.
    const dialog = await openCard(page, name);
    await dialog.locator("[data-testid=card-archive]").click();
    await expect(page.getByText(name, { exact: true })).not.toBeVisible({ timeout: 5_000 });

    // The count moves: Home -1, Archive +1.
    await expectBadge(page, "sidebar-count-home", homeBefore - 1);
    await expectBadge(page, "sidebar-count-archive", archiveBefore + 1);

    // Undo (restore) moves it back.
    await page.locator("[data-testid=undo-archive]").click();
    await expectBadge(page, "sidebar-count-home", homeBefore);
    await expectBadge(page, "sidebar-count-archive", archiveBefore);
  });
});
