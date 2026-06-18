---
name: e2e-testing-status
description: Status of Playwright E2E test integration — what works, what's blocked, the root cause, and next steps.
metadata:
  type: project
---

Playwright E2E test suite was implemented but is blocked on a Nuxt dev server issue.

**Why:** The Nuxt dev server's Vite disk cache (`node_modules/.cache/vite`) becomes stale after `nuxt.config.ts` is modified. With stale cache, the browser loads all JS (HTTP 200) but Vue silently fails to mount, producing a blank white page.

**How to apply:** The fix is already in place in `playwright.config.ts` (webServer command clears the cache before start). The next session should verify this fix works by running the full suite from `CheckCheck/frontend/`.

## What's done

All E2E infrastructure is in place and wired up correctly:

| File | Purpose |
|------|---------|
| `CheckCheck/frontend/playwright.config.ts` | Main config — 2 projects: auth-setup + chromium |
| `CheckCheck/frontend/tests/e2e/global-setup.ts` | Starts E2E backend (port 8182), kills stale process on that port |
| `CheckCheck/frontend/tests/e2e/global-teardown.ts` | Kills backend process group |
| `CheckCheck/backend/e2e/start_e2e_server.py` | Starts CheckCheck backend with a fresh SQLite DB on port 8182 |
| `CheckCheck/frontend/tests/e2e/auth.setup.ts` | Logs in as admin3, saves storageState to `.auth/state.json` |
| `CheckCheck/frontend/tests/e2e/auth.spec.ts` | 5 tests: login page, error on bad creds, redirect on success, logout |
| `CheckCheck/frontend/tests/e2e/checklist.spec.ts` | 5 tests: board grid, new button, search, modal opens, URL update |
| `run_e2e_tests.sh` | Convenience script from repo root |

11 tests are discovered correctly (`bunx playwright test --list` from `CheckCheck/frontend/`).

## What's blocked

The auth-setup test (and all 10 dependent tests) fail because the login page renders as a completely blank white page in the Playwright browser.

**Root cause confirmed:** Vite disk cache (`node_modules/.cache/vite/client/`) is stale → Vue's virtual Nuxt modules have stale transforms → Vue fails to mount silently. The browser's console only shows a WebSocket error (HMR to wrong port) and nothing else. The `/api/auth/list` call is never made.

**Evidence:**
- First run AFTER `bun add @types/node` (which forces Vite to re-optimize from scratch): `auth.setup.ts` test **passed** in 17.7s, form appeared, login worked.
- All subsequent runs with warm Vite cache: blank page, form never appears, test times out.

**Fixes applied:**
1. `nuxt.config.ts`: `process.env.PORT` for HMR port (so WebSocket connects to port 3001 not 3000)
2. `playwright.config.ts` webServer command: `rm -rf node_modules/.cache/vite && ...` before starting Nuxt — forces fresh Vite optimization on each E2E run (~5s extra, acceptable)

## How to verify the fix

From `CheckCheck/frontend/`:
```bash
# Kill any stale processes first
fuser -k 8182/tcp 2>/dev/null; fuser -k 3001/tcp 2>/dev/null
sleep 1
bunx playwright test
```

Expected: 11 tests pass. The Vite cache clear means ~15-20s for the first page to be ready.

## Other notes

- **Test credentials:** `admin3 / password123` (admin) and `testuser01 / testuserpw_secure1` (regular user)
- **Ports:** Backend E2E = 8182, Frontend E2E = 3001 (dev uses 8181 and 3000)
- **Process cleanup:** Global setup calls `fuser -k 8182/tcp` before starting to kill orphaned backends from previous runs
- **Process group kill:** The backend is started with `detached: true`; teardown sends `SIGTERM` to `-pid` (process group) so the uvicorn subprocess also dies
- **Broken symlink removed:** `CheckCheck/frontend/dist` was a symlink to `/app/.output/public` (Docker path, doesn't exist locally) — it caused Bun's file watcher to crash. It was removed.
