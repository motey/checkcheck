/**
 * Archive / permanent-delete flow (Chunk 4).
 *
 * The trash action is a two-stage flow:
 *   - Home / normal view: the trash button soft-archives the card (it leaves the
 *     board) and shows an "Undo" toast that restores it.
 *   - Archive view (?archived=true, reached via the sidebar): the card's trash
 *     button becomes a permanent delete behind a confirm dialog, and a Restore
 *     button pulls it back onto the home board.
 *
 * The toolbar we drive lives in the open card editor (always visible there),
 * avoiding the board preview's hover-revealed / touch-hidden toolbar.
 *
 * Runs in the "chromium" project with the pre-loaded admin session. See memory
 * `flaky-e2e-dnd-sharing`: re-run before blaming flakiness.
 */
import { test, expect, type Page } from "@playwright/test";

// Close the board's SSE (/api/sync) before Playwright tears down each page.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

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

// The editor is identified by its content — only the editor renders the
// `.checklist` container inside a dialog (see card-editor.spec.ts).
const editorDialog = (page: Page) => page.locator('[role="dialog"]:has(.checklist)');

// Open a card's editor by clicking its board preview title. Returns the editor
// dialog locator so toolbar-button clicks are scoped to the editor (the board
// preview renders the same toolbar testids behind the modal backdrop).
async function openCard(page: Page, name: string) {
  await page.locator("[data-testid=card-title]", { hasText: name }).first().click();
  await expect(page).toHaveURL(/\/card\//, { timeout: 5_000 });
  const dialog = editorDialog(page);
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  return dialog;
}

test.describe("archive & permanent delete", () => {
  test.setTimeout(30_000);

  const cleanupChecklists: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanupChecklists) await apiDelete(page, `/api/checklist/${id}`);
    cleanupChecklists.length = 0;
  });

  test("archive from home leaves the board and the undo toast restores it", async ({ page }) => {
    const tag = Date.now();
    const name = `Archive-${tag}`;
    const cl = await apiPost(page, "/api/checklist", { name });
    cleanupChecklists.push(cl.id);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(page.getByText(name, { exact: true })).toBeVisible();

    // Archive via the editor toolbar.
    const dialog = await openCard(page, name);
    await dialog.locator("[data-testid=card-archive]").click();

    // Editor closes, card leaves the home board, undo toast appears.
    await expect(page).not.toHaveURL(/\/card\//, { timeout: 5_000 });
    await expect(page.getByText(name, { exact: true })).not.toBeVisible({ timeout: 5_000 });

    // Undo restores it to the home board.
    await page.locator("[data-testid=undo-archive]").click();
    await expect(page.getByText(name, { exact: true })).toBeVisible({ timeout: 5_000 });
  });

  test("archive view lists archived cards; restore returns them home", async ({ page }) => {
    const tag = Date.now();
    const name = `Restore-${tag}`;
    const cl = await apiPost(page, "/api/checklist", { name });
    cleanupChecklists.push(cl.id);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(page.getByText(name, { exact: true })).toBeVisible();

    // Archive it.
    const archiveDialog = await openCard(page, name);
    await archiveDialog.locator("[data-testid=card-archive]").click();
    await expect(page.getByText(name, { exact: true })).not.toBeVisible({ timeout: 5_000 });

    // Open the Archive view via the sidebar — the card is there.
    await page.locator("[data-testid=sidebar-archive-filter]").click();
    await expect(page).toHaveURL(/archived=true/, { timeout: 3_000 });
    await expect(page.getByText(name, { exact: true })).toBeVisible({ timeout: 5_000 });

    // A second click on the active Archive entry leaves the view again
    // (issue #10), then re-enter it for the rest of the flow.
    await page.locator("[data-testid=sidebar-archive-filter]").click();
    await expect(page).not.toHaveURL(/archived=true/, { timeout: 3_000 });
    await page.locator("[data-testid=sidebar-archive-filter]").click();
    await expect(page).toHaveURL(/archived=true/, { timeout: 3_000 });
    await expect(page.getByText(name, { exact: true })).toBeVisible({ timeout: 5_000 });

    // Restore from the archived card's editor toolbar.
    const restoreDialog = await openCard(page, name);
    await restoreDialog.locator("[data-testid=card-restore]").click();
    // It drops out of the Archive view.
    await expect(page.getByText(name, { exact: true })).not.toBeVisible({ timeout: 5_000 });

    // Back on the home board.
    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(page.getByText(name, { exact: true })).toBeVisible({ timeout: 5_000 });
  });

  test("delete forever from the archive view removes the card and its backend row", async ({ page }) => {
    const tag = Date.now();
    const name = `DeleteForever-${tag}`;
    const cl = await apiPost(page, "/api/checklist", { name });
    cleanupChecklists.push(cl.id);

    // Archive via API to set up, then drive the delete in the UI.
    await page.request.patch(`/api/checklist/${cl.id}/position`, {
      data: { archived: true },
      headers: { "Content-Type": "application/json" },
    });

    await page.goto("/?archived=true");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(page.getByText(name, { exact: true })).toBeVisible({ timeout: 5_000 });

    // Trash button in the archive view = permanent delete behind a confirm.
    const dialog = await openCard(page, name);
    await dialog.locator("[data-testid=card-delete-forever]").click();
    // The board drops the card optimistically, before the DELETE has reached the
    // server, so the backend check below must wait for the app's own request.
    // Without this the GET can be served while the DELETE is still in flight
    // and read the row as 200.
    const deleted = page.waitForResponse(
      (r) => r.request().method() === "DELETE" && r.url().endsWith(`/api/checklist/${cl.id}`),
    );
    await page.locator("[data-testid=confirm-delete]").click();

    // Gone from the archive board.
    await expect(page.getByText(name, { exact: true })).not.toBeVisible({ timeout: 5_000 });
    expect((await deleted).status()).toBe(204);

    // Gone from the backend. Since WI-2 a delete is a tombstone, so the row
    // reads as 410 Gone (terminal for the sync outbox), not 404 (never existed).
    const res = await page.request.get(`/api/checklist/${cl.id}`);
    expect(res.status()).toBe(410);
  });

  test("animates a card leaving the board on the user's own action only", async ({ page }) => {
    // The suite emulates prefers-reduced-motion (playwright.config.ts), so the
    // first archive is instant; with motion allowed, the archived card fades out
    // as a ghost and the card after it slides into the gap. A card removed from
    // elsewhere (here: the API, like another device) just goes.
    const tag = Date.now();
    // New cards go on top, so creating B, Y, X, A gives the board order A, X, Y, B.
    const ids: Record<string, string> = {};
    for (const key of ["B", "Y", "X", "A"]) {
      ids[key] = (await apiPost(page, "/api/checklist", { name: `Leave-${key}-${tag}` })).id;
      cleanupChecklists.push(ids[key]!);
    }

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await expect(page.getByText(`Leave-B-${tag}`, { exact: true })).toBeVisible();

    /** Every animation frame for `ms`: which cards run the flip, and whether a leave ghost played. */
    const watchMotion = (ms: number) =>
      page.evaluate(
        (ms) =>
          new Promise<{ animated: string[]; ghosts: number }>((resolve) => {
            const animated = new Set<string>();
            let ghosts = 0;
            const end = performance.now() + ms;
            const tick = () => {
              for (const el of document.querySelectorAll<HTMLElement>("li[data-card-id]")) {
                if (el.getAnimations().some((a) => a.id === "card-flip")) animated.add(el.dataset.cardId!);
              }
              ghosts = Math.max(ghosts, document.querySelectorAll("[data-card-leaving]").length);
              if (performance.now() < end) requestAnimationFrame(tick);
              else resolve({ animated: [...animated], ghosts });
            };
            tick();
          }),
        ms
      );

    const archive = async (key: string) => {
      const dialog = await openCard(page, `Leave-${key}-${tag}`);
      await dialog.locator("[data-testid=card-archive]").click();
      await expect(page.getByText(`Leave-${key}-${tag}`, { exact: true })).not.toBeVisible({ timeout: 5_000 });
    };

    // Reduced motion: instant.
    let seen = watchMotion(1_500);
    await archive("X");
    expect(await seen).toEqual({ animated: [], ghosts: 0 });

    // Motion allowed: Y fades out, B slides into its slot.
    await page.emulateMedia({ reducedMotion: "no-preference" });
    seen = watchMotion(1_500);
    await archive("Y");
    const played = await seen;
    expect(played.ghosts).toBe(1);
    expect(played.animated).toContain(ids.B);

    // Removed elsewhere: no animation.
    seen = watchMotion(3_000);
    await apiDelete(page, `/api/checklist/${ids.A}`);
    await expect(page.getByText(`Leave-A-${tag}`, { exact: true })).not.toBeVisible({ timeout: 5_000 });
    expect(await seen).toEqual({ animated: [], ghosts: 0 });

    // At rest: no ghost left behind, no flip running.
    expect(await page.locator("[data-card-leaving]").count()).toBe(0);
    const running = await page.evaluate(
      () =>
        [...document.querySelectorAll<HTMLElement>("li[data-card-id]")].filter((el) =>
          el.getAnimations().some((a) => a.id === "card-flip")
        ).length
    );
    expect(running).toBe(0);
  });
});
