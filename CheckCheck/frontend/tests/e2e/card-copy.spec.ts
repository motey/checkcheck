/**
 * Card copy actions in a card's kebab (⋮) menu:
 *
 *   • Duplicate card: a new card "<name> (copy)" with the note, color, labels
 *     and all items, unchecked.
 *   • Move checked items to new card: after a confirm, the checked items leave
 *     the card for a new card "<name> (done)", still checked.
 *
 * Both are one server call (one outbox op) and work offline: the new card shows
 * at once, the op drains on reconnect, and the board converges on the server's
 * card without a duplicate.
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

async function check(page: Page, clId: string, itemId: string) {
  await page.request.patch(`/api/checklist/${clId}/item/${itemId}/state`, {
    data: { checked: true },
    headers: { "Content-Type": "application/json" },
  });
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

/** The caller's live cards whose name is exactly `name`. */
async function serverCardsNamed(page: Page, name: string): Promise<any[]> {
  const res = await page.request.get("/api/checklist?limit=999").then((r) => r.json());
  return res.items.filter((c: any) => c.name === name);
}

async function serverItems(page: Page, clId: string): Promise<any[]> {
  const res = await page.request.get(`/api/checklist/${clId}/item?limit=999999`).then((r) => r.json());
  return res.items;
}

/** A board card by its exact title (a copy's title contains the source's). */
function boardCard(page: Page, title: string) {
  return page
    .locator("[data-testid=checklist-board] .checklist-preview")
    .filter({ has: page.locator("[data-testid=card-title]", { hasText: new RegExp(`^\\s*${escapeRe(title)}\\s*$`) }) });
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function openCard(page: Page, title: string) {
  const card = boardCard(page, title);
  await expect(card).toBeVisible();
  await card.locator("[data-testid=card-title]").click();
  const dialog = page.locator('[role="dialog"]:has(.checklist)');
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  return dialog;
}

async function openKebab(dialog: ReturnType<Page["locator"]>) {
  await dialog.locator('button:has([class*="ellipsis-vertical"])').first().click();
}

/** A card with three items (`one`, `two`, `three`), `one` and `three` checked, and a label. */
async function seedCard(page: Page, name: string, tag: number) {
  const color = (await page.request.get("/api/color").then((r) => r.json()))[0];
  const cl = await apiPost(page, "/api/checklist", { name, text: `note-${tag}`, color_id: color.id });
  const ids: string[] = [];
  for (const t of ["one", "two", "three"]) {
    ids.push((await apiPost(page, `/api/checklist/${cl.id}/item`, { text: `${t}-${tag}` })).id);
  }
  await check(page, cl.id, ids[0]!);
  await check(page, cl.id, ids[2]!);
  const label = await apiPost(page, "/api/label", { display_name: `lbl-${tag}` });
  await page.request.put(`/api/checklist/${cl.id}/label/${label.id}`);
  return { cl, ids, label, color };
}

test.describe("card copy actions", () => {
  test.setTimeout(60_000);

  const cleanupCards: string[] = [];
  const cleanupLabels: string[] = [];

  test.afterEach(async ({ page }) => {
    await page.unroute("**/api/**").catch(() => {});
    for (const id of cleanupCards) await apiDelete(page, `/api/checklist/${id}`);
    for (const id of cleanupLabels) await apiDelete(page, `/api/label/${id}`);
    cleanupCards.length = 0;
    cleanupLabels.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("duplicate card copies note, color, label and all items unchecked", async ({ page }) => {
    const tag = Date.now();
    const name = `Dup-${tag}`;
    const { cl, label, color } = await seedCard(page, name, tag);
    cleanupCards.push(cl.id);
    cleanupLabels.push(label.id);

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCard(page, name);
    await expect(dialog.locator("[data-testid=item-row]")).toHaveCount(3, { timeout: 5_000 });

    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Duplicate card" }).click();
    await expect(page.getByText("Card duplicated", { exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(dialog).toBeHidden({ timeout: 5_000 });

    // The copy is on the board right away, and exactly once.
    await expect(boardCard(page, `${name} (copy)`)).toHaveCount(1, { timeout: 5_000 });

    // Server truth.
    await expect.poll(async () => (await serverCardsNamed(page, `${name} (copy)`)).length, { timeout: 10_000 }).toBe(1);
    const [copy] = await serverCardsNamed(page, `${name} (copy)`);
    cleanupCards.push(copy.id);
    expect(copy.text).toBe(`note-${tag}`);
    expect(copy.color_id).toBe(color.id);
    expect(copy.labels.map((l: any) => l.id)).toEqual([label.id]);
    const items = await serverItems(page, copy.id);
    expect(items.map((i: any) => i.text)).toEqual([`one-${tag}`, `two-${tag}`, `three-${tag}`]);
    expect(items.every((i: any) => !i.state.checked)).toBe(true);

    // The board's copy holds the server's rows (same ids), not a second set.
    const copyDialog = await openCard(page, `${name} (copy)`);
    await expect(copyDialog.locator("[data-testid=item-row]")).toHaveCount(3, { timeout: 5_000 });
  });

  test("move checked items asks first, then moves them to a new card", async ({ page }) => {
    const tag = Date.now();
    const name = `Move-${tag}`;
    const { cl, ids, label } = await seedCard(page, name, tag);
    cleanupCards.push(cl.id);
    cleanupLabels.push(label.id);

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCard(page, name);
    await expect(dialog.locator("[data-testid=item-row]")).toHaveCount(3, { timeout: 5_000 });

    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Move checked items to new card" }).click();
    const confirm = page.locator("[data-testid=confirm-move-checked]");
    await expect(confirm).toBeVisible();
    await expect(page.getByText("2 checked items move to a new card")).toBeVisible();
    await confirm.click();

    // Only the unchecked item stays in the open card.
    await expect(dialog.locator("[data-testid=item-row]")).toHaveCount(1, { timeout: 5_000 });
    await expect(dialog.locator("[data-testid=item-text-rendered]").first()).toContainText(`two-${tag}`);

    await expect.poll(async () => (await serverCardsNamed(page, `${name} (done)`)).length, { timeout: 10_000 }).toBe(1);
    const [done] = await serverCardsNamed(page, `${name} (done)`);
    cleanupCards.push(done.id);
    const moved = await serverItems(page, done.id);
    expect(moved.map((i: any) => [i.text, i.state.checked])).toEqual([
      [`one-${tag}`, true],
      [`three-${tag}`, true],
    ]);
    expect((await serverItems(page, cl.id)).map((i: any) => i.id)).toEqual([ids[1]]);

    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(boardCard(page, `${name} (done)`)).toHaveCount(1, { timeout: 5_000 });
  });

  test("a copy lands right after its source, also in the pinned section", async ({ page }) => {
    const tag = Date.now();
    // New cards go on top, so creating C, B, A gives the board order A, B, C.
    const names = { a: `Place-A-${tag}`, b: `Place-B-${tag}`, c: `Place-C-${tag}` };
    const ids: Record<string, string> = {};
    for (const key of ["c", "b", "a"] as const) {
      ids[key] = (await apiPost(page, "/api/checklist", { name: names[key] })).id;
      cleanupCards.push(ids[key]!);
    }

    /** Our cards' titles in one board section, top to bottom. */
    const order = (board: "checklist-board" | "pinned-board") =>
      page
        .locator(`[data-testid=${board}] > li[data-card-id] [data-testid=card-title]`)
        .allInnerTexts()
        .then((titles) => titles.map((t) => t.trim()).filter((t) => t.includes(`-${tag}`)));

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => order("checklist-board")).toEqual([names.a, names.b, names.c]);

    let dialog = await openCard(page, names.b);
    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Duplicate card" }).click();
    await expect(page.getByText("Card duplicated", { exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(dialog).toBeHidden({ timeout: 5_000 });
    const copyName = `${names.b} (copy)`;
    await expect.poll(() => order("checklist-board")).toEqual([names.a, names.b, copyName, names.c]);

    // The server stored the same slot: after a reload the order is unchanged.
    await expect.poll(async () => (await serverCardsNamed(page, copyName)).length, { timeout: 10_000 }).toBe(1);
    const [copy] = await serverCardsNamed(page, copyName);
    cleanupCards.push(copy.id);
    expect(copy.position.pinned).toBe(false);
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => order("checklist-board")).toEqual([names.a, names.b, copyName, names.c]);

    // Pinned: pin A and C, duplicate C; the copy is pinned and follows C.
    for (const key of ["a", "c"] as const) {
      await page.request.patch(`/api/checklist/${ids[key]}/position`, {
        data: { pinned: true },
        headers: { "Content-Type": "application/json" },
      });
    }
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => order("pinned-board")).toEqual([names.a, names.c]);

    dialog = await page
      .locator("[data-testid=pinned-board] .checklist-preview")
      .filter({ hasText: names.c })
      .locator("[data-testid=card-title]")
      .click()
      .then(() => page.locator('[role="dialog"]:has(.checklist)'));
    await expect(dialog).toBeVisible({ timeout: 5_000 });
    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Duplicate card" }).click();
    await expect(page.getByText("Card duplicated", { exact: true }).first()).toBeVisible();
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(dialog).toBeHidden({ timeout: 5_000 });
    const pinnedCopyName = `${names.c} (copy)`;
    await expect.poll(() => order("pinned-board")).toEqual([names.a, names.c, pinnedCopyName]);

    await expect.poll(async () => (await serverCardsNamed(page, pinnedCopyName)).length, { timeout: 10_000 }).toBe(1);
    const [pinnedCopy] = await serverCardsNamed(page, pinnedCopyName);
    cleanupCards.push(pinnedCopy.id);
    expect(pinnedCopy.position.pinned).toBe(true);
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => order("pinned-board")).toEqual([names.a, names.c, pinnedCopyName]);
  });

  test("animates the copy out of the source card, unless reduced motion is asked for", async ({ page }) => {
    // The suite emulates prefers-reduced-motion (playwright.config.ts), so a
    // copy is instant first; with motion allowed, the board plays the FLIP:
    // the copy raised above the others, the pushed cards sliding.
    const tag = Date.now();
    const name = `Anim-${tag}`;
    const { cl, label } = await seedCard(page, name, tag);
    cleanupCards.push(cl.id);
    cleanupLabels.push(label.id);

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCard(page, name);

    /** Every animation frame for `ms`: which cards run the flip, and which is raised. */
    const watchFlips = (ms: number) =>
      page.evaluate(
        (ms) =>
          new Promise<{ animated: string[]; raised: string[] }>((resolve) => {
            const animated = new Set<string>();
            const raised = new Set<string>();
            const end = performance.now() + ms;
            const tick = () => {
              for (const el of document.querySelectorAll<HTMLElement>("li[data-card-id]")) {
                if (!el.getAnimations().some((a) => a.id === "card-flip")) continue;
                animated.add(el.dataset.cardId!);
                if (el.style.zIndex === "20") raised.add(el.dataset.cardId!);
              }
              if (performance.now() < end) requestAnimationFrame(tick);
              else resolve({ animated: [...animated], raised: [...raised] });
            };
            tick();
          }),
        ms
      );

    // Reduced motion: no animation.
    let flips = watchFlips(2_000);
    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Duplicate card" }).click();
    expect(await flips).toEqual({ animated: [], raised: [] });
    await expect.poll(async () => (await serverCardsNamed(page, `${name} (copy)`)).length, { timeout: 10_000 }).toBe(1);
    const firstCopyId: string = (await serverCardsNamed(page, `${name} (copy)`))[0].id;
    cleanupCards.push(firstCopyId);

    // Motion allowed: the second copy floats out of the source.
    await page.emulateMedia({ reducedMotion: "no-preference" });
    flips = watchFlips(2_000);
    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Duplicate card" }).click();
    const seen = await flips;
    await expect.poll(async () => (await serverCardsNamed(page, `${name} (copy)`)).length, { timeout: 10_000 }).toBe(2);
    const copies = await serverCardsNamed(page, `${name} (copy)`);
    const newest = copies.find((c: any) => c.id !== firstCopyId)!;
    cleanupCards.push(newest.id);
    expect(seen.raised).toEqual([newest.id]);
    // The source keeps its slot; the new copy takes the one after it, so the
    // first copy is pushed one slot on and slides there.
    expect(seen.animated).toContain(firstCopyId);
    expect(seen.animated).not.toContain(cl.id);

    // Afterwards the board is at rest: no flip left running, nothing raised.
    const rest = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>("li[data-card-id]")].filter(
        (el) => el.getAnimations().some((a) => a.id === "card-flip") || el.style.zIndex !== ""
      ).length
    );
    expect(rest).toBe(0);
  });

  test("duplicate and move work offline and converge after reconnect", async ({ page }) => {
    const tag = Date.now();
    const name = `CopyOffline-${tag}`;
    const { cl, ids, label } = await seedCard(page, name, tag);
    cleanupCards.push(cl.id);
    cleanupLabels.push(label.id);

    await page.goto("/?localFirst=1");
    await page.waitForSelector("[data-testid=checklist-board]");
    const dialog = await openCard(page, name);
    await expect(dialog.locator("[data-testid=item-row]")).toHaveCount(3, { timeout: 5_000 });

    // ── Offline: duplicate, then move the checked items. ──────────────────────
    await page.route("**/api/**", (route) => route.abort());
    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Duplicate card" }).click();
    await openKebab(dialog);
    await page.getByRole("menuitem", { name: "Move checked items to new card" }).click();
    await page.locator("[data-testid=confirm-move-checked]").click();
    await expect(dialog.locator("[data-testid=item-row]")).toHaveCount(1, { timeout: 5_000 });
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(dialog).toBeHidden({ timeout: 5_000 });

    // Both new cards show at once, and the two ops wait in the outbox.
    await expect(boardCard(page, `${name} (copy)`)).toHaveCount(1, { timeout: 5_000 });
    await expect(boardCard(page, `${name} (done)`)).toHaveCount(1, { timeout: 5_000 });
    await expect.poll(() => outboxOpCount(page), { timeout: 8_000 }).toBe(2);

    // ── Reconnect: the outbox drains, the server converges. ───────────────────
    await page.unroute("**/api/**");
    await page.reload();
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect.poll(() => outboxOpCount(page), { timeout: 15_000 }).toBe(0);

    await expect.poll(async () => (await serverCardsNamed(page, `${name} (done)`)).length, { timeout: 10_000 }).toBe(1);
    const [copy] = await serverCardsNamed(page, `${name} (copy)`);
    const [done] = await serverCardsNamed(page, `${name} (done)`);
    cleanupCards.push(copy.id, done.id);
    // The duplicate ran first, so it has all three items, unchecked.
    expect((await serverItems(page, copy.id)).map((i: any) => [i.text, i.state.checked])).toEqual([
      [`one-${tag}`, false],
      [`two-${tag}`, false],
      [`three-${tag}`, false],
    ]);
    expect((await serverItems(page, done.id)).map((i: any) => [i.text, i.state.checked])).toEqual([
      [`one-${tag}`, true],
      [`three-${tag}`, true],
    ]);
    expect((await serverItems(page, cl.id)).map((i: any) => i.id)).toEqual([ids[1]]);

    // The board shows each new card once, with the server's items (no doubles).
    await expect(boardCard(page, `${name} (copy)`)).toHaveCount(1);
    await expect(boardCard(page, `${name} (done)`)).toHaveCount(1);
    const doneDialog = await openCard(page, `${name} (done)`);
    await expect(doneDialog.locator("[data-testid=item-row]")).toHaveCount(2, { timeout: 5_000 });
  });
});
