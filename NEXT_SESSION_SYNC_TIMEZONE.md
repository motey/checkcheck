
This is a throw away note file. Not to be committed. At the end of this session, write a cold start prompt for the following session in this file (overwrite).

# Next up: T (time zone re-sync on login) — the only chunk left

The brief is `docs/plans/SYSTEM_NOTIFICATIONS.md`. P1 (backend push channel)
and P2 (frontend subscribe flow) are both done, and section 10's table plus
the P1/P2 notes right below it are up to date — read the P2 notes before
touching `usePushSubscription.ts`, `public/sw-push.js` or the push block in
`NotificationSettingsModal.vue`. T is what remains: plan section 8's third
chunk, small and independent, described in full at the bottom of section 8
(and decision 4 in section 2). Nothing here is half-finished.

## Where the tree stands

Branch `feature/email-notifications`. Everything from this session is
**uncommitted** in the working tree (the user has not asked for a commit yet):

New files:
- `CheckCheck/frontend/composables/usePushSubscription.ts`
- `CheckCheck/frontend/public/sw-push.js`
- `CheckCheck/frontend/tests/unit/push.spec.ts`
- `CheckCheck/frontend/utils/push.ts`

Modified: `CheckCheck/backend/e2e/start_e2e_server.py` (push on + throwaway
VAPID pair for the E2E instance), `CheckCheck/frontend/nuxt.config.ts`
(`workbox.importScripts: ["/sw-push.js"]`),
`CheckCheck/frontend/components/NotificationSettingsModal.vue` (push column +
device-management block), `CheckCheck/frontend/stores/notification.ts`
(`pushSubscriptions` state + 4 actions), `CheckCheck/frontend/stores/publicConfig.ts`
(`vapidPublicKey` getter), `CheckCheck/frontend/types/index.ts` (3 push types),
`CheckCheck/frontend/utils/notificationSettings.ts` (`push` in
`VISIBLE_CHANNELS`/`visibleChannels`/`CHANNEL_WORDING`),
`CheckCheck/frontend/tests/unit/notificationSettings.spec.ts` (push cases added
to the existing `visibleChannels` describe), `CheckCheck/frontend/tests/e2e/notification-settings.spec.ts`
(new `P2 push notifications` describe, 5 specs), `docs/plans/SYSTEM_NOTIFICATIONS.md`
(progress table + P2 notes section).

`CheckCheck/openapi.json` and `CheckCheck/backend/pdm.lock` were touched by
running the dev/E2E servers this session (version-line and unrelated
transitive-dep churn) and were reverted with `git checkout --` before ending
the session — neither should show as modified when you check `git status`.

Green: full vitest (`bun run vitest run`, 300 passed, includes the 13 new
`push.spec.ts` cases) and the full Playwright suite (`./run_e2e_tests.sh`,
111 passed / 2 skipped — the invite-mode specs, which need a separate
`SHARING_REQUIRE_INVITE_ACCEPT=1` pass — plus 3 pre-existing flaky specs
unrelated to this work that passed on retry: `counts.spec.ts`,
`label-reorder.spec.ts`, `pin.spec.ts` REPRO2). All 5 new push specs passed
clean, no retries. No backend test changes this session (P2 is frontend-only);
the backend suite was not re-run.

## What P2 actually built

