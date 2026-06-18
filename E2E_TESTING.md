# End-to-End Testing

> **Writing new tests?**  Read
> [`CheckCheck/frontend/tests/e2e/LLM_GUIDE.md`](CheckCheck/frontend/tests/e2e/LLM_GUIDE.md)
> first — it is a single-file reference covering selectors, API patterns,
> sorting rules, DnD specifics, SSE sync, and copy-paste boilerplate.

CheckCheck uses [Playwright](https://playwright.dev) for end-to-end tests.
The suite builds the frontend into a static bundle, then spins up the real
backend (isolated SQLite database, port 8182).  The backend serves **both** the
API (`/api/*`) and the static files — exactly as it does in production.
No Vite dev server, no HMR, no proxy.

---

## Quick start

```bash
# From the repo root (SQLite — the default)
./run_e2e_tests.sh

# From the repo root (PostgreSQL — needs Docker)
./run_e2e_tests_postgres.sh

# Or directly from the frontend directory
cd CheckCheck/frontend
bunx playwright test
```

First run takes ~3–4 min (`nuxt generate` builds the static bundle + backend
startup + 19 tests).  Subsequent headless runs are similar — the build always
runs fresh to pick up frontend changes.

`--ui` mode skips `nuxt generate` when `CheckCheck/frontend/.output/public/`
already exists, so it becomes interactive in ~15 s after an initial headless run.
Force a rebuild: `FORCE_BUILD=1 ./run_e2e_tests.sh --ui`.

### Other useful commands

```bash
# Pick a test interactively and step through it with the Playwright inspector
./run_e2e_tests.sh --pick             # fzf list → selected test → --debug mode
./run_e2e_tests.sh --pick --headed    # same picker, just watch (no step-through)

# Run a specific test by name (substring / regex)
./run_e2e_tests.sh --grep "redirects to / after"

# Run a single spec file
./run_e2e_tests.sh tests/e2e/sync.spec.ts

# Step through a known test name without the picker
./run_e2e_tests.sh --debug --grep "cross-tab sync"

# Open the HTML report (traces included for every test)
cd CheckCheck/frontend && bunx playwright show-report
```

> **Tip:** install `fzf` (`apt install fzf`) for a fuzzy-searchable `--pick` list.

> **Note on `--ui` mode:** Playwright's interactive UI (`bunx playwright test --ui`)
> leaves the browser on "loading…" indefinitely and never shows the test list.
> Investigated: the Playwright HTTP server starts correctly (returns a 302 to
> `uiMode.html`) but every WebSocket upgrade attempt to the WS endpoint is
> immediately closed by the server before any data is exchanged.  Tried both the
> default mode (Playwright opens its own Chromium window) and `--ui-port` (served
> to an existing browser tab) — same result in both cases, which rules out the
> browser launch as the cause.  Root cause is unknown; likely a Playwright 1.60.0
> bug with `globalSetup` or the WS server on this Linux setup.  Use `--pick` +
> `--debug` instead for interactive test inspection.

---

## How it works

### Ports

| Service | Dev | Unit tests | E2E tests |
|---------|-----|------------|-----------|
| Backend | 8181 | 8888 | **8182** |
| Frontend | 3000 | — | **8182** (served by backend) |

The E2E backend serves both the API and the static frontend bundle on port 8182.
There is no separate frontend port.

### Startup sequence

`global-setup.ts` runs before any test worker:

1. **Build the static frontend** — runs `bunx nuxt generate` inside
   `CheckCheck/frontend/`, producing the production bundle in
   `CheckCheck/frontend/.output/public/`.  Takes ~7–10 s.
2. **Kill stale processes** — `fuser -k 8182/tcp` clears orphans from previous
   runs before attempting to bind the port.
3. **Start the E2E backend** — spawns
   `CheckCheck/backend/e2e/start_e2e_server.py` using the project's own
   Python venv.  The script deletes the previous test SQLite database, creates a
   fresh one, provisions test users, and prints `READY` to stdout when the
   health check passes.  `FRONTEND_FILES_DIR` is set to the absolute path of
   the generated bundle so the backend knows where to serve it from.

`global-teardown.ts` sends `SIGTERM` to the backend process **group** (negative
PID), which also terminates the forked uvicorn child.

### Authentication strategy

`auth.setup.ts` logs in once as `admin3` and saves the browser storage state
(session cookie) to `tests/e2e/.auth/state.json`.  Every test in the `chromium`
project then starts with that cookie already in the browser context — no login
step needed per test.

Tests that need to start **unauthenticated** override this at the describe level:

```ts
test.use({ storageState: { cookies: [], origins: [] } });
```

### SSE connections and clean shutdown

The board page (`/`) opens a persistent `EventSource` to `/api/sync`.  If a
test finishes with the page still at `/`, Playwright's browser teardown waits
for all HTTP connections to settle — the SSE stream never closes proactively, so
the runner hangs indefinitely.

Every spec file that navigates to `/` must therefore add a file-level
`test.afterEach` that navigates away:

```ts
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});
```

This is already done in all existing spec files.

---

## Test credentials

| Username | Password | Role |
|----------|----------|------|
| `admin3` | `password123` | admin |
| `testuser01` | `testuserpw_secure1` | regular user |

Provisioned at backend startup from
`CheckCheck/backend/tests/provisioning_data/test_users.yaml`.
The admin user is created from env vars set in `start_e2e_server.py`.

---

## File map

```
CheckCheck/frontend/
├── playwright.config.ts              # Main Playwright config (baseURL = http://localhost:8182)
├── tests/e2e/
│   ├── tsconfig.json                 # Node types for global-setup/teardown
│   ├── global-setup.ts              # Build frontend, start backend
│   ├── global-teardown.ts           # Stop backend
│   ├── auth.setup.ts                # Log in once, save session to .auth/
│   ├── LLM_GUIDE.md                 # Quick-reference for writing new tests
│   ├── auth.spec.ts                 # Login page, error handling, logout
│   ├── checklist.spec.ts            # Board smoke tests: grid, modal, search URL
│   ├── card-movement.spec.ts        # Drag-and-drop card reordering
│   ├── item-movement.spec.ts        # Drag-and-drop item reordering (in edit modal)
│   ├── filter-search.spec.ts        # Label filter, search, combined label+search
│   └── sync.spec.ts                 # Two-client SSE sync (new checklist, item state)
│
CheckCheck/backend/e2e/
├── start_e2e_server.py              # Starts backend on port 8182 with fresh DB
└── provisioning_data/test_users.yaml  # E2E test users (provisioned at startup)
```

Generated at runtime (gitignored):

```
CheckCheck/frontend/.output/public/  # Static frontend bundle (built by global-setup)
tests/e2e/.auth/state.json           # Saved browser session (auth cookies)
tests/e2e/.e2e-server.pid            # Backend PID for teardown
```

---

## Adding new tests

### Authenticated test (most common)

Every spec file that visits the board **must** include a file-level `afterEach`
to close the SSE connection before Playwright tears down the page (otherwise the
runner hangs):

```ts
import { test, expect } from "@playwright/test";

// Required in every spec that visits "/": closes the SSE connection so
// Playwright can shut down the browser cleanly.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

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
  });
});
```

### Test as the non-admin user

```ts
import { TEST_USER } from "./auth.setup";

test("regular user flow", async ({ page }) => {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.waitForSelector("form");
  await page.locator("[data-testid=login-username]").fill(TEST_USER.username);
  await page.locator("[data-testid=login-password]").fill(TEST_USER.password);
  await page.locator('form button[type="submit"]').click();
  await page.waitForURL("/");
  // ...
});
```

---

## Selectors — what to use

### Prefer `data-testid` for form elements

```
data-testid="login-username"   → the username <input>
data-testid="login-password"   → the password <input>
data-testid="login-error"      → the UAlert error banner
data-testid="checklist-board"  → the <ul> grid of checklist cards
```

### Nuxt UI 4 quirks

- **`UAlert`** does not render with `role="alert"`. Use `data-testid`.
- **`UButton`** renders as `<button>` — `getByRole("button", { name: "..." })` works.
- **`UModal`** renders with `role="dialog"` — `page.locator('[role="dialog"]')` works.
- **`UCheckbox`** hides the native `<input type="checkbox">` behind a styled wrapper.
  Use `getByRole('checkbox')`, not `locator('[type="checkbox"]')`.
- **`UTextarea`** in edit mode → items in the edit modal render as `<textarea>`.
  `getByText()` and `filter({ hasText })` do **not** match textarea *values*.
  Use `locator("li").nth(n)` and `toHaveValue(/pattern/)` instead.

### Labels in sidebar

The label's display name also appears as a badge on any card that carries it.
Always scope sidebar label locators to `<aside>` to avoid strict-mode violations:

```ts
// ✗ may find the badge on the card too
await page.getByText("MyLabel", { exact: true }).click();

// ✓ scoped to the sidebar
await page.locator("aside").getByText("MyLabel", { exact: true }).click();
```

---

## Configuration reference

Key settings in [`playwright.config.ts`](CheckCheck/frontend/playwright.config.ts):

| Setting | Value | Reason |
|---------|-------|--------|
| `baseURL` | `http://localhost:8182` | Backend serves both API and static frontend |
| `fullyParallel` | `false` | Tests share backend state; parallel execution causes race conditions |
| `retries` | `1` | Absorbs one-off flakiness (e.g. DnD timing) |
| `timeout` | `30 000 ms` | Per-test timeout; overridden per describe for DnD/SSE tests |

---

## Troubleshooting

### `address already in use` on port 8182

```bash
fuser -k 8182/tcp 2>/dev/null
sleep 1
./run_e2e_tests.sh
```

### Blank white page / frontend not loading

The static build is missing or incomplete.  Delete and rebuild:

```bash
rm -rf CheckCheck/frontend/.output
./run_e2e_tests.sh
```

### Backend Python venv not found

```
Error: Backend venv not found at .../CheckCheck/backend/.venv/bin/python
```

```bash
source build_server_dev_env.sh   # from repo root
```

### Test runner hangs after all tests complete

A spec file is leaving the board page open (SSE connection not closed).
Add a file-level `test.afterEach` with `page.goto("about:blank")` to that spec.
All existing spec files already do this.

### PostgreSQL run

`./run_e2e_tests_postgres.sh` starts a temporary Docker Postgres container,
runs the full suite against it, then removes the container.  Requires Docker.

---

## For coding agents

- **Failure screenshots** land in `test-results/` — readable via the `Read` tool.
- **Playwright traces** land in `test-results/*/trace.zip` for **every** test run
  (`trace: "on"` in `playwright.config.ts`) — not just on retry.
- **HTML report** at `playwright-report/index.html` — open with
  `cd CheckCheck/frontend && bunx playwright show-report`.
- **Headed mode**: `cd CheckCheck/frontend && bunx playwright test --headed`
- **Step-through a single test**: `./run_e2e_tests.sh --debug --grep "test name"`
