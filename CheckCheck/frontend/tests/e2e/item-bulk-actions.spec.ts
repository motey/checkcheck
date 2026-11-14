/**
 * More card bulk actions: "Check all items", "Sort items by name", "Remove
 * duplicates" and "Copy list as text" from the card's kebab menu.
 *
 * The first three are server-side bulk endpoints replayed as one outbox op each,
 * so they also work offline; copy is client only. See also bulk-item-ops.spec.ts
 * for uncheck all / delete checked.
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

async function setChecked(page: Page, clId: string, itemId: string, checked: boolean) {
  const res = await page.request.patch(`/api/checklist/${clId}/item/${itemId}/state`, {
    data: { checked },
    headers: { "Content-Type": "application/json" },
  });
  expect(res.ok()).toBeTruthy();
}

async function serverItems(page: Page, clId: string): Promise<Array<{ id: string; text: string; state: { checked: boolean } }>> {
  const res = await page.request.get(`/api/checklist/${clId}/item?limit=999999`);
  return (await res.json()).items;
}

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

async function openCardByTitle(page: Page, clName: string) {
  const card = page.locator("[data-testid=checklist-board] .checklist-preview").filter({ hasText: clName });
  await expect(card).toBeVisible();
  await card.locator("[data-testid=card-title]").click();
  const dialog = page.locator('[role="dialog"]:has(.checklist)');
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  return dialog;
}

async function openKebab(dialog: ReturnType<Page["locator"]>) {
  await dialog.locator('button:has([class*="ellipsis-vertical"])').first().click();
}

async function makeCard(page: Page, name: string, items: Array<[string, boolean?]>) {
  const cl = await apiPost(page, "/api/checklist", { name });
  const ids: string[] = [];
  for (const [text, checked] of items) {
    const it = await apiPost(page, `/api/checklist/${cl.id}/item`, { text });
    if (checked) await setChecked(page, cl.id, it.id, true);
    ids.push(it.id);
  }
  return { id: cl.id as string, ids };
}

test.describe("more item bulk actions", () => {
  test.setTimeout(60_000);

  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    await page.unroute("**/api/**").catch(() => {});
    for (const id of cleanup) await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("check all items checks every item", async ({ page }) => {
    const tag = Date.now();
    const name = `BulkCheckAll-${tag}`;
    const cl = await makeCard(page, name, [["one"], ["two", true], ["three"]]);
    cleanup.push(cl.id);

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCardByTitle(page, name);
    await expect(dialog).toContainText(/1\s+checked item/, { timeout: 5_000 });

    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Check all items", exact: true }).click();
    await expect(dialog).toContainText(/3\s+checked items/, { timeout: 5_000 });

    await expect
      .poll(async () => (await serverItems(page, cl.id)).filter((i) => !i.state.checked).length, { timeout: 10_000 })
      .toBe(0);
  });

  test("sort items by name orders the list A to Z", async ({ page }) => {
    const tag = Date.now();
    const name = `BulkSort-${tag}`;
    const cl = await makeCard(page, name, [["banana"], ["item 10"], ["Apple"], ["cherry"], ["item 2"]]);
    cleanup.push(cl.id);

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCardByTitle(page, name);
    const texts = dialog.locator("[data-testid=item-text-rendered]");
    await expect(texts).toHaveCount(5, { timeout: 5_000 });

    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Sort items by name", exact: true }).click();

    const expected = ["Apple", "banana", "cherry", "item 2", "item 10"];
    await expect(texts).toHaveText(expected, { timeout: 5_000 });
    await expect
      .poll(async () => (await serverItems(page, cl.id)).map((i) => i.text), { timeout: 10_000 })
      .toEqual(expected);
  });

  test("remove duplicates asks first, then keeps one copy (unchecked preferred)", async ({ page }) => {
    const tag = Date.now();
    const name = `BulkDedup-${tag}`;
    const cl = await makeCard(page, name, [["Milk", true], ["milk "], ["eggs"], ["EGGS"], ["bread"]]);
    cleanup.push(cl.id);
    const [, milkUnchecked, eggsTop, , bread] = cl.ids;

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCardByTitle(page, name);
    await expect(dialog.locator("[data-testid=item-row]")).toHaveCount(5, { timeout: 5_000 });

    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Remove duplicates", exact: true }).click();
    await expect(page.locator("[data-testid=remove-duplicates-summary]")).toContainText("2 duplicate items will be deleted");
    await page.locator("[data-testid=confirm-remove-duplicates]").click();

    await expect(dialog.locator("[data-testid=item-row]")).toHaveCount(3, { timeout: 5_000 });
    await expect
      .poll(async () => (await serverItems(page, cl.id)).map((i) => i.id).sort(), { timeout: 10_000 })
      .toEqual([milkUnchecked, eggsTop, bread].sort());
  });

  test("copy list as text puts a Markdown checklist on the clipboard", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const tag = Date.now();
    const name = `BulkCopy-${tag}`;
    const cl = await makeCard(page, name, [["milk"], ["eggs", true], ["bread"]]);
    cleanup.push(cl.id);

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCardByTitle(page, name);
    await expect(dialog.locator("[data-testid=item-row]")).toHaveCount(3, { timeout: 5_000 });

    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Copy list as text", exact: true }).click();
    await expect(page.getByText("List copied", { exact: true })).toBeVisible({ timeout: 5_000 });

    // "Separate checked items" is on by default: unchecked first, then checked.
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip).toBe("- [ ] milk\n- [ ] bread\n- [x] eggs");
  });

  test("check all, sort and remove duplicates work offline and drain in order", async ({ page }) => {
    const tag = Date.now();
    const name = `BulkOffline-${tag}`;
    const cl = await makeCard(page, name, [["pears"], ["Apples"], ["pears"], ["figs"]]);
    cleanup.push(cl.id);

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCardByTitle(page, name);
    const texts = dialog.locator("[data-testid=item-text-rendered]");
    await expect(texts).toHaveCount(4, { timeout: 5_000 });

    // ── Offline: sort, remove duplicates, check all. ──────────────────────────
    await page.route("**/api/**", (route) => route.abort());

    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Sort items by name", exact: true }).click();
    await expect(texts).toHaveText(["Apples", "figs", "pears", "pears"], { timeout: 5_000 });

    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Remove duplicates", exact: true }).click();
    await expect(page.locator("[data-testid=remove-duplicates-summary]")).toContainText("1 duplicate item will be deleted");
    await page.locator("[data-testid=confirm-remove-duplicates]").click();
    await expect(texts).toHaveCount(3, { timeout: 5_000 });

    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Check all items", exact: true }).click();
    await expect(dialog).toContainText(/3\s+checked items/, { timeout: 5_000 });

    await expect.poll(() => outboxOpCount(page), { timeout: 8_000 }).toBe(3);

    // ── Back online: the queue drains, the server converges. ───────────────────
    await page.unroute("**/api/**");
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => outboxOpCount(page), { timeout: 15_000 }).toBe(0);

    const items = await serverItems(page, cl.id);
    expect(items.map((i) => i.text)).toEqual(["Apples", "figs", "pears"]);
    expect(items.every((i) => i.state.checked)).toBe(true);
  });
});
