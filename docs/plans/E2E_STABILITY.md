# Plan: E2E suite stability

**Status:** S0, S1, S2, S3, S4 and S6 implemented 2026-08-09. S5 (per-worker
accounts) is the only chunk left; the plan gates it on several green serial runs.
Two things the investigation had not seen turned up during implementation and are
fixed here too: see [§5](#5-what-changed-versus-this-plan).

The run of `./run_e2e_tests_postgres.sh` that triggered this plan finished with
1 failed, 4 flaky, 2 skipped, 132 passed. This document says what each of those
five actually was, which of them are test problems and which are application
bugs the suite happened to catch, and in what order to fix them.

The short version: the suite runs 4 workers in parallel, and every worker drives
the *same* `admin3` account against the *same* database, so specs corrupt each
other's board. That accounts for four of the five. The fifth (and two more that
this investigation found, which the parallel run had been hiding) are genuine
sync and pagination bugs in the app.

---

## 0. Standing rules

- No em dashes or en dashes anywhere: docs, comments, commit messages, UI copy.
- Anything a user clicks gets vitest and/or Playwright coverage.
- **The maintainer commits, nobody else.** Leave the work dirty in the tree and
  report what changed.
- Postgres is the target the suite must be green on
  (`run_e2e_tests_postgres.sh`); the SQLite runner has to keep working but is a
  dev convenience.
- No schema change anywhere in this plan, so no Alembic revision. Head stays
  `0018`.

---

## 1. What the run actually showed

Evidence below is from the failing run's traces plus one control experiment:
the five affected spec files re-run with `--workers=1 --retries=0` against a
fresh Postgres. Result of the control: **13 of 15 passed**, and the two that
still failed are not the ones that failed in the parallel run.

| # | Spec | Parallel run | Serial control | Verdict |
|---|---|---|---|---|
| 1 | `card-movement` reorder persists | failed twice | passed | interference, plus bug **B1** |
| 2 | `counts` home to archive | flaky | passed | interference |
| 3 | `sharing-modal` owner revoke | flaky | passed | interference, plus bug **B2** |
| 4 | `offline-sync` concurrent edit conflict | flaky | passed | interference |
| 5 | `local-delta-apply` label chip via delta | flaky | **failed** | bug **B4** |
| 6 | `local-delta-apply` offline gap, one delta pull | passed | **failed** | bug **B3** |

### 1.1 The shared-account problem (cause of 1 to 4)

`playwright.config.ts` sets `fullyParallel: false` but leaves `workers`
unset, so Playwright used 4 workers (half of 8 cores). `fullyParallel: false`
only serialises tests *within* a file; files still run concurrently. Every spec
in the `chromium` project loads the same `tests/e2e/.auth/state.json`, so four
workers create, archive, reorder and delete cards on one user's board at once.

