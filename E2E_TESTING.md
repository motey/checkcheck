# End-to-End Testing

CheckCheck uses [Playwright](https://playwright.dev) for end-to-end tests.
The suite spins up a real backend (isolated SQLite database, port 8182) and a
real Nuxt dev server (port 3001) so tests exercise the full stack with no mocks.

---

## Quick start

```bash
# From the repo root
./run_e2e_tests.sh

# Or directly from the frontend directory
cd CheckCheck/frontend
bunx playwright test
```

First run takes ~30 s (Vite re-optimises its dependency cache).
Subsequent runs take ~25 s.

### Other useful commands

```bash
# Interactive Playwright UI (great for debugging)
bunx playwright test --ui

# Headed browser (watch tests run in a real Chrome window)
bunx playwright test --headed

# Single spec file
bunx playwright test tests/e2e/auth.spec.ts

# Single test by name
bunx playwright test --grep "redirects to / after"

# Open the last HTML report
bunx playwright test:e2e:report
```

> **Always run from `CheckCheck/frontend/`** (or use `run_e2e_tests.sh` from the
> repo root, which does the `cd` for you). Running from the repo root without
> the wrapper causes Playwright to look for its config in the wrong directory.

---

## How it works

### Ports

| Service | Dev | Unit tests | E2E tests |
|---------|-----|------------|-----------|
| Backend | 8181 | 8888 | **8182** |
| Frontend | 3000 | — | **3001** |

Dedicated ports mean E2E runs never collide with an already-running dev server.

### Startup sequence

`global-setup.ts` runs before any test worker:

1. **Kill stale processes** — `fuser -k 8182/tcp` clears orphans from previous
   runs before attempting to bind the port.
2. **Start the E2E backend** — spawns
   `CheckCheck/backend/tests/start_e2e_server.py` using the project's own
   Python venv. The script deletes the previous test SQLite database, creates a
   fresh one, provisions test users, and prints `READY` to stdout when the
   health check passes.
3. **Warm up the Nuxt dev server** — the webServer command clears Vite's disk
   cache (`node_modules/.cache/vite`) before starting Nuxt. Without this,
   stale cached module transforms silently prevent Vue from mounting (blank
   page). After Vite starts, a headless browser navigates to `/login` and waits
   for the form to render — this forces Vite's ~5 s dependency optimisation to
   complete before any real test runs.

`global-teardown.ts` sends `SIGTERM` to the backend process **group** (negative
PID), which also terminates the forked uvicorn child. Without the group kill,
uvicorn would keep holding port 8182 and the next run would fail with
`address already in use`.

### Authentication strategy

`auth.setup.ts` logs in once as `admin3` and saves the browser storage state
(session cookie) to `tests/e2e/.auth/state.json`. Every test in the `chromium`
project then starts with that cookie already in the browser context — no login
step needed per test, and the board loads immediately.

Tests that need to start **unauthenticated** (login page tests, for example)
override this at the describe level:

```ts
test.use({ storageState: { cookies: [], origins: [] } });
```

---

## Test credentials

| Username | Password | Role |
|----------|----------|------|
| `admin3` | `password123` | admin |
| `testuser01` | `testuserpw_secure1` | regular user |

These credentials are provisioned at backend startup from
`CheckCheck/backend/tests/provisioning_data/test_users.yaml`. The admin user is
created from env vars set in `start_e2e_server.py`.

---

## File map

```
CheckCheck/frontend/
├── playwright.config.ts              # Main Playwright config
├── tests/e2e/
│   ├── tsconfig.json                 # Node types for global-setup/teardown
│   ├── global-setup.ts              # Start backend + warm up Nuxt
│   ├── global-teardown.ts           # Stop backend
│   ├── auth.setup.ts                # Log in once, save session to .auth/
│   ├── auth.spec.ts                 # Login page, error handling, logout
│   └── checklist.spec.ts            # Board, new checklist, search, modal
│
CheckCheck/backend/tests/
└── start_e2e_server.py              # Starts backend on port 8182 with fresh DB
```

Generated at runtime (gitignored):

```
tests/e2e/.auth/state.json           # Saved browser session (auth cookies)
tests/e2e/.e2e-server.pid            # Backend PID for teardown
```

---

## Adding new tests

### Authenticated test (most common)

Create a new file in `tests/e2e/`. It automatically runs inside the `chromium`
project which pre-loads the admin session:

```ts
// tests/e2e/label.spec.ts
import { test, expect } from "@playwright/test";

test.describe("label management", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("sidebar shows labels", async ({ page }) => {
    await expect(page.getByText("Edit Labels")).toBeVisible();
  });
});
```

### Unauthenticated test

```ts
test.describe("public page", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("login page is reachable", async ({ page }) => {
    await page.goto("/login");
    await page.waitForSelector("form");
    // ...
  });
});
```

### Test as the non-admin user

The test user credentials are available in `auth.setup.ts` but there is no
pre-saved storage state for `testuser01`. The simplest approach is to log in
inside the test:

```ts
import { ADMIN, TEST_USER } from "./auth.setup";

test("regular user cannot see admin panel", async ({ page }) => {
  // Clear the admin session and log in as testuser01
  await page.context().clearCookies();
  await page.goto("/login");
  await page.waitForSelector("form");
  await page.locator("[data-testid=login-username]").fill(TEST_USER.username);
  await page.locator("[data-testid=login-password]").fill(TEST_USER.password);
  await page.locator('form button[type="submit"]').click();
  await page.waitForURL("/");
  // ... assertions
});
```

---

## Selectors — what to use

### Prefer `data-testid` for form elements

Nuxt UI components (`UInput`, `UButton`, etc.) wrap native elements in
extra divs. Playwright's semantic locators (`getByLabel`, `getByPlaceholder`)
sometimes miss the inner `<input>`. The login form already has:

```
data-testid="login-username"   → the username <input>
data-testid="login-password"   → the password <input>
data-testid="login-error"      → the UAlert error banner
```

When writing new tests, add `data-testid` to any element that doesn't have a
reliable text/role selector.

### Nuxt UI 4 quirks

- **`UAlert`** does not render with `role="alert"`. Use `data-testid` or target
  the text content.
- **`UButton`** renders as a `<button>` — `getByRole("button", { name: "..." })`
  works fine.
- **`UModal`** renders with `role="dialog"` — `page.locator('[role="dialog"]')`
  works.
- **`getByRole("heading", { name: "Login" })`** — always pass `exact: true` if
  there are multiple headings whose text contains the search string.

### The checklist board

```
data-testid="checklist-board"  → the <ul> grid of checklist cards
```

---

## Configuration reference

Key settings in [`playwright.config.ts`](CheckCheck/frontend/playwright.config.ts):

| Setting | Value | Reason |
|---------|-------|--------|
| `fullyParallel` | `false` | Tests share backend state; running in parallel would cause race conditions |
| `retries` | `1` | Absorbs one-off flakiness from Vite HMR mid-test |
| `timeout` | `30 000 ms` | Per-test timeout |
| `webServer.reuseExistingServer` | `false` | The API proxy must point to port 8182; re-using a dev server on 8181 would silently test against the wrong backend |
| `webServer.command` | `rm -rf node_modules/.cache/vite && ...` | Clears stale Vite cache before every run to prevent blank-page failures |

---

## Troubleshooting

### `address already in use` on port 8182 or 3001

A previous run left an orphaned process. Clean up and re-run:

```bash
fuser -k 8182/tcp 2>/dev/null
fuser -k 3001/tcp 2>/dev/null
sleep 1
bunx playwright test
```

### Blank white page in test screenshots

The Vite optimisation cycle did not finish before the test browser connected.
This should not happen in normal runs because the warmup step waits for the
login form to render. If it recurs, check that `global-setup.ts` is still
calling `page.waitForSelector("form")` in the warmup loop.

### Backend Python venv not found

```
Error: Backend venv not found at .../CheckCheck/backend/.venv/bin/python
```

The backend dev environment has not been set up:

```bash
source build_server_dev_env.sh
```

### `test.use()` outside a `test.describe()` is not allowed

You're importing `test` from `@playwright/test` at the top level of a spec
file that is also picked up by the configuration. Move the `test.use()` call
inside a `test.describe()` block, or check that the file extension is `.spec.ts`
(not `.ts` alone, which could be picked up as a config file).

---

## For coding agents

The setup is designed so that an agent can run tests, read the output, look at
failure screenshots, and iterate without any human in the loop:

- **Failure screenshots** land in `test-results/` — readable via the `Read`
  tool (they are PNG images).
- **Playwright traces** (network log + console log + DOM snapshots) land in
  `test-results/*/trace.zip` on retry. Extract with
  `bunx playwright show-trace <path>` or unzip and parse the NDJSON files.
- **HTML report** is generated at `playwright-report/index.html` after every
  run. Open with `bunx playwright test:e2e:report`.
- **`--headed` mode** lets the agent launch a visible browser when something is
  hard to diagnose from screenshots alone.

If the [`@playwright/mcp`](https://github.com/microsoft/playwright-mcp) server
is configured, the agent can also drive the running app directly (navigate,
click, fill, read DOM) without writing a test file first — useful for
exploratory debugging.
