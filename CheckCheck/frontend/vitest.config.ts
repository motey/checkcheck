import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

// Unit-test runner for the framework-free local-first core (WI-7 outbox engine,
// coalescing, error classification, the IndexedDB outbox store). The engine is
// deliberately Nuxt/Vue-free so it runs here without the full Nuxt test harness;
// the IndexedDB store runs against `fake-indexeddb` (see tests/unit/setup.ts).
//
// The Playwright E2E suite (tests/e2e, a separate `test:e2e` script) still owns
// full-app / round-trip coverage.
export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/unit/**/*.spec.ts"],
    setupFiles: ["tests/unit/setup.ts"],
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
      "~": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
});
