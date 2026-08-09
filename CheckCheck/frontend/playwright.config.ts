import { defineConfig, devices } from "@playwright/test";

// The E2E backend serves BOTH the API and the static frontend on this port.
// No separate Nuxt dev server is needed — `nuxt generate` builds a static
// bundle that the backend serves from CheckCheck/frontend/.output/public/.
const E2E_BACKEND_PORT = 8182;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  // Every spec drives the SAME account against the SAME database, so any two
  // specs running at once corrupt each other's board (card counts, board order,
  // archive state). `fullyParallel: false` alone does NOT prevent that: it only
  // serialises tests *within* a file, while files still run on separate workers.
  // One worker is the only setting that actually makes assertions on absolute
  // board state valid. See docs/plans/E2E_STABILITY.md (S5) for the per-worker
  // account work that will buy the parallelism back.
  fullyParallel: false,
  workers: 1,
  retries: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],

  use: {
    baseURL: `http://localhost:${E2E_BACKEND_PORT}`,
    // Recording a trace for every test costs a noticeable slice of the run.
    // Keep them only where they are read: failures and retried tests. Pass
    // `--trace on` on the command line when debugging a single spec.
    trace: "retain-on-failure",
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
    //    Desktop Chrome; excludes the touch-only specs (those need hasTouch).
    {
      name: "chromium",
      testIgnore: /touch-.*\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        storageState: "tests/e2e/.auth/state.json",
      },
      dependencies: ["auth-setup"],
    },
    // 3. Touch/mobile specs – a real touch-capable context (hasTouch + mobile
    //    viewport/UA) so tap-to-open and the @formkit/drag-and-drop longPress
    //    synthetic-drag path (touch pointerType) can be exercised. Scoped to
    //    touch-*.spec.ts only; the desktop project ignores those.
    {
      name: "mobile",
      testMatch: /touch-.*\.spec\.ts/,
      use: {
        ...devices["Pixel 7"],
        storageState: "tests/e2e/.auth/state.json",
      },
      dependencies: ["auth-setup"],
    },
  ],

  // Starts/stops the dedicated E2E backend server (which also serves the frontend).
  globalSetup: "./tests/e2e/global-setup.ts",
  globalTeardown: "./tests/e2e/global-teardown.ts",
});
