/**
 * The board, light and dark, on desktop.
 *
 * Writes: desktopLight.png, desktopDark.png
 * These two are also the inputs to the diagonal composite in
 * compose-mix.spec.ts, so they must run before it (the "compose" project
 * declares the dependency).
 */
import { test } from "@playwright/test";
import { openBoard, shootPage, closeSse } from "./helpers";

test.afterEach(async ({ page }) => {
  await closeSse(page);
});

test("desktopLight", async ({ page }) => {
  await openBoard(page, "light");
  await shootPage(page, "desktopLight");
});

test("desktopDark", async ({ page }) => {
  await openBoard(page, "dark");
  await shootPage(page, "desktopDark");
});
