import { defineConfig, devices } from "@playwright/test";

// The E2E backend serves BOTH the API and the static frontend on this port.
// No separate Nuxt dev server is needed — `nuxt generate` builds a static
// bundle that the backend serves from CheckCheck/frontend/.output/public/.
const E2E_BACKEND_PORT = 8182;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  // Tests share backend state; run sequentially to keep assertions predictable.
  fullyParallel: false,
  retries: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],

  use: {
    baseURL: `http://localhost:${E2E_BACKEND_PORT}`,
    trace: "on",
    screenshot: "only-on-failure",
    // Emulate prefers-reduced-motion so the Phase 6 reduced-motion CSS disables
    // the card hover-lift and FormKit drag reflow animations during tests. This
    // removes animation-timing races from the drag specs (card/item movement,
    // pin, reorder) without changing drop behaviour (drop index is geometry-based
    // on drop, not animation-based).
    reducedMotion: "reduce",
  },

  projects: [
    // 1. Log in once and persist auth state for the rest of the suite.
    {
      name: "auth-setup",
      testMatch: /auth\.setup\.ts/,
    },
    // 2. Feature tests – run with a pre-authenticated browser.
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        storageState: "tests/e2e/.auth/state.json",
      },
      dependencies: ["auth-setup"],
    },
  ],

  // Starts/stops the dedicated E2E backend server (which also serves the frontend).
  globalSetup: "./tests/e2e/global-setup.ts",
  globalTeardown: "./tests/e2e/global-teardown.ts",
});
