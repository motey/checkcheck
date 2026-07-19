/**
 * The card editor dialog on desktop, light and dark.
 *
 * Writes: DesktopLightEditor.png, DesktopDarkEditor.png
 *
 * The mobile counterparts live in mobile-editor.spec.ts. Shot on a shorter
 * viewport than the board specs: the modal is the subject, so the frame is
 * cropped to roughly the dialog's height rather than the whole board.
 */
import { test } from "@playwright/test";
import { openBoard, openFullestCardEditor, shootPage, closeSse } from "./helpers";

const EDITOR_VIEWPORT = { width: 1992, height: 1020 };

test.use({ viewport: EDITOR_VIEWPORT });

test.afterEach(async ({ page }) => {
  await closeSse(page);
});

test("DesktopLightEditor", async ({ page }) => {
  await openBoard(page, "light");
  await openFullestCardEditor(page);
  await shootPage(page, "DesktopLightEditor");
});

test("DesktopDarkEditor", async ({ page }) => {
  await openBoard(page, "dark");
  await openFullestCardEditor(page);
  await shootPage(page, "DesktopDarkEditor");
});
