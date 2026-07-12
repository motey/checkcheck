/**
 * Offline cold start — service worker app shell (WI-13).
 *
 * This is the WI-13 done-when: with the `localFirst` flag on, a returning user
 * can COLD START the app with no network at all — not just "API blocked" (that
 * is WI-6's local-persistence spec, which keeps the shell reachable over HTTP).
 * Here the whole context goes offline, so the app shell (HTML/JS/CSS) can only
 * come from the Workbox precache installed by the service worker, and the board
 * data can only come from the IndexedDB snapshot (WI-6).
 *
 * It also exercises the offline-auth grace (plugins/api.ts): the board's
 * `fetchMe` / `/api/changes` calls fail offline, and the user must stay on the
 * cached board instead of being bounced to `/login`.
 *
 * Service workers only ship in a real build, so this relies on the E2E harness's
 * `nuxt generate` output (served by the backend on localhost — a secure context
 * for SW registration).
 */
import { test, expect, type Page } from "@playwright/test";

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

async function snapshotHasCard(page: Page, clId: string): Promise<boolean> {
  return page.evaluate(
    (id) =>
      new Promise<boolean>((resolve) => {
        const req = indexedDB.open("checkcheck-localfirst", 1);
        req.onsuccess = () => {
          try {
            const db = req.result;
            const getReq = db.transaction("kv", "readonly").objectStore("kv").get("checkList");
            getReq.onsuccess = () => {
              const data: any = getReq.result;
              resolve(
                !!data &&
                  Array.isArray(data.checkLists) &&
                  data.checkLists.some((c: any) => c.id === id)
              );
            };
            getReq.onerror = () => resolve(false);
          } catch {
            resolve(false);
          }
        };
        req.onerror = () => resolve(false);
      }),
    clId
  );
}

test.describe("offline cold start (service worker)", () => {
  const cleanup: string[] = [];

  test.afterEach(async ({ page, context }) => {
    await context.setOffline(false).catch(() => {});
    for (const id of cleanup) {
      await apiDelete(page, `/api/checklist/${id}`);
    }
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("board cold-starts from the SW-cached shell + IndexedDB when fully offline", async ({
    page,
    context,
  }) => {
    const clName = `Offline-${Date.now()}`;
    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);

    // Flag on — the query param persists to localStorage so it survives reloads.
    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(
      page
        .locator("[data-testid=checklist-board] .checklist-preview")
        .filter({ hasText: clName })
    ).toBeVisible();

    // Wait for the service worker to finish installing (shell precached) and for
    // the debounced snapshot to hit IndexedDB — both are preconditions for an
    // offline cold start.
    await page.waitForFunction(() => navigator.serviceWorker?.ready.then(() => true));
    await expect
      .poll(() => snapshotHasCard(page, cl.id), { timeout: 8_000 })
      .toBe(true);

    // Reload once so this client is CONTROLLED by the active worker (Workbox
    // doesn't clientsClaim on first install), then confirm control.
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect
      .poll(() => page.evaluate(() => !!navigator.serviceWorker.controller), {
        timeout: 8_000,
      })
      .toBe(true);

    // Full airplane mode: no shell, no API. The next load can ONLY be served by
    // the service worker precache + IndexedDB.
    await context.setOffline(true);
    await page.reload();

    await page.waitForSelector("[data-testid=checklist-board]", { timeout: 10_000 });
    await expect(
      page
        .locator("[data-testid=checklist-board] .checklist-preview")
        .filter({ hasText: clName })
    ).toBeVisible({ timeout: 10_000 });

    // Offline-auth grace: a failed offline session check must NOT bounce to login.
    await page.waitForTimeout(500);
    expect(new URL(page.url()).pathname).not.toBe("/login");
  });
});
