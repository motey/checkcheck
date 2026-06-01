/**
 * Two-client (two browser contexts) sync tests.
 *
 * Verifies that SSE-driven state updates propagate from one open tab to
 * another without a manual refresh.
 *
 * Covered scenarios:
 *   1. A checklist created in tab-1 appears in tab-2 via SSE
 *      (checklist_created → store.refresh).
 *   2. Item state changed server-side appears in tab-2 via SSE
 *      (item_state → itemStore.refreshState → reactive board re-render).
 *
 * ── Known UI quirk ────────────────────────────────────────────────────────
 * CheckListItem.vue uses both v-model and @click.stop on the same UCheckbox:
 *
 *   <UCheckbox v-model="item.state.checked" @click.stop="toggleCheck()" />
 *
 * UCheckbox fires its update:modelValue emit synchronously on click, so
 * state.checked is already flipped BEFORE toggleCheck() runs.  toggleCheck()
 * reads !state.checked which is the *original* value — it sends that back to
 * the backend, which sees no change and emits no SSE notification.  The
 * checkbox flickers (v-model flip) and reverts.
 *
 * Therefore the sync test drives state changes via page.request.patch
 * (direct API) instead of clicking the checkbox UI.  This sends the correct
 * {checked: true} payload, produces a real server-side state change, and
 * causes the backend to emit an item_state SSE notification.
 *
 * ── Runner hang prevention ────────────────────────────────────────────────
 * The frontend keeps /api/sync as a persistent EventSource.  If a browser
 * context is closed while the SSE stream is open, Playwright's browser
 * teardown waits for the HTTP connection to settle — the backend never closes
 * it proactively, so the runner hangs forever.  The afterEach hook navigates
 * all pages to about:blank first, which triggers a full navigation that
 * causes the browser to abort the SSE connection immediately, then closes
 * the context (now safe because there are no open streams).
 */
import { test, expect, type Page, type BrowserContext } from "@playwright/test";
import { resolve } from "path";

const AUTH_STATE_FILE = resolve(__dirname, ".auth/state.json");

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

/** Open a second authenticated context, navigate to "/", and wait for board. */
async function openSecondTab(
  ctx1: BrowserContext
): Promise<{ context: BrowserContext; page: Page }> {
  const browser = ctx1.browser()!;
  const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
  const page = await context.newPage();
  await page.goto("/");
  await page.waitForSelector("[data-testid=checklist-board]");
  return { context, page };
}

// ── tests ─────────────────────────────────────────────────────────────────────

