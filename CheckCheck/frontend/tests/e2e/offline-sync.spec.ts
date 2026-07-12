/**
 * Offline sync — conflict, no-revert, and revocation (WI-15).
 *
 * The offline-first release gate: the three multi-actor scenarios that the
 * WI-11 conflict/revocation UX was built for but whose E2E was explicitly
 * deferred here. All run with the `localFirst` flag on (`?localFirst=1`, which
 * persists to localStorage — see utils/localFirst.ts).
 *
 *   1. Two-context concurrent edit — a queued offline edit and a concurrent
 *      server edit of the SAME field. The delta pull keeps the local value
 *      (outbox field-guard, WI-11 finding #2), surfaces a "was also edited
 *      elsewhere" conflict toast, and LWW converges to the local value on drain.
 *   2. Offline edit + concurrent delta for a DIFFERENT row — the queued edit
 *      must NOT visibly revert when an unrelated delta lands; the delta is still
 *      folded in (both survive), and both reach the server on drain.
 *   3. Revocation while offline — an offline editor whose access is revoked sees
 *      the queued write terminally dropped (403 → `op-dropped`), the card removed
 *      from their board, and a "no longer available" toast.
 *
 * Offline for (1)/(2) = the API writes are blocked but GET stays live, so the
 * SSE stream (`GET /api/sync`) and the delta pull (`GET /api/changes`) keep
 * working — this makes the poke→delta→conflict path deterministic (no reconnect
 * race against the outbox drain). (3) uses a real second user + share/revoke.
 *
 * SSE teardown mirrors sync.spec.ts / local-delta-apply.spec.ts: navigate pages
 * to about:blank so the EventSource closes before Playwright tears the browser
 * down (otherwise the runner hangs on the open stream).
 */
import {
  test,
  expect,
  type Page,
  type Browser,
  type BrowserContext,
  type APIRequestContext,
} from "@playwright/test";
import { resolve } from "path";

const AUTH_STATE_FILE = resolve(__dirname, ".auth/state.json");

// Credentials match auth.setup.ts / provisioning_data/test_users.yaml.
const TEST_USER = { username: "testuser01", password: "testuserpw_secure1" };