The subscribe/unsubscribe/device-list flow end to end, wired to P1's
endpoints. `public/sw-push.js` is imported into the existing Workbox-generated
service worker (`pwa.workbox.importScripts`, not a second registration) and
owns three listeners: `push` (shows the notification), `notificationclick`
(focuses/navigates an existing tab or opens one at the `/?card=&n=` deep-link
URL — the email deep link's own contract, no new one), and
`pushsubscriptionchange` (re-subscribes and re-POSTs entirely inside the
worker, since a push service can rotate a subscription with no tab open to
catch it — same-origin `fetch` from a SW carries the session cookie by
default, no extra auth wiring needed).

`utils/push.ts` is the framework-free layer (VAPID key base64url→bytes
conversion, iOS/iPadOS-as-Macintosh detection via `maxTouchPoints`, standalone-
display detection, the enable-button gating decision, device labelling),
unit-tested in `tests/unit/push.spec.ts`. `composables/usePushSubscription.ts`
is the thin browser-API layer on top (`Notification.requestPermission`,
`PushManager.subscribe`, wired to the store's HTTP calls) that
`NotificationSettingsModal.vue` consumes declaratively, the same shape as the
email/webhook blocks: an enable button, a device list with per-row remove
(labelled "this device" by comparing the row's `endpoint` to the browser's own
current subscription), and a test-push button disabled with no device. The
push column itself is gated on the settings response's own `push_enabled`
flag, matching how `email_enabled`/`webhook_enabled` already gate their
columns — not the public-config copy. The VAPID public key needed as
`applicationServerKey` *does* come from public-config (`vapidPublicKey`
getter on `usePublicConfigStore`), since that's the only place it exists.

E2E coverage mocks `navigator.serviceWorker`/`PushManager` at the JS level
(`addInitScript`, a fake subscription object) rather than subscribing for
real, per the plan's own testing note — nothing in CI can reach a real push
service. The E2E backend now runs with `NOTIFY_PUSH_ENABLED` and the same
throwaway VAPID pair `tests_notification_push.py` uses, so the column and the
device list are real; a "send test push" click does reach the background
dispatcher, which fails harmlessly against the fake endpoint in the log
(`WebPushException: Invalid p256dh key specified`) — same non-error background
failure the webhook test button already produces against an unreachable host.

## Traps that are still true

- **`pdm install --dev` fails with "groups not in lockfile: docs"** — use
  `PDM_IGNORE_ACTIVE_VENV=1 pdm install -G dev -G test` if you need the root
  venv for anything backend-side. The E2E harness itself doesn't need it: it
  runs `CheckCheck/backend/.venv/bin/python` directly
  (`tests/e2e/global-setup.ts`), which already works.
- **`CheckCheck/openapi.json`'s `version` line and `CheckCheck/backend/pdm.lock`**
  are rewritten by every backend/E2E run. Check `git status` before
  committing and `git checkout --` either if the diff is just version/relock
  churn and you didn't intend a dependency change.
- **Never run two backend test invocations at once** (shared port 8888 and
  SQLite file), same for two E2E runs (port 8182).
- No em dashes or en dashes anywhere: docs, comments, commit messages, UI and
  mail copy.
- A few DnD and sharing E2E specs fail non-deterministically (this session:
  `counts.spec.ts`, `label-reorder.spec.ts`, `pin.spec.ts` REPRO2, all
  unrelated to push). Playwright's own retry (`retries: 1`) already covers
  this in a normal run; don't chase them if they pass on retry.
- **The push service worker only exists in a real build** (`devOptions.enabled:
  false`, same as the rest of the PWA layer) — `nuxt dev` never registers it,
  so manual push testing needs `./run_e2e_tests.sh` or a real
  `nuxt generate`/`nuxt build` + serve, not the dev server.

## Commands

```
./run_e2e_tests.sh notification-settings    # this session's specs (P2 push notifications + E5/E6)
./run_e2e_tests.sh                          # full Playwright suite
cd CheckCheck/frontend && bun run vitest run                     # full unit suite
cd CheckCheck/frontend && bun run vitest run tests/unit/push.spec.ts
```

## Not started yet

- **T (time zone re-sync on login)**: plan section 8's third chunk. On
  authenticated app boot (or right after login), compare `detectTimezone()`
  (already in `utils/notificationSettings.ts`) to the loaded settings'
  `timezone`; if different, a silent best-effort `PUT` (fire-and-forget, no
  toast on failure — a convenience, not a critical write). One line of copy
  next to the time-zone picker in `NotificationSettingsModal.vue` stating it's
  kept in sync with the current device (decision 4, section 2 — read it, it's
  a real behavioral trade-off worth stating precisely, not just "add a
  sentence"). Tests: vitest for the comparison/trigger logic, one E2E
  asserting a changed browser time zone updates the stored value on next login
  without opening the modal.
- Nothing has been committed. Ask before committing.
