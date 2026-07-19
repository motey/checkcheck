import { defineConfig, devices } from "@playwright/test";

// Docs-screenshot generation. NOT a test suite: every "test" here writes a PNG
// into docs/screenshots/. Run it via ../../gen_screenshots.sh, which owns the
// Postgres container, the seeding and the backend process — this config
// deliberately has no globalSetup/webServer so it can never boot a half-seeded
// server on its own.
const SCREENSHOT_BACKEND_PORT = 8183;

// Viewports are chosen to reproduce the existing committed images pixel-for-
// pixel (all DPR 1): the desktop board is 1992x1353 and shows six card columns;
// mobile is 412x915, the Pixel 7 CSS viewport. Changing these reflows every
// screenshot in the docs, so treat them as fixed.
export const DESKTOP_VIEWPORT = { width: 1992, height: 1353 };
export const MOBILE_VIEWPORT = { width: 412, height: 915 };

export default defineConfig({
  testDir: "./tests/screenshots",
  timeout: 60_000,
  // Shots share one backend and one seeded dataset; ordering matters and a
  // retried shot could capture leftover UI state, so neither parallelism nor
  // retries are wanted here.
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [["list"]],

  use: {
    baseURL: `http://localhost:${SCREENSHOT_BACKEND_PORT}`,
    // Kills the card hover-lift and FormKit drag reflow animations, so a shot
    // taken mid-transition is impossible.
    reducedMotion: "reduce",
    trace: "off",
    video: "off",
  },

  projects: [
    {
      name: "auth-setup",
      testMatch: /auth\.setup\.ts/,
    },
    {
      name: "desktop",
      testMatch: /desktop-.*\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        viewport: DESKTOP_VIEWPORT,
        deviceScaleFactor: 1,
        storageState: "tests/screenshots/.auth/state.json",
      },
      dependencies: ["auth-setup"],
    },
    {
      name: "mobile",
      testMatch: /mobile-.*\.spec\.ts/,
      use: {
        ...devices["Pixel 7"],
        viewport: MOBILE_VIEWPORT,
        // Pixel 7 defaults to DPR ~2.6; the committed mobile PNGs are 412x915,
        // i.e. DPR 1. Override so the output size stays stable.
        deviceScaleFactor: 1,
        storageState: "tests/screenshots/.auth/state.json",
      },
      dependencies: ["auth-setup"],
    },
    {
      // Composites already-written PNGs (desktopDarkLightMix). Must run last;
      // depending on both shot projects enforces that.
      name: "compose",
      testMatch: /compose-.*\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
      dependencies: ["desktop", "mobile"],
    },
  ],
});
