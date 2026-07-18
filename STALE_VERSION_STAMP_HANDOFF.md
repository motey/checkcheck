# Handoff: stale version number in the web client corner

> Cold-start brief for a fresh session. This documents an unresolved issue and
> everything observed/tried. It intentionally contains **no hypotheses, no
> conclusions, and no suggested fixes** — investigate with a fresh mind.

## The problem (as currently understood)

The version string shown in the web client's sidebar corner is **stale**. The
running server and the deployed client bundle seems to be are `0.0.8.dev4+g4f9e8e124`, but
the corner keeps showing `0.0.8.dev3+g7972505a2` in the reporter's Firefox.

Scope correction made late in the session: **the client bundle itself is NOT
stuck.** A feature that shipped in dev4 ("item suggestions") is present and
working in the affected browser. Only the version label in the corner is wrong.

## Where the corner version comes from (verified in code)

- Sidebar renders `serverVersion` — `components/SideMenuNav.vue:202`
  (`const serverVersion = computed(() => publicConfig.serverVersion)`), displayed
  around `SideMenuNav.vue:124-176`.
- `serverVersion` getter — `stores/publicConfig.ts:35`
  (`state.config?.server_version ?? null`).
- `config` is populated by `usePublicConfigStore().fetch()` —
  `stores/publicConfig.ts:38-48`. `fetch()` begins with
  `if (this.config) return this.config;` (in-memory memoization).
- `fetch()` is called in `pages/index.vue` on mount (`pages/index.vue:66` and
  `:79`).
- Source endpoint: `GET /api/public-config`, field `server_version` —
  `backend/checkcheckserver/api/routes/routes_public_config.py:48,76`. The value
  is `checkcheckserver.__version__`.
- `__version__` is stamped at Docker build from the `APP_VERSION` build arg —
  `Dockerfile:28,59-63` (writes `checkcheckserver/__version__.py`).
- The **frontend** build stage receives no version — `Dockerfile:4-14`
  (`bun run build && bunx nuxi generate`, no version env). So the client has no
  build-time version constant; the corner is purely the live API value.

## Relevant architecture facts (verified)

- Single Docker image `motey/checkcheck:dev`: FastAPI backend + a prebuilt Nuxt
  SPA served as static files by the backend (`FRONTEND_FILES_DIR=/app`,
  `Dockerfile:34-36`). Static files served by
  `backend/.../api/routes/routes_webclient.py`.
- Deployment: single container behind **Traefik** (vanilla routing labels only,
  no cache plugin visible in the compose file). Compose at
  `/opt/motey.org/config-repo/compose/44_checkcheck/docker-compose.yml`
  (clone: `/home/tim/Repos/secure.motey.org/server-conf/compose/44_checkcheck`).
- Auth is via Authentik (OIDC). The session is a cookie.
- Service worker (`@vite-pwa/nuxt`, `nuxt.config.ts` `pwa`): `registerType:
  "prompt"`; precaches the app shell; `navigateFallbackDenylist` includes
  `/^\/api\//`; **no** `runtimeCaching` entry for `/api`.

## Cache-control state of the server (changes made earlier this session)

- `backend/checkcheckserver/app.py`: `APINoStoreCacheMiddleware` stamps
  `Cache-Control: no-store` on all `/api/*` responses (pure-ASGI, applied in
  `_apply_api_middleware`).
- `backend/.../routes/routes_webclient.py`: `_cache_control_for()` sets
  `no-cache` on the app shell / `sw.js` / manifest, and
  `public, max-age=31536000, immutable` on `/_nuxt/*`.
- `frontend/plugins/pwa.client.ts`: proactive `registration.update()` on
  tab-focus / `online`, plus a "safe auto-apply" of a waiting SW; falls back to a
  "New version available → Reload" toast.
- These are in the `7972505 "uncache api"` commit (= dev3) and later.

## Commit / version map

- `4f9e8e1 item suggestions`   → running as `dev4+g4f9e8e124` (current server)
- `7972505 uncache api`        → `dev3+g7972505a2` (the value stuck in the corner)
- `1dc5690 update webclient`
- `2687381 fix mobile dnd`

## Observations and things tried (chronological, factual)

1. Reporter updated the deployment dev1 → dev2 → dev3 → dev4 over the session.
   The corner tracked behind: it was stuck at dev1 for a long time, later at
   dev3. Currently stuck at dev3 while the server is dev4.
2. `docker compose logs` confirms the container runs
   `checkcheckserver version: 0.0.8.dev4+g4f9e8e124`.
3. `curl -sI https://check.motey.org/api/public-config` returns
   `cache-control: no-store`.
4. `curl -s https://check.motey.org/api/public-config` returns
   `server_version: "0.0.8.dev4+g4f9e8e124"` (i.e. the server/origin is current;
   cookie-less curl sees the new version).
5. In the reporter's Firefox, the corner shows dev3.
6. Running this in the page console did **not** change the corner version (a
   reload afterward still showed dev3):
   ```js
   (async () => {
     const rs = await navigator.serviceWorker.getRegistrations();
     await Promise.all(rs.map(r => r.unregister()));
     const ks = await caches.keys();
     await Promise.all(ks.map(k => caches.delete(k)));
     location.reload();
   })();
   ```
   (This unregisters service workers and deletes Cache Storage, then reloads.)
7. Plain reload and hard reload (Ctrl+Shift+R) did not change the corner version.
8. The **only** action that fixed the corner (moved it to the current version)
   was Firefox → History → **"Forget About This Site"** for the domain. That
   action also clears cookies, which logs the reporter out of Authentik and
   requires re-login.
9. The dev4 "item suggestions" feature is present in the affected browser (client
   bundle is current).

## Facts that are in tension (unexplained)

- `curl` and the server return **dev4** with `Cache-Control: no-store` on
  `/api/public-config`, the service worker does not cache `/api`, and no
  persistence of the `publicConfig` store to localStorage/IndexedDB was found —
  yet the browser renders **dev3** in the corner.
- Unregistering the service worker + clearing Cache Storage did **not** fix it;
  only "Forget About This Site" (which additionally clears cookies, localStorage,
  IndexedDB, and the HTTP disk cache) did.

## Not yet inspected on the live stuck session (data a fresh session may want)

- DevTools → Network entry for the `public-config` request on the stuck page:
  served from cache vs network, the exact response body, and the exact response
  headers as seen by the browser (vs. curl).
- Contents of `localStorage`, `sessionStorage`, and IndexedDB for the origin on
  the stuck session.
- Whether `publicConfigStore.fetch()` actually issues a network request on a
  fresh load of the stuck session (e.g. Network filter, or a breakpoint/log),
  and what `this.config` holds at mount.
- Whether the affected browser is running the site as an installed PWA (separate
  storage bucket) vs a normal tab.

## Reproduction environment

- Site: https://check.motey.org
- Browser reported: Firefox (also asked about Chrome). "Forget About This Site"
  is Firefox-specific.
- Repo: this working tree. Backend: `CheckCheck/backend`. Frontend:
  `CheckCheck/frontend`.
