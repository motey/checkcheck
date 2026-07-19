/**
 * Bulk item operations — "Untick all items" & "Delete ticked items".
 *
 * Two entries in a card's kebab (⋮) menu, both offline-safe (one outbox op each,
 * replayed against a dedicated server endpoint):
 *
 *   • Untick all items   — sets every item's state to unchecked (needs `check`).
 *   • Delete ticked items — soft-deletes every checked item (needs `edit`); it is
 *     destructive, so it goes through a confirm modal.
 *
 * Online: the optimistic store update + delta reconcile leaves the card correct.
 * Offline: the ops queue and drain on reconnect, converging server state.
 *
 * The kebab menu content is teleported to the body, so its items are located at
 * page level (not inside the dialog). Menu action items are located by role+name
 * for robustness; the confirm button is located by its data-testid.
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

async function openCardByTitle(page: Page, clName: string) {
  const card = page
    .locator("[data-testid=checklist-board] .checklist-preview")
    .filter({ hasText: clName });
  await expect(card).toBeVisible();
  await card.locator("[data-testid=card-title]").click();
  const dialog = page.locator('[role="dialog"]:has(.checklist)');
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  return dialog;
}

// Open the card's kebab (⋮) menu (the ellipsis-vertical button in the footer).
async function openKebab(page: Page, dialog: ReturnType<Page["locator"]>) {
  await dialog.locator('button:has([class*="ellipsis-vertical"])').first().click();
}

test.describe("bulk item operations", () => {
  test.setTimeout(45_000);

  const cleanup: string[] = [];

  test.afterEach(async ({ page }) => {
    await page.unroute("**/api/**").catch(() => {});
    for (const id of cleanup) await apiDelete(page, `/api/checklist/${id}`);
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("untick all items unchecks every item and zeroes the checked count", async ({ page }) => {
    const tag = Date.now();
    const clName = `BulkUncheck-${tag}`;

    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    for (const t of ["one", "two", "three"]) {
      await apiPost(page, `/api/checklist/${cl.id}/item`, { text: `${t}-${tag}` });
    }

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCardByTitle(page, clName);
    await expect(dialog.locator("li textarea")).toHaveCount(3, { timeout: 5_000 });

    // Check the first two items.
    await dialog.locator("li").nth(0).getByRole("checkbox").click();
    await dialog.locator("li").nth(1).getByRole("checkbox").click();
    await expect(dialog).toContainText(/2\s+checked items/, { timeout: 5_000 });

    // Kebab → Untick all items.
    await openKebab(page, dialog);
    await page.getByRole("menuitem", { name: "Untick all items" }).click();

    // The checked count returns to 0 (every item unchecked).
    await expect(dialog).toContainText(/0\s+checked items/, { timeout: 5_000 });

    // Converges on the server: nothing checked.
    await expect
      .poll(async () => {
        const items = await page.request
          .get(`/api/checklist/${cl.id}/item?limit=999999`)
          .then((r) => r.json());
        return items.items.filter((i: any) => i.state.checked).length;
      }, { timeout: 10_000 })
      .toBe(0);
  });

  test("delete ticked items removes only the checked items after confirm", async ({ page }) => {
    const tag = Date.now();
    const clName = `BulkDelete-${tag}`;

    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    const keep = await apiPost(page, `/api/checklist/${cl.id}/item`, { text: `keep-${tag}` });
    const gone1 = await apiPost(page, `/api/checklist/${cl.id}/item`, { text: `gone1-${tag}` });
    const gone2 = await apiPost(page, `/api/checklist/${cl.id}/item`, { text: `gone2-${tag}` });
    // Check the two we intend to delete.
    for (const id of [gone1.id, gone2.id]) {
      await page.request.patch(`/api/checklist/${cl.id}/item/${id}/state`, {
        data: { checked: true },
        headers: { "Content-Type": "application/json" },
      });
    }

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCardByTitle(page, clName);
    await expect(dialog.locator("li textarea")).toHaveCount(3, { timeout: 5_000 });

    // Kebab → Delete ticked items → confirm.
    await openKebab(page, dialog);
    await page.getByRole("menuitem", { name: "Delete ticked items" }).click();
    await page.locator("[data-testid=confirm-delete-ticked]").click();

    // Only the unchecked item survives in the editor.
    await expect(dialog.locator("li textarea")).toHaveCount(1, { timeout: 5_000 });
    await expect(dialog.locator("li textarea").first()).toHaveValue(new RegExp(`keep-${tag}`));

    // Server truth: the two checked items are gone, the kept one remains.
    await expect
      .poll(async () => {
        const items = await page.request
          .get(`/api/checklist/${cl.id}/item?limit=999999`)
          .then((r) => r.json());
        return items.items.map((i: any) => i.id).sort();
      }, { timeout: 10_000 })
      .toEqual([keep.id]);
  });

  test("untick all works offline and drains on reconnect", async ({ page }) => {
    const tag = Date.now();
    const clName = `BulkUncheckOffline-${tag}`;

    const cl = await apiPost(page, "/api/checklist", { name: clName });
    cleanup.push(cl.id);
    const a = await apiPost(page, `/api/checklist/${cl.id}/item`, { text: `a-${tag}` });
    const b = await apiPost(page, `/api/checklist/${cl.id}/item`, { text: `b-${tag}` });
    for (const id of [a.id, b.id]) {
      await page.request.patch(`/api/checklist/${cl.id}/item/${id}/state`, {
        data: { checked: true },
        headers: { "Content-Type": "application/json" },
      });
    }

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCardByTitle(page, clName);
    await expect(dialog.locator("li textarea")).toHaveCount(2, { timeout: 5_000 });

    // ── Go offline, untick all via the kebab. ──────────────────────────────────
    await page.route("**/api/**", (route) => route.abort());
    await openKebab(page, dialog);
    await page.getByRole("menuitem", { name: "Untick all items" }).click();

    // Optimistic: checked count 0 and exactly one op queued (with the API blocked
    // nothing could have reached the server yet).
    await expect(dialog).toContainText(/0\s+checked items/, { timeout: 5_000 });
    await expect.poll(() => outboxOpCount(page), { timeout: 8_000 }).toBeGreaterThan(0);

    // ── Restore the API + reload → the outbox drains, server converges. ────────
    await page.unroute("**/api/**");
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => outboxOpCount(page), { timeout: 15_000 }).toBe(0);

    const items = await page.request
      .get(`/api/checklist/${cl.id}/item?limit=999999`)
      .then((r) => r.json());
    expect(items.items.filter((i: any) => i.state.checked).length).toBe(0);
  });
});
