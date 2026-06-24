/**
 * Filter and search tests.
 *
 * Covers three scenarios:
 *   1. Clicking a label in the sidebar filters the board to only show
 *      checklists that carry that label.
 *   2. Typing in the search box shows only matching checklists.
 *   3. Label filter and search text can be combined (only items matching
 *      BOTH label AND search are shown).
 *
 * Label filtering is client-side (store.getCheckLists({ label_id })), so
 * the label must be present on the checklist returned from the initial
 * fetchNextPage() call — new checklists always have the highest position.index
 * and therefore land in the first batch.
 *
 * Combined label+search triggers a server-side search via
 * GET /api/checklist?search=...&label_id=... because the label filter is
 * preserved in the URL and picked up by the debounced search watcher.
 */
import { test, expect, type Page } from "@playwright/test";

// Close the board's SSE (/api/sync) before Playwright tears down each page.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

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

async function apiPut(page: Page, path: string) {
  const res = await page.request.put(path);
  expect(res.ok(), `PUT ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

// ── tests ─────────────────────────────────────────────────────────────────────

test.describe("label filter", () => {
  test.setTimeout(15_000);

  const cleanupChecklists: string[] = [];
  const cleanupLabels: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanupChecklists) await apiDelete(page, `/api/checklist/${id}`);
    for (const id of cleanupLabels) await apiDelete(page, `/api/label/${id}`);
    cleanupChecklists.length = 0;
    cleanupLabels.length = 0;
  });

  test("clicking a label in the sidebar shows only labeled checklists", async ({ page }) => {
    const tag = Date.now();
    const labelName = `Label-${tag}`;
    const withLabelName = `WithLabel-${tag}`;
    const noLabelName = `NoLabel-${tag}`;

    // Create a label and two checklists (one with, one without the label).
    const label = await apiPost(page, "/api/label", { display_name: labelName });
    cleanupLabels.push(label.id);

    const clWith = await apiPost(page, "/api/checklist", { name: withLabelName });
    cleanupChecklists.push(clWith.id);
    await apiPut(page, `/api/checklist/${clWith.id}/label/${label.id}`);

    const clWithout = await apiPost(page, "/api/checklist", { name: noLabelName });
    cleanupChecklists.push(clWithout.id);

    // Load the board — both checklists visible, label present in the sidebar.
    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(page.getByText(withLabelName, { exact: true })).toBeVisible();
    await expect(page.getByText(noLabelName, { exact: true })).toBeVisible();
    // exact:true is required — without it, "Label-…" would substring-match
    // "WithLabel-…" and "NoLabel-…" card titles → strict-mode violation.
    // Scope to the sidebar <aside> — the label name also appears as a badge
    // on the checklist card, which would cause a strict-mode violation.
    const sidebarLabel = page.locator("aside").getByText(labelName, { exact: true });
    await expect(sidebarLabel).toBeVisible();

    // Click the label in the sidebar to activate the filter.
    await sidebarLabel.click();

    // URL should contain ?label=<id>
    await expect(page).toHaveURL(new RegExp(`label=${label.id}`), { timeout: 3_000 });

    // Only the labeled checklist should be shown.
    await expect(page.getByText(withLabelName)).toBeVisible({ timeout: 3_000 });
    await expect(page.getByText(noLabelName)).not.toBeVisible();
  });
});

// ─────────────────────────────────────────────────────────────────────────────

test.describe("search", () => {
  test.setTimeout(15_000);

  const cleanupChecklists: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanupChecklists) await apiDelete(page, `/api/checklist/${id}`);
    cleanupChecklists.length = 0;
  });

  test("typing in the search box shows only matching checklists", async ({ page }) => {
    const tag = Date.now();
    const matchName = `SearchMatch-${tag}`;
    const noMatchName = `ShouldBeHidden-${tag}`;

    const clMatch = await apiPost(page, "/api/checklist", { name: matchName });
    cleanupChecklists.push(clMatch.id);
    const clNoMatch = await apiPost(page, "/api/checklist", { name: noMatchName });
    cleanupChecklists.push(clNoMatch.id);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(page.getByText(matchName)).toBeVisible();
    await expect(page.getByText(noMatchName)).toBeVisible();

    // Type the unique part of matchName — only that checklist should survive.
    const searchInput = page.locator("[data-testid=search-input]");
    await searchInput.fill(`SearchMatch-${tag}`);

    // Wait for the debounced search (300 ms) to fire and results to render.
    await expect(page).toHaveURL(/search=/, { timeout: 2_000 });
    await expect(page.getByText(matchName)).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(noMatchName)).not.toBeVisible({ timeout: 5_000 });
  });

  test("clearing the search restores all checklists", async ({ page }) => {
    const tag = Date.now();
    const name = `ClearSearch-${tag}`;

    const cl = await apiPost(page, "/api/checklist", { name });
    cleanupChecklists.push(cl.id);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");

    const searchInput = page.locator("[data-testid=search-input]");
    await searchInput.fill(name);
    await expect(page).toHaveURL(/search=/, { timeout: 2_000 });

    // Clear by emptying the input — the watcher removes ?search from the URL.
    await searchInput.fill("");
    await searchInput.press("Enter");

    await expect(page).not.toHaveURL(/search=/, { timeout: 2_000 });
    await expect(page.getByText(name)).toBeVisible({ timeout: 5_000 });
  });
});

// ─────────────────────────────────────────────────────────────────────────────

test.describe("combined label and search filter", () => {
  test.setTimeout(15_000);

  const cleanupChecklists: string[] = [];
  const cleanupLabels: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanupChecklists) await apiDelete(page, `/api/checklist/${id}`);
    for (const id of cleanupLabels) await apiDelete(page, `/api/label/${id}`);
    cleanupChecklists.length = 0;
    cleanupLabels.length = 0;
  });

  test("label filter and search text narrow results to their intersection", async ({ page }) => {
    const tag = Date.now();
    const labelName = `CombinedLabel-${tag}`;
    // A: has label, name matches search → should be VISIBLE
    const nameA = `Combined-Match-${tag}`;
    // B: has label, name does NOT match search → should be hidden
    const nameB = `Combined-Other-${tag}`;
    // C: no label, name matches search → should be hidden
    const nameC = `Combined-Match-NoLabel-${tag}`;

    const label = await apiPost(page, "/api/label", { display_name: labelName });
    cleanupLabels.push(label.id);

    const clA = await apiPost(page, "/api/checklist", { name: nameA });
    cleanupChecklists.push(clA.id);
    await apiPut(page, `/api/checklist/${clA.id}/label/${label.id}`);

    const clB = await apiPost(page, "/api/checklist", { name: nameB });
    cleanupChecklists.push(clB.id);
    await apiPut(page, `/api/checklist/${clB.id}/label/${label.id}`);

    const clC = await apiPost(page, "/api/checklist", { name: nameC });
    cleanupChecklists.push(clC.id);

    // Start with the label filter active via URL.
    await page.goto(`/?label=${label.id}`);
    await page.waitForSelector("[data-testid=checklist-board]");

    // Both labeled checklists visible (label filter active, no text search yet).
    await expect(page.getByText(nameA)).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(nameB)).toBeVisible({ timeout: 5_000 });
    // Unlabeled checklist should already be filtered out by label.
    await expect(page.getByText(nameC)).not.toBeVisible();

    // Now add the search text — the watcher will call searchChecklists(query, labelId).
    const searchInput = page.locator("[data-testid=search-input]");
    // Search for the unique token that only nameA (and nameC) contain.
    await searchInput.fill(`Combined-Match-${tag}`);

    // URL should contain both filters.
    await expect(page).toHaveURL(/search=.*label=|label=.*search=/, { timeout: 3_000 });

    // Only A (has label + matches search) should remain.
    await expect(page.getByText(nameA)).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(nameB)).not.toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(nameC)).not.toBeVisible({ timeout: 5_000 });
  });
});
