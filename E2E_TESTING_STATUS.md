# E2E Testing — Setup & Status (2026-06-01)

## Status: ✅ Done — 11/11 tests pass in ~23 s

```bash
cd CheckCheck/frontend
fuser -k 8182/tcp 2>/dev/null; fuser -k 3001/tcp 2>/dev/null
sleep 1
bunx playwright test
```

---

## What was built

| File | Purpose |
|------|---------|
| `CheckCheck/frontend/playwright.config.ts` | Main Playwright config |
| `CheckCheck/frontend/tests/e2e/global-setup.ts` | Starts the E2E backend, kills any stale process on port 8182 |
| `CheckCheck/frontend/tests/e2e/global-teardown.ts` | Kills the backend process group |
| `CheckCheck/backend/e2e/start_e2e_server.py` | Starts CheckCheck backend with a fresh SQLite DB on port 8182 |
| `CheckCheck/frontend/tests/e2e/auth.setup.ts` | Logs in, saves session to `.auth/state.json` |
| `CheckCheck/frontend/tests/e2e/auth.spec.ts` | Login page, bad-creds error, redirect on success, logout |
| `CheckCheck/frontend/tests/e2e/checklist.spec.ts` | Board renders, New button, search, modal, URL filter |
| `run_e2e_tests.sh` | Convenience wrapper from the repo root |

### Port map

| Service | Dev | Unit tests | E2E tests |
|---------|-----|-----------|-----------|
| Backend | 8181 | 8888 | **8182** |
| Frontend | 3000 | — | **3001** |

### Test credentials

| User | Password | Role |
|------|----------|------|
| `admin3` | `password123` | admin |
| `testuser01` | `testuserpw_secure1` | regular |

---

## What works

- `bunx playwright test --list` discovers all 11 tests correctly.
- The Python E2E backend starts cleanly, deletes and recreates the SQLite DB, provisions test users, and prints `READY` when the health check passes.
- Process group teardown (`process.kill(-pid, 'SIGTERM')`) correctly kills both the wrapper script and the forked uvicorn child so port 8182 is released.
- A stale process holding port 8182 is killed via `fuser -k` at the start of each run, preventing "address already in use" errors on re-runs.
- The login test **did pass** once — after a fresh Vite optimisation (triggered by adding `@types/node` to the lockfile) the auth-setup test ran in 17.7 s and the form appeared, credentials were accepted, and `waitForURL('/')` succeeded.

---

## The blocker and its fix

### Root cause

The Vite disk cache (`node_modules/.cache/vite/client/`) stores pre-bundled module transforms.
After `nuxt.config.ts` was modified (to add `API_PROXY_TARGET` env-var support and fix the HMR port), the cache held stale transforms for Nuxt's virtual modules. Vite used the stale cache on the next startup, causing Vue to silently fail to mount — the browser received all JS files (HTTP 200) but the `#__nuxt` div was never populated.

This produced a completely blank white page. The only console error was a Vite HMR WebSocket failure (connecting to port 3000 instead of 3001).

### Changes made to fix it

1. **`nuxt.config.ts`** — HMR port now reads `process.env.PORT` so it matches the server port:
   ```ts
   hmr: {
     port: parseInt(process.env.PORT ?? "3000"),
     clientPort: parseInt(process.env.PORT ?? "3000"),
   }
   ```

2. **`playwright.config.ts`** — webServer command clears the stale Vite cache before starting:
   ```ts
   command: `rm -rf node_modules/.cache/vite && API_PROXY_TARGET=... PORT=3001 bun --bun run dev`
   ```
   This adds ~5 s for Vite to re-optimise but guarantees a clean state on every run.

### Status

The fix is **applied but not yet run** — the session ended before a final verification pass.

---

## How to extend the tests

Add new spec files to `CheckCheck/frontend/tests/e2e/`.
Tests in the `chromium` project start pre-authenticated (admin session).
To run a test as an unauthenticated user, add:
```ts
test.use({ storageState: { cookies: [], origins: [] } });
```

The `TEST_USER` (`testuser01`) credentials are exported from `auth.setup.ts` if you need a non-admin session.

---

## Misc notes

- A broken symlink `CheckCheck/frontend/dist → /app/.output/public` (Docker artifact) was removed — it caused Bun's file watcher to crash on startup with `ENOENT`.
- `data-testid="login-username"` and `data-testid="login-password"` were added to the login form inputs.
- `data-testid="checklist-board"` was added to the `<ul>` in `CheckListBoard.vue`.
- `@playwright/test` and `@types/node` are in `devDependencies`; Chromium browser binary is installed at `~/.cache/ms-playwright/`.