async function apiPost(req: APIRequestContext, path: string, body: object) {
  const res = await req.post(path, { data: body, headers: { "Content-Type": "application/json" } });
  expect(res.ok(), `POST ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

async function apiPatch(req: APIRequestContext, path: string, body: object) {
  const res = await req.patch(path, { data: body, headers: { "Content-Type": "application/json" } });
  expect(res.ok(), `PATCH ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

async function apiPut(req: APIRequestContext, path: string, body: object) {
  const res = await req.put(path, { data: body, headers: { "Content-Type": "application/json" } });
  expect(res.ok(), `PUT ${path} failed: ${res.status()}`).toBeTruthy();
  return res.json();
}

async function apiDelete(req: APIRequestContext, path: string) {
  await req.delete(path).catch(() => {});
}

// How many ops are queued in the outbox DB right now?
async function outboxOpCount(page: Page): Promise<number> {
  return page.evaluate(
    () =>
      new Promise<number>((resolve) => {
        const req = indexedDB.open("checkcheck-outbox");
        req.onsuccess = () => {
          try {
            const c = req.result.transaction("ops", "readonly").objectStore("ops").count();
            c.onsuccess = () => resolve(c.result);
            c.onerror = () => resolve(-1);
          } catch {
            resolve(-1);
          }
        };
        req.onerror = () => resolve(-1);
      })
  );
}

const cardPreview = (page: Page, name: string) =>
  page.locator("[data-testid=checklist-board] .checklist-preview").filter({ hasText: name });

/** Open a card's editor. After a reload the modal auto-reopens from /card/<id>. */
async function openCard(page: Page, clName: string) {
  const dialog = page.locator('[role="dialog"]');
  try {
    await dialog.waitFor({ state: "visible", timeout: 2_000 });
    return dialog;
  } catch {
    /* not open yet — open it by clicking the card title */
  }
  const card = cardPreview(page, clName);
  await expect(card).toBeVisible();
  await card.locator("[data-testid=card-title]").click();
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  return dialog;
}

// The streaming SSE endpoint must be left completely un-intercepted: routing a
// long-lived `text/event-stream` GET through `route.continue()` breaks its
// streaming, so the poke never arrives. `/api/changes` (a normal JSON GET) is
// excluded too for safety. Everything else under /api is intercepted, and only
// non-GET (the writes) is aborted — this is our "offline" without dropping SSE.
const LIVE_GET = /^\/api\/(sync|changes)\b/;

/** Route filter: block API writes (non-GET) while leaving GET — SSE + delta — live. */
async function blockApiWrites(page: Page) {
  await page.route(
    (url) => url.pathname.startsWith("/api/") && !LIVE_GET.test(url.pathname),
    (route) => (route.request().method() === "GET" ? route.continue() : route.abort())
  );
}

test.describe("offline sync — conflict, no-revert, revocation", () => {
  // Two reloads / drains / cross-context edits: give it room before a hang.
  test.setTimeout(60_000);

  const cleanup: string[] = [];
  let sideReq: APIRequestContext | null = null;
  let extraCtx: BrowserContext | null = null;

  // A fresh ONLINE request context for "elsewhere" edits not subject to a page's
  // route/offline emulation. Reused across tests.
  async function side(browser: Browser): Promise<APIRequestContext> {
    if (!sideReq) {
      const c = await browser.newContext({ storageState: AUTH_STATE_FILE });
      sideReq = c.request;
    }
    return sideReq;
  }

  test.afterEach(async ({ page, browser }) => {
    await page.unrouteAll().catch(() => {});
    const req = await side(browser);
    for (const id of cleanup) await apiDelete(req, `/api/checklist/${id}`);
    cleanup.length = 0;
    if (extraCtx) {
      await Promise.all(extraCtx.pages().map((p) => p.goto("about:blank").catch(() => {})));
      await extraCtx.close().catch(() => {});
      extraCtx = null;
    }
    await page.goto("about:blank").catch(() => {});
  });

  // ── 1. Two-context concurrent edit → local kept + conflict toast + LWW ──────

  test("concurrent edit of the same item keeps the local value and surfaces a conflict", async ({
    page,
    browser,
  }) => {
    const tag = Date.now();
    const clName = `Conflict-${tag}`;
    const localText = `local-edit-${tag}`;
    const remoteText = `remote-edit-${tag}`;

    const req = await side(browser);
    const cl = await apiPost(req, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    const item = await apiPost(req, `/api/checklist/${cl.id}/item`, { text: `orig-${tag}` });

    // Load the board (flag on), open the card, confirm the item renders.
    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCard(page, clName);
    const textarea = dialog.locator("li textarea").first();
    await expect(textarea).toHaveValue(`orig-${tag}`, { timeout: 8_000 });

    // Block writes (GET/SSE stay live). Edit the item text optimistically — it
    // queues in the outbox and the outbox field-guard now protects `text`.
    await blockApiWrites(page);
    await textarea.fill(localText);
    await expect(textarea).toHaveValue(localText);
    await expect.poll(() => outboxOpCount(page), { timeout: 8_000 }).toBeGreaterThan(0);

    // Concurrent edit of the SAME field from an independent online context. This
    // bumps server_seq → SSE poke → the tab pulls /api/changes while its write is
    // still queued, so the divergence is a genuine conflict.
    await apiPatch(req, `/api/checklist/${cl.id}/item/${item.id}`, { text: remoteText });

    // The conflict toast fires and the local value is KEPT (not clobbered).
    // (matches both the visible toast and an aria-live announcer span → .first())
    await expect(page.getByText("was also edited elsewhere").first()).toBeVisible({ timeout: 15_000 });
    await expect(textarea).toHaveValue(localText);

    // Restore writes + reload → the outbox drains → LWW converges the server to
    // the local value (the queued edit wins because it replays last). Reload is
    // the drain trigger because navigator.onLine never flipped (writes were
    // route-aborted, not a real offline), so no "online" event fires the engine.
    await page.unrouteAll();
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => outboxOpCount(page), { timeout: 15_000 }).toBe(0);

    const serverItems = await req
      .get(`/api/checklist/${cl.id}/item?limit=999999`)
      .then((r) => r.json());
    const match = serverItems.items.find((i: any) => i.id === item.id);
    expect(match?.text).toBe(localText);
  });

  // ── 2. Offline edit + concurrent delta for a different row → no revert ──────

  test("a concurrent delta for another item does not revert the queued local edit", async ({
    page,
    browser,
  }) => {
    const tag = Date.now();
    const clName = `NoRevert-${tag}`;
    const localText = `keep-me-${tag}`;
    const remoteText = `arrived-via-delta-${tag}`;

    const req = await side(browser);
    const cl = await apiPost(req, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    const item1 = await apiPost(req, `/api/checklist/${cl.id}/item`, { text: `orig1-${tag}` });

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCard(page, clName);
    const textarea = dialog.locator("li textarea").first();
    await expect(textarea).toHaveValue(`orig1-${tag}`, { timeout: 8_000 });

    // Queue an offline edit to item1.
    await blockApiWrites(page);
    await textarea.fill(localText);
    await expect(textarea).toHaveValue(localText);
    await expect.poll(() => outboxOpCount(page), { timeout: 8_000 }).toBeGreaterThan(0);

    // Elsewhere, ADD a new item2 (an unrelated row). Poke → the tab folds the new
    // item into the card via the delta feed.
    await apiPost(req, `/api/checklist/${cl.id}/item`, { text: remoteText });

    // The delta arrives (a second item textarea appears) AND item1's queued local
    // edit is untouched. In edit mode items render as <textarea>, so match on the
    // textarea VALUE, not text content.
    await expect(dialog.locator("li textarea")).toHaveCount(2, { timeout: 15_000 });
    const values = await dialog
      .locator("li textarea")
      .evaluateAll((els) => els.map((e) => (e as HTMLTextAreaElement).value));
    expect(values).toContain(remoteText);
    await expect(textarea).toHaveValue(localText);

    // Restore writes + reload → drain → both survive on the server.
    await page.unrouteAll();
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => outboxOpCount(page), { timeout: 15_000 }).toBe(0);

    const serverItems = await req
      .get(`/api/checklist/${cl.id}/item?limit=999999`)
      .then((r) => r.json());
    const texts = serverItems.items.map((i: any) => i.text);
    expect(serverItems.items.find((i: any) => i.id === item1.id)?.text).toBe(localText);
    expect(texts).toContain(remoteText);
  });

  // ── 3. Revocation while offline → op-dropped → discard + toast ──────────────

  test("revoking access while a collaborator is offline discards their queued write", async ({
    browser,
  }) => {
    const tag = Date.now();
    const clName = `Revoked-${tag}`;

    // Admin (the owner) owns the card and shares it with testuser01 (edit).
    const admin = await browser.newContext({ storageState: AUTH_STATE_FILE });
    const adminReq = admin.request;
    const cl = await apiPost(adminReq, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    const found = await adminReq
      .get("/api/user/search", { params: { q: TEST_USER.username } })
      .then((r) => r.json());
    const target = found.find((u: any) => u.user_name === TEST_USER.username) ?? found[0];
    expect(target, "testuser01 should be findable").toBeTruthy();
    await apiPut(adminReq, `/api/checklist/${cl.id}/shares/${target.id}`, { permission: "edit" });

    // testuser01 logs in (flag on) in their own context and sees the shared card.
    extraCtx = await browser.newContext();
    const collab = await extraCtx.newPage();
    await collab.goto("/login?localFirst=1");
    await collab.waitForSelector("form");
    await collab.locator("[data-testid=login-username]").fill(TEST_USER.username);
    await collab.locator("[data-testid=login-password]").fill(TEST_USER.password);
    await collab.locator('form button[type="submit"]').click();
    await collab.waitForURL("/");
    await collab.goto("/?localFirst=1");
    await collab.waitForSelector("[data-testid=checklist-board]");
    await expect(cardPreview(collab, clName)).toBeVisible({ timeout: 8_000 });

    // testuser01 goes offline (writes blocked) and renames the card — a checklist
    // update op that queues in the outbox.
    const dialog = await openCard(collab, clName);
    const title = dialog.locator("textarea").first();
    await expect(title).toHaveValue(clName, { timeout: 8_000 });
    await blockApiWrites(collab);
    await title.fill(`${clName}-mine`);
    await expect.poll(() => outboxOpCount(collab), { timeout: 8_000 }).toBeGreaterThan(0);

    // The owner revokes testuser01's access while they are dark.
    const revoke = await adminReq.delete(`/api/checklist/${cl.id}/shares/${target.id}`);
    expect(revoke.status(), "revoke should 204").toBe(204);
    await admin.close();

    // Back online → the queued rename replays to a terminal 403 → op-dropped.
    // The drop no longer splices the card out locally (a 403 can be a mere
    // permission downgrade — Chunk A3); it triggers a delta pull instead, and the
    // real revocation comes back in `removed_checklist_ids`, which removes the card.
    await collab.unrouteAll();
    await collab.goto("about:blank");
    await collab.goto("/?localFirst=1");
    await collab.waitForSelector("[data-testid=checklist-board]");

    await expect(cardPreview(collab, clName)).toHaveCount(0, { timeout: 20_000 });
    await expect.poll(() => outboxOpCount(collab), { timeout: 20_000 }).toBe(0);
  });

  // ── 4. Offline create → cold reload → reconnect → card lands on the server ───
  //
  // Chunk D finding #3: the enqueue→IndexedDB→reboot→drain loop for a *create*.
  // A card created entirely offline must (a) persist its queued create across a
  // cold page reload (still offline), staying on the board from the snapshot, and
  // (b) drain to the server once connectivity returns — the flow the
  // `queuedCreateIds`/`known=` protection exists for.

  // Does the persisted snapshot hold a card with this name yet?
  async function snapshotHasCardNamed(page: Page, name: string): Promise<boolean> {
    return page.evaluate(
      (n) =>
        new Promise<boolean>((resolve) => {
          const req = indexedDB.open("checkcheck-localfirst", 1);
          req.onsuccess = () => {
            try {
              const getReq = req.result
                .transaction("kv", "readonly")
                .objectStore("kv")
                .get("checkList");
              getReq.onsuccess = () => {
                const data: any = getReq.result;
                resolve(
                  !!data && Array.isArray(data.checkLists) && data.checkLists.some((c: any) => c.name === n)
                );
              };
              getReq.onerror = () => resolve(false);
            } catch {
              resolve(false);
            }
          };
          req.onerror = () => resolve(false);
        }),
      name
    );
  }

  test("a card created offline persists across a cold reload and drains on reconnect", async ({
    page,
    browser,
  }) => {
    const tag = Date.now();
    const clName = `Offline-create-${tag}`;
    const req = await side(browser);

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");

    // Go offline (writes blocked, GET/SSE live) and create a card from the UI —
    // `_localCreate` is optimistic: it mints a client id, adds the card, and
    // queues a `create` op without touching the (blocked) network.
    await blockApiWrites(page);
    await page.locator("[data-testid=new-card-button]").click();
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 8_000 });
    await dialog.locator("textarea").first().fill(clName);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden({ timeout: 5_000 });

    // The optimistic card is on the board and its create is queued in IndexedDB.
    await expect(cardPreview(page, clName)).toBeVisible({ timeout: 8_000 });
    await expect.poll(() => outboxOpCount(page), { timeout: 8_000 }).toBeGreaterThan(0);
    // Wait for the debounced snapshot so the cold reload can rehydrate the card.
    await expect.poll(() => snapshotHasCardNamed(page, clName), { timeout: 8_000 }).toBe(true);

    // Cold reload, STILL offline: the card can only come from the IndexedDB
    // snapshot, and its create must still be queued (persisted across the reboot).
    // The delta pull excludes it from `known=`, so it isn't reported as revoked.
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(cardPreview(page, clName)).toBeVisible({ timeout: 8_000 });
    await expect.poll(() => outboxOpCount(page), { timeout: 8_000 }).toBeGreaterThan(0);
    // Not yet on the server (the create never reached it while offline).
    const before = await req.get(`/api/checklist?search=${encodeURIComponent(clName)}&limit=5`).then((r) => r.json());
    expect(before.items.some((c: any) => c.name === clName)).toBe(false);

    // Reconnect + reload (the drain trigger — navigator.onLine never flipped, so
    // the reload re-inits the outbox and drains it).
    await page.unrouteAll();
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => outboxOpCount(page), { timeout: 20_000 }).toBe(0);

    // The card now exists on the server with the name typed offline.
    const after = await req.get(`/api/checklist?search=${encodeURIComponent(clName)}&limit=5`).then((r) => r.json());
    const landed = after.items.find((c: any) => c.name === clName);
    expect(landed, "offline-created card should exist on the server after drain").toBeTruthy();
    cleanup.push(landed.id);
  });
});
