/**
 * Optimistic-local item CRUD offline (WI-8).
 *
 * With the `localFirst` flag on, the checklist_item store's create / update /
 * updateState mutate local state immediately and enqueue the REST call to the
 * WI-7 outbox instead of awaiting `$checkapi`. This spec proves the full offline
 * round-trip:
 *
 *   1. Online: create a card, open it, confirm it's in the IndexedDB snapshot.
 *   2. Offline (API aborted): add an item, type text, check it — all optimistic.
 *      The ops queue in `checkcheck-outbox`; the item persists to the snapshot.
 *   3. Reload still offline → the item hydrates from cache (reopen the card and
 *      see it) and the ops are still queued.
 *   4. Restore the API + reload → the outbox drains and the item — with its text
 *      and checked state — is on the server.
 *
 * Modeled on local-persistence.spec.ts. "Offline" = the API only (no service
 * worker yet, WI-13); the app shell is served over HTTP. The flag is set via
 * `?localFirst=1`, which also persists to localStorage so it survives reloads
 * (see utils/localFirst.ts).
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

// Does the persisted snapshot's checklist store contain this card?
async function snapshotHasCard(page: Page, clId: string): Promise<boolean> {
  return page.evaluate(
    (id) =>
      new Promise<boolean>((resolve) => {
        const req = indexedDB.open("checkcheck-localfirst");
        req.onsuccess = () => {
          try {
            const g = req.result.transaction("kv", "readonly").objectStore("kv").get("checkList");
            g.onsuccess = () => {
              const data: any = g.result;
              resolve(!!data && Array.isArray(data.checkLists) && data.checkLists.some((c: any) => c.id === id));
            };
            g.onerror = () => resolve(false);
          } catch {
            resolve(false);
          }
        };
        req.onerror = () => resolve(false);
      }),
    clId
  );
}

// Does the persisted snapshot's item store hold an item with this text for the card?
async function snapshotHasItemText(page: Page, clId: string, text: string): Promise<boolean> {
  return page.evaluate(
    ({ id, text }) =>
      new Promise<boolean>((resolve) => {
        const req = indexedDB.open("checkcheck-localfirst");
        req.onsuccess = () => {
          try {
            const g = req.result.transaction("kv", "readonly").objectStore("kv").get("checkListitem");
            g.onsuccess = () => {
              const data: any = g.result;
              const items: any[] = data?.checkListsItems?.[id] ?? [];
              resolve(items.some((i) => (i?.text ?? "").includes(text)));
            };
            g.onerror = () => resolve(false);
          } catch {
            resolve(false);
          }
        };
        req.onerror = () => resolve(false);
      }),
    { id: clId, text }
  );
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

async function openCard(page: Page, clName: string) {
  const dialog = page.locator('[role="dialog"]');
  // The editor is URL-driven (/card/<id>), so after a reload the modal
  // auto-reopens from the path — in that case don't click the (overlay-covered)
  // title, just use the open dialog.
  try {
    await dialog.waitFor({ state: "visible", timeout: 2_000 });
    return dialog;
  } catch {
    /* not open yet — open it by clicking the card title */
  }
  const card = page
    .locator("[data-testid=checklist-board] .checklist-preview")
    .filter({ hasText: clName });
  await expect(card).toBeVisible();
  await card.locator("[data-testid=card-title]").click();
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  return dialog;
}

test.describe("local-first item offline", () => {
  // Two reloads + outbox drain: give it room before declaring a hang.
  test.setTimeout(45_000);

  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    await page.unroute("**/api/**").catch(() => {});
    for (const id of cleanup) {
      await apiDelete(page, `/api/checklist/${id}`);
    }
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("item created/edited/checked offline persists across reload and converges on reconnect", async ({
    page,
  }) => {
    const tag = Date.now();
    const clName = `LocalItem-${tag}`;
    const itemText = `offline-item-${tag}`;

    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);

    // ── 1. Online: load the board, open the (empty) card, confirm it's cached. ──
    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    let dialog = await openCard(page, clName);
    await expect(dialog.locator("[data-testid=item-row]")).toHaveCount(0);

    // Wait until the snapshot layer has persisted the card, so an offline reload
    // has something to hydrate.
    await expect.poll(() => snapshotHasCard(page, cl.id), { timeout: 8_000 }).toBe(true);

    // ── 2. Go offline (block the API) and mutate the item optimistically. ──────
    await page.route("**/api/**", (route) => route.abort());

    await dialog.locator("[data-testid=add-item]").click();
    const textarea = dialog.locator("[data-testid=item-text-editor]").first();
    await expect(textarea).toBeVisible({ timeout: 5_000 });
    await textarea.fill(itemText);
    await expect(textarea).toHaveValue(itemText);

    // The optimistic create + edit reached the snapshot and queued in the outbox —
    // with the API blocked, nothing could have gone to the server.
    await expect.poll(() => snapshotHasItemText(page, cl.id, itemText), { timeout: 8_000 }).toBe(true);
    await expect.poll(() => outboxOpCount(page), { timeout: 8_000 }).toBeGreaterThan(0);

    // ── 3. Reload while STILL offline — the item hydrates from cache. ──────────
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    dialog = await openCard(page, clName);
    const reopened = dialog.locator("[data-testid=item-text-rendered]").first();
    await expect(reopened).toContainText(itemText, { timeout: 10_000 });
    // Ops survived the restart, still unsent.
    const opsBeforeCheck = await outboxOpCount(page);
    expect(opsBeforeCheck).toBeGreaterThan(0);

    // Check it now (exercises updateState offline). Nuxt UI renders the checkbox
    // as a <button role="checkbox"> (see sharing-gating.spec.ts). Checking moves
    // the item into the (separated) checked section, so we don't re-assert the
    // unchecked list — the final server-state check below proves it stuck.
    await dialog.locator("li").first().getByRole("checkbox").click();
    // Wait for the state op to persist before reloading, so it isn't lost.
    await expect.poll(() => outboxOpCount(page), { timeout: 8_000 }).toBeGreaterThan(opsBeforeCheck);

    // ── 4. Restore the API + reload → the outbox drains to the server. ─────────
    await page.unroute("**/api/**");
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => outboxOpCount(page), { timeout: 15_000 }).toBe(0);

    // Server truth: the item exists with its text and checked state, under the
    // client-generated id (no duplicate).
    const serverItems = await page.request
      .get(`/api/checklist/${cl.id}/item?limit=999999`)
      .then((r) => r.json());
    const match = serverItems.items.find((i: any) => (i.text ?? "").includes(itemText));
    expect(match, "the offline item should be on the server after draining").toBeTruthy();
    expect(match.state.checked).toBe(true);
  });
});
