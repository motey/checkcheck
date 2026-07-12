/**
 * Delta application replaces the legacy refetch (WI-10).
 *
 * With `localFirst` on, the board reconciles with server truth through ONE read
 * path: pull `GET /api/changes` and fold the delta into the stores. This spec
 * proves the two "Done when" scenarios:
 *
 *   1. Live convergence — two flag-on tabs. A change made in context A emits an
 *      SSE `changes_available` poke; context B pulls the delta and converges,
 *      using `/api/changes` and NOT a full board refetch.
 *   2. Offline-gap convergence — a flag-on tab goes offline (context.setOffline,
 *      which drops the open SSE stream), a change happens elsewhere, and on
 *      reconnect the tab pulls one delta and converges — again no full refetch.
 *
 * The flag is set via `?localFirst=1` (persists to localStorage, survives
 * reloads — see utils/localFirst.ts). SSE teardown mirrors sync.spec.ts:
 * navigate pages to about:blank so the EventSource closes before Playwright
 * tears the browser down (otherwise the runner hangs on the open stream).
 */
import { test, expect, type Page, type BrowserContext, type APIRequestContext } from "@playwright/test";
import { resolve } from "path";

const AUTH_STATE_FILE = resolve(__dirname, ".auth/state.json");

async function apiPost(req: APIRequestContext, path: string, body: object) {
  const res = await req.post(path, { data: body, headers: { "Content-Type": "application/json" } });
  expect(res.ok(), `POST ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

async function apiPut(req: APIRequestContext, path: string) {
  const res = await req.put(path);
  expect(res.ok(), `PUT ${path} failed: ${res.status()}`).toBeTruthy();
}

async function apiDelete(req: APIRequestContext, path: string) {
  await req.delete(path).catch(() => {});
}

/**
 * Resolve once this page's SSE stream (`GET /api/sync`) is established, so a
 * subsequent "elsewhere" mutation's `changes_available` poke is guaranteed to be
 * delivered to this client.
 *
 * The board renders from the IndexedDB snapshot *before* the EventSource
 * connects, so waiting on the card alone races the poke: the server fans a poke
 * out only to clients already registered in its subscriber set (the SQLite drain
 * marks each notification row consumed as it sends it), and a poke fired before
 * this client registers is lost forever — there is no re-delivery without an SSE
 * reconnect. Arm this *before* `page.goto` (the response listener must be
 * attached before `connect()` issues the request) and await it before the
 * mutation / going offline.
 */
function syncConnected(page: Page): Promise<unknown> {
  return page.waitForResponse((r) => r.url().includes("/api/sync"), { timeout: 20_000 });
}

/** Open a second flag-on authenticated tab and wait for the board + live SSE. */
async function openSecondTab(ctx1: BrowserContext): Promise<{ context: BrowserContext; page: Page }> {
  const browser = ctx1.browser()!;
  const context = await browser.newContext({ storageState: AUTH_STATE_FILE });
  const page = await context.newPage();
  const ready = syncConnected(page);
  await page.goto("/?localFirst=1");
  await page.waitForSelector("[data-testid=checklist-board]");
  await ready;
  return { context, page };
}

const cardPreview = (page: Page, name: string) =>
  page.locator("[data-testid=checklist-board] .checklist-preview").filter({ hasText: name });

test.describe("local-first delta application", () => {
  test.setTimeout(40_000);

  const cleanup: string[] = [];
  const labelCleanup: string[] = [];
  let secondCtx: BrowserContext | null = null;
  // A fresh online request context for making "elsewhere" edits that are NOT
  // subject to a page's offline emulation.
  let sideReq: APIRequestContext | null = null;

  test.afterEach(async ({ page, browser }) => {
    if (!sideReq) sideReq = await browser.newContext({ storageState: AUTH_STATE_FILE }).then((c) => c.request);
    for (const id of cleanup) await apiDelete(sideReq, `/api/checklist/${id}`);
    for (const id of labelCleanup) await apiDelete(sideReq, `/api/label/${id}`);
    cleanup.length = 0;
    labelCleanup.length = 0;

    if (secondCtx) {
      await Promise.all(secondCtx.pages().map((p) => p.goto("about:blank").catch(() => {})));
      await secondCtx.close().catch(() => {});
      secondCtx = null;
    }
    await page.goto("about:blank").catch(() => {});
  });

  // ── 1. Live convergence via poke → delta ──────────────────────────────────

  test("a change in one tab converges into a second tab via poke → delta (no full refetch)", async ({
    page,
    context,
  }) => {
    const tag = Date.now();
    const clName = `Delta-${tag}`;
    const itemText = `delta-item-${tag}`;

    const cl = await apiPost(page.request, "/api/checklist", { name: clName });
    cleanup.push(cl.id);

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(cardPreview(page, clName)).toBeVisible({ timeout: 8_000 });

    const { context: ctx2, page: page2 } = await openSecondTab(context);
    secondCtx = ctx2;
    await expect(cardPreview(page2, clName)).toBeVisible({ timeout: 8_000 });

    // Record page2's requests so we can prove it converged via /api/changes and
    // NOT via a legacy board list refetch (GET /api/checklist?...).
    const seen: string[] = [];
    page2.on("request", (r) => {
      if (r.method() === "GET") seen.push(r.url());
    });

    // Change made from tab A's context (direct API — representative, avoids the
    // checkbox v-model quirk). Emits `changes_available` to tab B.
    await apiPost(page.request, `/api/checklist/${cl.id}/item`, { text: itemText });

    // Tab B: poke → applyDelta → the new item shows in the card preview.
    await expect(page2.getByText(itemText, { exact: true })).toBeVisible({ timeout: 10_000 });

    // It reconciled through the delta feed …
    expect(seen.some((u) => u.includes("/api/changes"))).toBe(true);
    // … and did NOT refetch the whole board (the legacy resync path).
    expect(seen.some((u) => /\/api\/checklist(\?|$)/.test(u))).toBe(false);
  });

  // ── 2. Offline-gap convergence on reconnect ───────────────────────────────

  test("a tab offline during a change converges on reconnect via one delta pull", async ({
    page,
    context,
    browser,
  }) => {
    const tag = Date.now();
    const clName = `Gap-${tag}`;
    const newName = `Gap-renamed-${tag}`;

    const cl = await apiPost(page.request, "/api/checklist", { name: clName });
    cleanup.push(cl.id);

    const ready = syncConnected(page);
    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(cardPreview(page, clName)).toBeVisible({ timeout: 8_000 });
    // The reconnect delta pull only fires on a *re-open* (hasOpened=true), so the
    // stream must be established before we drop it — otherwise setOffline→online
    // is a first connect that pulls nothing and the rename is never seen.
    await ready;

    // Go offline — this drops the open SSE stream, so the poke for the coming
    // change is missed (the real offline-gap condition).
    await context.setOffline(true);

    // Record requests from now on to prove the reconnect used the delta feed.
    const seen: string[] = [];
    page.on("request", (r) => {
      if (r.method() === "GET") seen.push(r.url());
    });

    // Rename the card from an independent ONLINE context while the tab is dark.
    const sideCtx = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const patch = await sideCtx.request.patch(`/api/checklist/${cl.id}`, {
      data: { name: newName },
      headers: { "Content-Type": "application/json" },
    });
    expect(patch.ok()).toBeTruthy();
    await sideCtx.close();

    // Back online → EventSource reconnects → onopen(reconnect) → applyDelta.
    await context.setOffline(false);

    // The tab converges to the new name without a full board refetch.
    await expect(page.getByText(newName, { exact: true })).toBeVisible({ timeout: 20_000 });
    expect(seen.some((u) => u.includes("/api/changes"))).toBe(true);
    expect(seen.some((u) => /\/api\/checklist(\?|$)/.test(u))).toBe(false);
  });

  // ── 3. Label tombstone strips the chip off a live client's card ────────────
  //
  // Chunk D finding #6: deleting a label elsewhere must, via the delta feed,
  // remove its chip from every card that carried it in an already-loaded client
  // (unit-covered in deltaApply.spec.ts; this drives the full poke→delta→store
  // path). The chip is a <button> rendering the label's `display_name`, driven by
  // the card's `labels` array + the label store — a `label_tombstone` clears both.

  test("deleting a label elsewhere removes its chip from a live client's card via delta", async ({
    page,
    browser,
  }) => {
    const tag = Date.now();
    const clName = `Labeled-${tag}`;
    const labelName = `chip-${tag}`;

    if (!sideReq) sideReq = await browser.newContext({ storageState: AUTH_STATE_FILE }).then((c) => c.request);

    const cl = await apiPost(page.request, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    const label = await apiPost(page.request, "/api/label", { display_name: labelName });
    labelCleanup.push(label.id);
    await apiPut(page.request, `/api/checklist/${cl.id}/label/${label.id}`);

    const ready = syncConnected(page);
    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const card = cardPreview(page, clName);
    await expect(card).toBeVisible({ timeout: 8_000 });
    // The chip is present on the card.
    await expect(card.getByText(labelName, { exact: true })).toBeVisible({ timeout: 8_000 });
    // The chip renders from the local snapshot before the SSE connects; wait for
    // the stream so the delete's poke is delivered rather than fanned out to no
    // one (see syncConnected).
    await ready;

    const seen: string[] = [];
    page.on("request", (r) => {
      if (r.method() === "GET") seen.push(r.url());
    });

    // Delete the label from an independent online context → SSE poke → delta with
    // a `label_tombstone`.
    await apiDelete(sideReq, `/api/label/${label.id}`);

    // The chip disappears from the card in the live client, via the delta feed.
    await expect(card.getByText(labelName, { exact: true })).toHaveCount(0, { timeout: 15_000 });
    expect(seen.some((u) => u.includes("/api/changes"))).toBe(true);
  });
});
