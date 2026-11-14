/**
 * "Add items from text" from the card's kebab menu: a dialog whose lines become
 * items, appended in order after the existing ones. Each item is a normal
 * optimistic create (one outbox op each), so it works offline too, and the text
 * written by "Copy list as text" reads back as the same items.
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

async function serverItems(page: Page, clId: string): Promise<Array<{ text: string; state: { checked: boolean } }>> {
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

async function openAddFromText(page: Page, dialog: ReturnType<Page["locator"]>) {
  await dialog.locator('button:has([class*="ellipsis-vertical"])').first().click();
  await page.getByRole("menuitem", { name: "Add items from text", exact: true }).click();
  const textarea = page.getByTestId("add-items-text");
  await expect(textarea).toBeVisible();
  return textarea;
}

test.describe("add items from text", () => {
  test.setTimeout(60_000);

  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    await page.unroute("**/api/**").catch(() => {});
    for (const id of cleanup) await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("each line becomes an item after the existing ones, markers removed", async ({ page }) => {
    const tag = Date.now();
    const name = `AddFromText-${tag}`;
    const cl = await apiPost(page, "/api/checklist", { name });
    cleanup.push(cl.id);
    await apiPost(page, `/api/checklist/${cl.id}/item`, { text: "existing" });

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCardByTitle(page, name);
    await expect(dialog.locator("[data-testid=item-row]")).toHaveCount(1, { timeout: 5_000 });

    const textarea = await openAddFromText(page, dialog);
    const confirm = page.getByTestId("confirm-add-items");
    await expect(confirm).toBeDisabled();
    await textarea.fill("- [ ] milk\n- [x] eggs\n\n* bread\n1. cheese\n");
    await expect(page.getByTestId("add-items-count")).toHaveText("4 items will be added, 1 of them checked.");
    await confirm.click();

    await expect(page.getByTestId("add-items-text")).toHaveCount(0);
    await expect(page.getByText("4 items added", { exact: true })).toBeVisible({ timeout: 5_000 });
    await expect(dialog.locator("[data-testid=item-row]")).toHaveCount(5, { timeout: 5_000 });

    await expect
      .poll(async () => (await serverItems(page, cl.id)).map((i) => `${i.state.checked ? "x" : " "} ${i.text}`), {
        timeout: 10_000,
      })
      .toEqual(["  existing", "  milk", "x eggs", "  bread", "  cheese"]);
  });

  test("cancel discards the text", async ({ page }) => {
    const tag = Date.now();
    const name = `AddFromTextCancel-${tag}`;
    const cl = await apiPost(page, "/api/checklist", { name });
    cleanup.push(cl.id);

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCardByTitle(page, name);

    let textarea = await openAddFromText(page, dialog);
    await textarea.fill("one\ntwo");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByTestId("add-items-text")).toHaveCount(0);
    expect(await serverItems(page, cl.id)).toHaveLength(0);

    // Reopening starts empty.
    textarea = await openAddFromText(page, dialog);
    await expect(textarea).toHaveValue("");
  });

  test("copy list as text from one card adds the same items to another, offline", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const tag = Date.now();
    const source = await apiPost(page, "/api/checklist", { name: `CopySource-${tag}` });
    const target = await apiPost(page, "/api/checklist", { name: `PasteTarget-${tag}` });
    cleanup.push(source.id, target.id);
    for (const text of ["milk", "eggs", "- odd one"]) {
      const it = await apiPost(page, `/api/checklist/${source.id}/item`, { text });
      if (text === "eggs") {
        await page.request.patch(`/api/checklist/${source.id}/item/${it.id}/state`, {
          data: { checked: true },
          headers: { "Content-Type": "application/json" },
        });
      }
    }

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");

    // Copy from the source card.
    let dialog = await openCardByTitle(page, `CopySource-${tag}`);
    await expect(dialog.locator("[data-testid=item-row]")).toHaveCount(3, { timeout: 5_000 });
    await dialog.locator('button:has([class*="ellipsis-vertical"])').first().click();
    await page.getByRole("menuitem", { name: "Copy list as text", exact: true }).click();
    await expect(page.getByText("List copied", { exact: true })).toBeVisible({ timeout: 5_000 });
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    await dialog.getByRole("button", { name: "Close" }).first().click();
    await expect(dialog).toHaveCount(0, { timeout: 5_000 });

    // Paste into the target card while offline.
    dialog = await openCardByTitle(page, `PasteTarget-${tag}`);
    await page.route("**/api/**", (route) => route.abort());
    const textarea = await openAddFromText(page, dialog);
    await textarea.fill(copied);
    await expect(page.getByTestId("add-items-count")).toHaveText("3 items will be added, 1 of them checked.");
    await page.getByTestId("confirm-add-items").click();
    await expect(dialog.locator("[data-testid=item-row]")).toHaveCount(3, { timeout: 5_000 });
    await expect.poll(() => outboxOpCount(page), { timeout: 8_000 }).toBe(3);

    // Back online: the creates drain and the target matches the source.
    await page.unroute("**/api/**");
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => outboxOpCount(page), { timeout: 15_000 }).toBe(0);

    const summary = (items: Array<{ text: string; state: { checked: boolean } }>) =>
      items.map((i) => `${i.state.checked ? "x" : " "} ${i.text}`).sort();
    expect(summary(await serverItems(page, target.id))).toEqual(summary(await serverItems(page, source.id)));
  });
});
