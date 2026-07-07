/**
 * Optimistic-local checklist CRUD + item reorder offline (WI-9).
 *
 * With the `localFirst` flag on, the checklist store's create / update /
 * position actions and the item reorder path mutate local state immediately and
 * enqueue the REST call to the WI-7 outbox instead of awaiting `$checkapi`. This
 * spec proves two offline round-trips:
 *
 *   A. Create a brand-new card offline (client UUID), title it, reload while
 *      still offline (it hydrates from the snapshot), then reconnect → the
 *      outbox drains and the card exists on the server under the same id with
 *      its title.
 *   B. Reorder two items offline (client-side fractional index → plain position
 *      PATCH), reload offline (order persists), reconnect → the server converges
 *      on the new order.
 *
 * Modeled on local-item-offline.spec.ts (WI-8). "Offline" = the API only (no
 * service worker yet, WI-13); the app shell is served over HTTP. The flag is set
 * via `?localFirst=1`, which persists to localStorage so it survives reloads.
 */
import { test, expect, type Locator, type Page } from "@playwright/test";

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

// Does the persisted snapshot's checklist store contain a card with this id?
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
              resolve(
                !!data && Array.isArray(data.checkLists) && data.checkLists.some((c: any) => c.id === id)
              );
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

// The persisted snapshot's name for this card (empty string if absent).
async function snapshotCardName(page: Page, clId: string): Promise<string> {
  return page.evaluate(
    (id) =>
      new Promise<string>((resolve) => {
        const req = indexedDB.open("checkcheck-localfirst");
        req.onsuccess = () => {
          try {
            const g = req.result.transaction("kv", "readonly").objectStore("kv").get("checkList");
            g.onsuccess = () => {
              const data: any = g.result;
              const card = (data?.checkLists ?? []).find((c: any) => c.id === id);
              resolve(card?.name ?? "");
            };
            g.onerror = () => resolve("");
          } catch {
            resolve("");
          }
        };
        req.onerror = () => resolve("");
      }),
    clId
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

// Open the URL-driven card editor: after a reload the modal auto-reopens from
// the /card/<id> path, so tolerate an already-open dialog.
async function openCardByTitle(page: Page, clName: string) {
  const dialog = page.locator('[role="dialog"]');
  try {
    await dialog.waitFor({ state: "visible", timeout: 2_000 });
    return dialog;
  } catch {
    /* not open yet */
  }
  const card = page
    .locator("[data-testid=checklist-board] .checklist-preview")
    .filter({ hasText: clName });
  await expect(card).toBeVisible();
  await card.locator("[data-testid=card-title]").click();
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  return dialog;
}

async function itemOrder(page: Page): Promise<string[]> {
  const textareas = page.locator('[role="dialog"] li textarea');
  const count = await textareas.count();
  const texts: string[] = [];
  for (let i = 0; i < count; i++) texts.push(await textareas.nth(i).inputValue());
  return texts;
}

/**
 * Drag via low-level pointer events to reliably pass @formkit/drag-and-drop's
 * activation threshold. targetYFraction 0.8 = "release 80 % down" = drop after.
 */
async function drag(page: Page, source: Locator, target: Locator, targetYFraction = 0.8) {
  const srcBox = await source.boundingBox();
  const tgtBox = await target.boundingBox();
  if (!srcBox || !tgtBox) throw new Error("Could not read bounding boxes for drag");
  const srcX = srcBox.x + srcBox.width / 2;
  const srcY = srcBox.y + srcBox.height / 2;
  const tgtX = tgtBox.x + tgtBox.width / 2;
  const tgtY = tgtBox.y + tgtBox.height * targetYFraction;
  await page.mouse.move(srcX, srcY);
  await page.mouse.down();
  await page.mouse.move(srcX + 2, srcY + 6, { steps: 5 });
  await page.mouse.move(tgtX, tgtY, { steps: 30 });
  await page.mouse.up();
}

test.describe("local-first checklist offline", () => {
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

  test("a card created and titled offline persists across reload and converges on reconnect", async ({
    page,
  }) => {
    const tag = Date.now();
    const clName = `LocalCard-${tag}`;

    // ── 1. Online: load the board so the snapshot layer is live. ───────────────
    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");

    // ── 2. Go offline and create a new card via the navbar "+". ────────────────
    await page.route("**/api/**", (route) => route.abort());
    await page.locator("[data-testid=new-card-button]").click();
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // The card editor is URL-driven (/card/<uuid>) — read the client-generated id.
    await expect(page).toHaveURL(/\/card\/[0-9a-f-]{36}/, { timeout: 5_000 });
    const clId = page.url().match(/\/card\/([0-9a-f-]{36})/)![1]!;
    cleanup.push(clId);

    // Title it (debounced checklist.update → outbox).
    const title = dialog.locator('textarea[placeholder="Enter a checklist title..."]');
    await expect(title).toBeVisible({ timeout: 5_000 });
    await title.fill(clName);
    await title.blur();

    // Both the create and the title update are queued, and the TITLED card is in
    // the snapshot — with the API blocked nothing could have reached the server.
    // (Poll on the name, not just the id: the title write is debounced, so the
    // card lands in the snapshot before its name does.)
    await expect.poll(() => snapshotCardName(page, clId), { timeout: 8_000 }).toBe(clName);
    await expect.poll(() => outboxOpCount(page), { timeout: 8_000 }).toBeGreaterThan(0);

    // ── 3. Reload while STILL offline — the card hydrates from cache. ──────────
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    const reopened = await openCardByTitle(page, clName);
    await expect(
      reopened.locator('textarea[placeholder="Enter a checklist title..."]')
    ).toHaveValue(clName, { timeout: 10_000 });
    expect(await outboxOpCount(page)).toBeGreaterThan(0);

    // ── 4. Restore the API + reload → the outbox drains to the server. ─────────
    await page.unroute("**/api/**");
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => outboxOpCount(page), { timeout: 15_000 }).toBe(0);

    // Server truth: the card exists under the client id with its title (no dup).
    const serverCard = await page.request.get(`/api/checklist/${clId}`).then((r) => r.json());
    expect(serverCard.id).toBe(clId);
    expect(serverCard.name).toBe(clName);
  });

  test("items reordered offline converge on the server after reconnect", async ({ page }) => {
    const tag = Date.now();
    const clName = `LocalReorder-${tag}`;
    const alpha = `Alpha-${tag}`;
    const beta = `Beta-${tag}`;

    // Two items: alpha (lower index, top) then beta (higher index, below).
    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    await apiPost(page, `/api/checklist/${cl.id}/item`, { text: alpha });
    await apiPost(page, `/api/checklist/${cl.id}/item`, { text: beta });

    // ── 1. Online: open the card, confirm the initial order. ───────────────────
    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCardByTitle(page, clName);
    await expect(dialog.locator("li textarea").nth(0)).toHaveValue(new RegExp(alpha), { timeout: 5_000 });
    await expect(dialog.locator("li textarea").nth(1)).toHaveValue(new RegExp(beta), { timeout: 5_000 });
    await expect.poll(() => snapshotHasCard(page, cl.id), { timeout: 8_000 }).toBe(true);

    // ── 2. Go offline and drag alpha below beta. ───────────────────────────────
    await page.route("**/api/**", (route) => route.abort());
    const before = await itemOrder(page);
    const alphaIdx = before.findIndex((t) => t.includes(alpha));
    const betaIdx = before.findIndex((t) => t.includes(beta));
    expect(alphaIdx).toBeLessThan(betaIdx);

    const alphaRow = dialog.locator("li").nth(alphaIdx);
    const betaRow = dialog.locator("li").nth(betaIdx);
    await alphaRow.hover();
    const handle = alphaRow.locator(".list-item-drag-handle");
    await expect(handle).toBeVisible({ timeout: 3_000 });
    await drag(page, handle, betaRow);

    // Optimistic reorder: beta is now above alpha, and a position op is queued.
    await expect
      .poll(async () => {
        const order = await itemOrder(page);
        return order.findIndex((t) => t.includes(beta)) < order.findIndex((t) => t.includes(alpha));
      }, { timeout: 5_000 })
      .toBe(true);
    await expect.poll(() => outboxOpCount(page), { timeout: 8_000 }).toBeGreaterThan(0);

    // ── 3. Restore the API + reload → the outbox drains and order converges. ───
    await page.unroute("**/api/**");
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => outboxOpCount(page), { timeout: 15_000 }).toBe(0);

    // Server truth: beta now sorts before alpha (lower position.index).
    const serverItems = await page.request
      .get(`/api/checklist/${cl.id}/item?limit=999999`)
      .then((r) => r.json());
    const items: any[] = [...serverItems.items].sort((a, b) => a.position.index - b.position.index);
    const serverAlphaIdx = items.findIndex((i) => (i.text ?? "").includes(alpha));
    const serverBetaIdx = items.findIndex((i) => (i.text ?? "").includes(beta));
    expect(serverBetaIdx, "beta should sort before alpha on the server").toBeLessThan(serverAlphaIdx);
  });
});