test.describe("cross-tab sync", () => {
  test.setTimeout(25_000);

  const cleanupChecklists: string[] = [];
  let secondCtx: BrowserContext | null = null;

  test.afterEach(async ({ page }) => {
    // API cleanup first, while the pages are still live.
    for (const id of cleanupChecklists) await apiDelete(page, `/api/checklist/${id}`);
    cleanupChecklists.length = 0;

    // Close the second context safely:
    // 1. Navigate its pages to about:blank — the full navigation aborts the
    //    SSE stream at the browser level without waiting for server close.
    // 2. Then await context.close() — fast now that no streams are open.
    if (secondCtx) {
      await Promise.all(
        secondCtx.pages().map((p) => p.goto("about:blank").catch(() => {}))
      );
      await secondCtx.close().catch(() => {});
      secondCtx = null;
    }

    // Navigate the main page away too so its SSE closes before Playwright
    // tears down the browser at the end of the suite.
    await page.goto("about:blank").catch(() => {});
  });

  // ── test 1 ──────────────────────────────────────────────────────────────

  test("a checklist created in one tab appears in a second tab via SSE", async ({
    page,
    context,
  }) => {
    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");

    const { context: ctx2, page: page2 } = await openSecondTab(context);
    secondCtx = ctx2;

    const tag = Date.now();
    const newName = `SyncCreate-${tag}`;

    // Create via the UI in tab-1 to trigger a checklist_created SSE event.
    await page.getByRole("button", { name: "New Check List" }).click();
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    await dialog.locator("textarea").first().fill(newName);

    // Wait for the debounced PATCH (500 ms debounce, 3 s maxWait).
    await page.waitForResponse(
      (r) => r.url().includes("/api/checklist/") && r.request().method() === "PATCH",
      { timeout: 6_000 }
    );

    // Grab the ID for cleanup.
    const listRes = await page.request.get("/api/checklist", {
      params: { search: newName, limit: 5 },
    });
    const { items } = await listRes.json();
    if (items?.length) cleanupChecklists.push(items[0].id);

    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible({ timeout: 3_000 });

    // Tab-2: checklist_created SSE → store.refresh → card appears.
    await expect(page2.getByText(newName, { exact: true })).toBeVisible({ timeout: 8_000 });
  });

  // ── test 2 ──────────────────────────────────────────────────────────────

  test("an item state change in one tab propagates to a second tab via SSE", async ({
    page,
    context,
  }) => {
    const tag = Date.now();
    const clName = `SyncItem-${tag}`;
    const itemText = `SyncItemText-${tag}`;

    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanupChecklists.push(cl.id);
    const item = await apiPost(page, `/api/checklist/${cl.id}/item`, { text: itemText });

    // Wait for the backend to commit the item's position record; without this,
    // the concurrent GET /api/item calls from two tabs can race the write and
    // one returns 403 (access denied because the inner-join on CheckListPosition
    // doesn't find the row yet), leaving that tab with no item preview.
    await expect.poll(
      async () => (await page.request.get(`/api/checklist/${cl.id}/item`)).ok(),
      { timeout: 4_000, intervals: [100, 200, 400] }
    ).toBeTruthy();

    // Tab-1: navigate and wait for the item preview checkbox to appear.
    // Only open tab-2 after tab-1 has confirmed the item loaded — this makes
    // the two GET /api/item calls sequential rather than concurrent.
    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");

    const card = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: clName });
    await expect(card).toBeVisible({ timeout: 5_000 });

    // getByRole('checkbox') targets the accessible wrapper (Nuxt UI 4 hides
    // the native <input> — [type="checkbox"] finds the invisible element).
    const previewCheckbox1 = card.getByRole("checkbox").first();
    await expect(previewCheckbox1).toBeVisible({ timeout: 5_000 });

    // Tab-2: open now that tab-1's GET /api/item has settled.
    const { context: ctx2, page: page2 } = await openSecondTab(context);
    secondCtx = ctx2;

    const card2 = page2
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: clName });
    await expect(card2).toBeVisible({ timeout: 5_000 });
    const previewCheckbox2 = card2.getByRole("checkbox").first();
    await expect(previewCheckbox2).toBeVisible({ timeout: 5_000 });
    await expect(previewCheckbox2).not.toBeChecked();

    // Drive the state change via direct API call on tab-1's request context.
    // We intentionally avoid clicking the checkbox in the UI because of a
    // known quirk: UCheckbox's v-model fires synchronously BEFORE @click.stop
    // calls toggleCheck(), so toggleCheck() reads the already-flipped value
    // and sends the original (unchecked) state back — a no-op that produces
    // no SSE notification.  The direct PATCH sends {checked: true} correctly.
    const patchRes = await page.request.patch(
      `/api/checklist/${cl.id}/item/${item.id}/state`,
      { data: { checked: true }, headers: { "Content-Type": "application/json" } }
    );
    expect(patchRes.ok()).toBeTruthy();
    expect((await patchRes.json()).checked).toBe(true);

    // Tab-2 SSE verification:
    //   item_state SSE event → refreshState → state.checked becomes true
    //   → watchEffect in CheckListItemCollectionPreview re-runs
    //   → getCheckListItems(clId, false) filters out the now-checked item
    //   → the checkbox's <li> is removed from the DOM
    //
    // "checkbox disappears from unchecked list" is the correct observable
    // outcome — toBeChecked() would fail because the element is removed, not
    // because it stays and shows as unchecked.
    await expect(previewCheckbox2).not.toBeVisible({ timeout: 8_000 });
  });
});