`tests/e2e/LLM_GUIDE.md` states the opposite ("Tests run **sequentially**
(`fullyParallel: false`)"), which is why so many specs were written against
absolute board state. That sentence is wrong and has been wrong for every spec
written since.

Concrete damage seen in the traces:

- **counts**: the spec reads the Home badge, archives one card, and expects the
  badge to drop by exactly 1. It read 7 where it expected 5, because other
  workers created two cards during the archive.
- **sharing-modal revoke**: the failure was `Error 403` toasts on the *owner's*
  page, not the collaborator's. Trace shows
  `GET /api/item?checklist_ids=<a>&checklist_ids=<b>&checklist_ids=<c>` returning
  403: another worker deleted one of those cards mid-flight. See **B2**.
- **card-movement**: the drag itself worked. Server state after the drag was
  correct (card A at index `0.4`, card B at `0.2`, so A sorts above B), yet the
  reloaded DOM showed B above A. See **B1**.

### 1.2 Application bugs this uncovered

**B1. A board page is appended without re-sorting.**
`stores/checklist.ts:265` `fetchNextPage()` pushes `resChecklistPage.items`
onto `checkLists` and never calls `_sort()`. The board renders `checkLists` in
array order (`CheckListBoard.vue` watchEffect over
`checkListStore.getCheckLists(...)`, which returns the array as-is). Paging is
offset based with `limit: 5`, so if the server-side order shifts between page
requests (another device, another tab, an SSE-driven change, or in the suite
another worker), page 2 can contain rows that belong above page 1's tail, and
they stay in the wrong place until something else triggers a sort. The
card-movement trace shows exactly this: `offset=0&limit=5`, then `offset=3`, then
after reload `offset=2`, all returning overlapping sets. This is a real
user-facing bug on any board with more than 5 cards, not only a test artifact.

**B2. One inaccessible id fails an entire preview batch, with a scary toast.**
`GET /api/item` (routes_checklist_item.py:141) raises 403 for the whole request
if *any* requested `checklist_id` is not in the caller's access set, and a
concurrently deleted card is also "not accessible". The board asks for previews
in batches, so a single card deleted or revoked elsewhere while your board is
open kills the previews for every card in that batch and surfaces the generic
`Error 403 / GET /api/item failed` toast from `plugins/api.ts:75`. Users hit
this whenever a share is revoked or a card is deleted on another device.

**B3. Nothing recovers a tab from an offline gap if the SSE stream dies quietly.**
`composables/useSync.ts` catches up after a gap in exactly one place: the
EventSource `onopen` handler, reached either by the browser's own retry or by
`scheduleReconnect()` on `onerror`. If the connection goes dead without firing
`onerror` (which is what Playwright's `context.setOffline` produces, and what a
sleeping laptop, a dropped Wi-Fi link or a silently dropped NAT mapping produce
too), no `onopen` ever runs. There is no `onConnectivityChange(up => reconnect)`
subscriber, and `applyDelta` is explicitly best-effort with no retry ("a failed
pull leaves the cursor untouched", `utils/localSnapshot.ts:481`). The trace for
test 6 shows the endgame: two `/api/changes` pulls aborted at the moment
connectivity was restored, then **no further `/api/sync` and no further
`/api/changes` for the remaining 20 seconds**. The tab sat there stale. Pokes
only arrive over SSE, so a zombie stream means a permanently stale board.

**B4. The SSE stream reports itself live before the server has subscribed it.**
`_postgres_stream` / `_sqlite_stream` (routes_sync_notification.py:262 and :290)
append the client to the fan-out list as the first statement *inside the
generator*, which Starlette only starts iterating after it has already sent the
response headers. Both the browser's `onopen` and Playwright's `waitForResponse`
fire on those headers. Any notification published in that window is fanned out
to a client set that does not yet include this client, and pokes are not
redelivered, so the change is invisible until the next unrelated poke or a
reconnect. Test 5's trace is a clean capture: `DELETE /api/label/...` at
`50.849`, and the tab never issued a single `/api/changes` afterwards. The
spec's own `syncConnected()` helper documents the race and then loses it anyway,
because there is nothing better to wait on.

---

## 2. Chunks

### S0. Make the suite honest again (do this first)

1. `playwright.config.ts`: set `workers: 1`.
2. `tests/e2e/LLM_GUIDE.md`: fix the "tests run sequentially" claim to describe
   what the config now actually does, and state the rule the specs already
   assume: *the board is one shared account, so no spec may assume it owns it*.
3. Re-run the full suite to get a clean baseline.

Cost: wall clock goes from about 3.6 minutes to roughly 8 to 12 minutes
(the serial control ran 15 tests in about 1 minute of test time). That is the
price of a deterministic suite until S5 buys the parallelism back properly.

Expected outcome: failures 1 to 4 stop. Failures 5 and 6 remain, and are now
reproducible on demand instead of hiding behind retries.

### S1. Sort after appending a page (bug B1)

- `stores/checklist.ts` `fetchNextPage()`: call `this._sort()` after the append,
  the same way `resync()` does.
- Check `_fetchFilteredPage()` for the same omission and fix it if present.
- Vitest: append a page whose rows interleave by index with the already-loaded
  ones, assert the resulting order is by `pinned` then descending index.
- The existing `card-movement` spec is the E2E regression test; no change needed.

Optional follow-up, not required for green: offset paging over a list that
mutates also *skips* rows. Keyset paging (`index < last_seen_index`) would fix
that class outright. Out of scope here, worth an issue.

### S2. Contain the preview-batch 403 (bug B2)

Two halves, both wanted:

- **Backend**: `GET /api/item` returns previews for the ids the caller *can*
  see and silently omits the rest, instead of 403ing the batch. This leaks
  nothing (the response never mentions the omitted ids) and matches what the
  endpoint is for (a bootstrap overview). Keep 403 for the single-checklist
  routes, which are the ones an IDOR test cares about. Backend test: request a
  mix of owned and foreign ids, assert 200 and that only the owned ones come
  back.
- **Frontend**: the preview fetch in `stores/checklist_item.ts` passes
  `skipErrorToast: true` and, on a 4xx, triggers one `applyDelta` so the board
  drops whatever it no longer has access to. A card vanishing from under the
  user is a normal event in a shared app and must not produce a raw status-code
  toast.

E2E: extend `sharing-modal`'s revoke test to assert the collaborator's board
loses the card *and* that neither page shows an `Error 4xx` toast. That is
already asserted; it will simply stop being luck.

### S3. A real SSE readiness handshake (bug B4)

- **Backend** (`routes_sync_notification.py`): in both stream generators, append
  the client to the fan-out list and then immediately
  `yield "event: ready\ndata: {}\n\n"` as the first message. Registration now
  provably precedes the client's notion of "connected".
- **Frontend** (`composables/useSync.ts`): move what `onopen` does today
  (`setConnectivity(true)`, the `hasOpened` reconnect delta pull) onto an
  `es.addEventListener("ready", ...)` handler. `onopen` keeps only the backoff
  reset. Anonymous public-viewer subscribers are unaffected: they listen on
  `onmessage`, which named events do not reach.
- **Testability**: expose the live state on the existing navbar chip
  (`components/SyncStatusIndicator.vue`, `data-testid="sync-status-chip"`) as
  `data-sync-live="true|false"`, fed by the same signal.
- **Specs**: replace `syncConnected()` in `local-delta-apply.spec.ts`,
  `offline-sync.spec.ts` and `sync.spec.ts` with a helper that waits for
  `[data-testid=sync-status-chip][data-sync-live=true]`. Delete the comment
  block explaining the race, since there is no longer one.

### S4. Recover from an offline gap without depending on `onerror` (bug B3)

In `composables/useSync.ts`:

- Subscribe to `onConnectivityChange`. On a false-to-true transition, force a
  stream rebuild (`disconnect(); connect();` preserving `hasOpened`) rather than
  waiting for an error that may never come.
- Give the delta pull a retry: if `applyDelta` returns without reaching the
  server, schedule one retry on the same capped backoff the reconnect uses.
  `pullAndApply` already reports whether the pull reached the server
  (`endSync(ok)`), so the signal exists; it just needs to be acted on.
- Keep the existing `visibilitychange` catch-up as is.

Vitest around the connectivity-to-reconnect wiring, plus the existing
`local-delta-apply` offline-gap spec as the E2E. That spec asserts no full board
refetch happens, which a delta-pull-only recovery satisfies.

### S5. Give parallelism back safely (per-worker accounts)

Only after S0 to S4 are green. This is the piece that returns the suite to about
4 minutes without returning the interference.

- Provision one account pair per worker in
  `backend/e2e/provisioning_data/test_users.yaml`: `e2e_w0_a` / `e2e_w0_b`
  through `e2e_w3_a` / `e2e_w3_b`, fixed UUIDs, same shape as `testuser01`.
- Replace the single `auth.setup.ts` with a worker-scoped `storageState`
  fixture (Playwright's documented "one account per parallel worker" pattern):
  each worker logs its own `_a` user in once and writes
  `.auth/state-w<N>.json`. Specs needing a second actor take `_b` of the same
  worker.
- Verify which specs genuinely need the *admin* role (api-keys, notification
  settings, user management are the candidates). Either grant the per-worker
  users the same roles as `admin3`, or keep those specs on `admin3` and pin them
  to a serial project.
- Set `workers` back to the default and `fullyParallel: true` once every spec is
  isolated. Any spec that then fails is asserting on state it does not own, which
  is exactly the bug we want surfaced.

### S6. Harness hygiene

- `trace: "on"` records a trace for all 139 tests. Switch to
  `trace: "retain-on-failure"` (and keep `screenshot: "only-on-failure"`), which
  removes a chunk of the per-test cost. `--trace on` stays available for a single
  spec when debugging.
- `retries: 1` is what converts a real bug into a green "flaky" line. Keep the
  retry, but treat every `flaky` line as a defect to file rather than noise. Add
  a `test:e2e:flakehunt` script (`playwright test --repeat-each=5 --retries=0
  <file>`) so a suspect spec can be characterised in one command.
- Note in `LLM_GUIDE.md` that `test-results/` is wiped at the start of every run,
  so traces from a failing run must be copied out before re-running.
- Update the `flaky-e2e-dnd-sharing` note ("re-run before blaming your change"):
  after this plan, a re-run is no longer an acceptable answer.

---

## 3. Order and expected end state

1. **S0** now. One line plus a doc correction, turns 5 mysteries into 2.
2. **S1**, **S2** next: small, self-contained, each fixes a real user-facing bug.
3. **S3**, **S4** next: these are the sync-layer bugs, and they are the reason
   tests 5 and 6 fail even with a quiet machine. Land them together, since S3's
   readiness signal is what makes S4's reconnect assertion testable.
4. **S6** any time.
5. **S5** last, and only once the suite is green serially for several runs.

Done when: `./run_e2e_tests_postgres.sh` reports 0 failed **and 0 flaky** on
three consecutive runs, and `--repeat-each=5 --retries=0` on
`local-delta-apply`, `offline-sync`, `card-movement`, `counts` and
`sharing-modal` is clean.

---

## 4. Deliberately not doing

- Rewriting the drag helpers. The DnD specs were never the problem here; the
  card-movement failure was pagination, not the drag (the drag produced the
  correct fractional index `0.2` and the server stored it).
- Giving each spec its own database. One backend process plus per-worker
  accounts (S5) is enough isolation, and per-spec databases would cost a server
  boot each.
- Loosening assertions to make specs pass (widening timeouts, dropping the
  `Error 4xx` checks, asserting "at least one" instead of exact counts). Every
  one of those assertions caught something real in this run.

---

## 5. What changed versus this plan

Written after implementing S0 to S4 and S6.

**B5 (new). A label change is never poked, so nobody learns about it.**
`delete_label` / `update_label` (`routes_checklist_label.py`) emitted no sync
notification at all. Renaming or deleting a label changes the chips on every card
it is attached to, but those cards' own rows are untouched, so nothing at the card
level announced it either. The delta feed *does* carry the label change and its
tombstone, but a local-first client only pulls that feed in response to a poke,
and no poke existed. Another open tab kept the stale chip until a reload.

This, not B4, is why test 5 (`local-delta-apply` label chip) failed in the serial
control: with four workers, unrelated test traffic poked the same admin account
often enough to trigger a pull that happened to include the tombstone. The fix
emits one `checklist_label` event (and therefore one poke) per affected card,
pinned to the label's owner, since labels are a per-user layer. Covered by
`tests_label_sync.py`. B4 was real too and is still fixed by S3; it just was not
what that spec was tripping over.

**Two `notifications` specs were also asserting state they do not own.**
`testuser01` is shared with by `invites`, `sharing-*` and `offline-sync`, and
every share leaves them a notification that nothing clears. The specs asserted an
absolute unread badge count (`toContainText("1")`) and an absolute feed length
(`toHaveCount(1)`). Under four workers they happened to run before those specs;
serially they run after and read 2 and 3. Fixed inside the spec, without
loosening anything: `testuser01`'s notifications are marked read at login (so the
badge count is again exactly this share's), and the feed assertion is scoped to
the row this share produced, matched by the card's unique title, and additionally
asserts that row is the newest. This is exactly the class of latent bug S0 was
expected to surface.

**S6's SSE readiness dividend for backend tests.** `_SSECollector`
(`tests_sharing_sync.py`) used to wait for response headers and then sleep a
second, hoping registration had happened. It now waits for the server's `ready`
message, which is the guarantee it was approximating.

**S1's optional follow-up is filed, not done.** Offset paging over a list that
moves does not only misorder rows (fixed), it also *skips* them. That needs
keyset paging and is logged in `docs/ISSUES.md` ("Board paging is offset based,
so a board that moves while you page SKIPS rows").
