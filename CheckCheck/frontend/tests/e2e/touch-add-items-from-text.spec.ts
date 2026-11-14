/**
 * "Add items from text" on a phone (mobile project, Pixel 7): the dialog fills
 * the screen and keeps its buttons above the on-screen keyboard. The keyboard is
 * simulated by shrinking the viewport, which is what
 * `interactive-widget=resizes-content` makes Android Chrome do (iOS Safari goes
 * through useVisualViewport and needs an on-device check).
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

test.describe("touch add items from text", () => {
  const cleanup: string[] = [];
  test.afterEach(async ({ page }) => {
    for (const id of cleanup) await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanup.length = 0;
    await page.goto("about:blank").catch(() => {});
  });

  test("the dialog fills the screen and its buttons stay above the keyboard", async ({ page }) => {
    const name = `TouchAddText-${Date.now()}`;
    const cl = await apiPost(page, "/api/checklist", { name });
    cleanup.push(cl.id);

    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");
    await page.locator("[data-testid=card-title]", { hasText: name }).first().tap();
    const editor = page.locator('[role="dialog"]:has(.checklist)');
    await expect(editor).toBeVisible({ timeout: 5_000 });
    await editor.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));

    await editor.locator('button:has([class*="ellipsis-vertical"])').first().tap();
    await page.getByRole("menuitem", { name: "Add items from text", exact: true }).tap();
    const form = page.getByTestId("add-items-from-text");
    await expect(form).toBeVisible();
    const modal = page.locator('[role="dialog"]:has([data-testid=add-items-from-text])');
    await modal.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));

    const viewport = page.viewportSize()!;
    const box = (await modal.boundingBox())!;
    expect(Math.round(box.width)).toBe(viewport.width);
    expect(Math.round(box.height)).toBe(viewport.height);

    // Simulated keyboard: half the height is left. Type a long list; the
    // textarea scrolls, the confirm button stays reachable.
    await page.setViewportSize({ width: viewport.width, height: Math.round(viewport.height / 2) });
    const textarea = page.getByTestId("add-items-text");
    await textarea.fill(Array.from({ length: 30 }, (_, i) => `- item ${i + 1}`).join("\n"));
    const confirm = page.getByTestId("confirm-add-items");
    await expect(confirm).toBeInViewport();
    await expect(page.getByRole("button", { name: "Close" }).last()).toBeInViewport();

    await confirm.tap();
    await expect(form).toHaveCount(0, { timeout: 5_000 });
    await expect
      .poll(
        async () => (await (await page.request.get(`/api/checklist/${cl.id}/item?limit=999999`)).json()).items.length,
        { timeout: 10_000 }
      )
      .toBe(30);
  });
});
