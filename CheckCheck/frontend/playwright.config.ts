import { defineConfig, devices } from "@playwright/test";

// Backend port dedicated to E2E tests (dev=8181, unit-tests=8888, e2e=8182)
const E2E_BACKEND_PORT = 8182;
// Frontend port dedicated to E2E tests (dev=3000, e2e=3001)
const FRONTEND_PORT = 3001;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  // Tests share backend state (checklists created in one test affect others),
  // so run sequentially to keep assertions predictable.
  fullyParallel: false,
  // Allow one retry to recover from transient Vite HMR reloads that can
  // interrupt a test on the first run (page reloads to same URL, cancelling
  // the navigation the test was waiting for).  By the second attempt the
  // Vite optimization cycle is complete and the server is stable.
  retries: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],

  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
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

  // Starts/stops the dedicated E2E backend server.
  globalSetup: "./tests/e2e/global-setup.ts",
  globalTeardown: "./tests/e2e/global-teardown.ts",

  // Starts the Nuxt dev server, pointing its /api proxy at the E2E backend.
  // The Vite disk cache (node_modules/.cache/vite) becomes stale when nuxt.config.ts
  // changes, causing Vue to silently fail to mount (blank page).  Clearing it before
  // each run forces a fresh optimisation (~5 s extra) and guarantees a clean state.
  webServer: {
    command: `rm -rf node_modules/.cache/vite && API_PROXY_TARGET=http://localhost:${E2E_BACKEND_PORT}/api PORT=${FRONTEND_PORT} bun --bun run dev`,
    port: FRONTEND_PORT,
    // Never reuse an existing server – the API_PROXY_TARGET must match the E2E backend.
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
