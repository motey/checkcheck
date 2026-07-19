/**
 * The card editor dialog on a phone viewport, light and dark.
 *
 * Writes: mobileLightEditor.png, mobileDarkEditor.png
 *
 * These filenames previously held desktop-width shots, which made the "On your
 * phone" section of docs/screenshots.md misleading. They are now genuinely
 * mobile; the desktop framing moved to DesktopLightEditor / DesktopDarkEditor.
 */
import { test } from "@playwright/test";
import { openBoard, openFullestCardEditor, shootPage, closeSse } from "./helpers";

test.afterEach(async ({ page }) => {
  await closeSse(page);
});

test("mobileLightEditor", async ({ page }) => {
  await openBoard(page, "light");
  await openFullestCardEditor(page);
  await shootPage(page, "mobileLightEditor");
});

test("mobileDarkEditor", async ({ page }) => {
  await openBoard(page, "dark");
  await openFullestCardEditor(page);
  await shootPage(page, "mobileDarkEditor");
});
