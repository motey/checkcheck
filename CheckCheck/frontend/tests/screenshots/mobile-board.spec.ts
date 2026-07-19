/**
 * The board, light and dark, on a phone viewport.
 *
 * Writes: mobileLight.png, mobileDark.png
 *
 * Note the mobile sidebar is a drawer, so these shots show the single-column
 * card list plus the mobile navbar — that is what the committed images show.
 */
import { test } from "@playwright/test";
import { openBoard, shootPage, closeSse } from "./helpers";

test.afterEach(async ({ page }) => {
  await closeSse(page);
});

test("mobileLight", async ({ page }) => {
  await openBoard(page, "light");
  await shootPage(page, "mobileLight");
});

test("mobileDark", async ({ page }) => {
  await openBoard(page, "dark");
  await shootPage(page, "mobileDark");
});
