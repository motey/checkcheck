/**
 * The split light/dark hero image used at the top of README.md.
 *
 * Writes: desktopDarkLightMix.png
 *
 * This is a composite, not a screenshot of the app: the dark board with the
 * light board laid over it, clipped to a diagonal. Rather than pull in sharp or
 * shell out to ImageMagick, the two PNGs are inlined into a blank page as data
 * URIs, stacked, and clipped with CSS — then that page is screenshotted. Zero
 * new dependencies, and the diagonal is defined in one readable place.
 *
 * Depends on desktopLight.png / desktopDark.png already existing, which the
 * "compose" project's dependency on the desktop project guarantees.
 */
import { test, expect } from "@playwright/test";
import { readFile } from "fs/promises";
import { resolve } from "path";
import { SHOTS_DIR, shootPage } from "./helpers";

// A single corner-to-corner diagonal: the light board fills the upper-left
// triangle, the dark board shows through the lower-right. Deliberately a clean
// 50/50 split — earlier variants clipped small corner wedges instead, which
// read as a rendering accident rather than a designed theme showcase.
const CLIP_POLYGON = "polygon(0% 0%, 100% 0%, 0% 100%)";

async function dataUri(name: string): Promise<string> {
  const buf = await readFile(resolve(SHOTS_DIR, `${name}.png`));
  return `data:image/png;base64,${buf.toString("base64")}`;
}

test("desktopDarkLightMix", async ({ page }) => {
  const [light, dark] = await Promise.all([dataUri("desktopLight"), dataUri("desktopDark")]);

  // Read the real dimensions off the decoded image rather than hardcoding them,
  // so changing the desktop viewport does not silently letterbox this composite.
  await page.setContent(`
    <style>
      html, body { margin: 0; padding: 0; background: #000; }
      #stack { position: relative; line-height: 0; }
      #stack img { display: block; width: 100%; height: auto; }
      #top { position: absolute; inset: 0; clip-path: ${CLIP_POLYGON}; }
    </style>
    <div id="stack">
      <img id="base" src="${dark}">
      <img id="top" src="${light}">
    </div>
  `);

  const base = page.locator("#base");
  await expect(base).toBeVisible();

  const size = await base.evaluate((img: HTMLImageElement) => {
    return img.decode().then(() => ({
      width: img.naturalWidth,
      height: img.naturalHeight,
    }));
  });

  await page.setViewportSize(size);
  // Both layers must be decoded before the shot, or the overlay renders blank.
  await page.locator("#top").evaluate((img: HTMLImageElement) => img.decode());
  await page.waitForTimeout(200);

  await shootPage(page, "desktopDarkLightMix");
});
