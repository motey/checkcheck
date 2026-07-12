/**
 * Local-first persistence + hydration (WI-6).
 *
 * With the `localFirst` flag on, the board must render from the IndexedDB
 * snapshot on reload even when the API is unreachable. The flag is enabled at
 * runtime via `?localFirst=1` (the E2E bundle is a static `nuxt generate` build,
 * so runtimeConfig can't be set per run — see utils/localFirst.ts); the param
 * also persists the choice to localStorage so it survives the reload.
 *
 * "Network blocked" here means the API only: there is no service worker yet
 * (WI-13), so a full offline reload can't fetch the app shell. We abort
 * `**​/api/**` and confirm the board still paints from cache.
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

// Does the persisted IndexedDB snapshot's checklist store contain this card?
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

test.describe("local-first persistence", () => {
  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    await page.unroute("**/api/**").catch(() => {});
    for (const id of cleanup) {
      await apiDelete(page, `/api/checklist/${id}`);
    }
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("board renders from IndexedDB cache when the API is blocked on reload", async ({
    page,
  }) => {
    const clName = `LocalFirst-${Date.now()}`;
    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);

    // Flag on — the query param sets localStorage so it survives the reload.
    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");

    const card = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: clName });
    await expect(card).toBeVisible();

    // Wait until the debounced snapshot persistence has written the card to
    // IndexedDB — otherwise the offline reload would have nothing to hydrate.
    await expect
      .poll(() => snapshotHasCard(page, cl.id), { timeout: 8_000 })
      .toBe(true);

    // Block every API call, then reload. The app shell still loads (served over
    // HTTP, not /api); the board must hydrate from the snapshot.
    await page.route("**/api/**", (route) => route.abort());
    await page.reload();

    await page.waitForSelector("[data-testid=checklist-board]");
    const cachedCard = page
      .locator("[data-testid=checklist-board] .checklist-preview")
      .filter({ hasText: clName });
    await expect(cachedCard).toBeVisible({ timeout: 10_000 });
  });
});
