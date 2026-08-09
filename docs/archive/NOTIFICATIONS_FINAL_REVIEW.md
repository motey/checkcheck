# Final review: the notification branch (`feature/email-notifications`)

**Scope:** the whole branch as it stands at `499484a`, read against
[`EMAIL_NOTIFICATIONS.md`](EMAIL_NOTIFICATIONS.md),
[`DATE_REMINDERS.md`](DATE_REMINDERS.md),
[`SYSTEM_NOTIFICATIONS.md`](SYSTEM_NOTIFICATIONS.md),
[`NOTIFICATIONS_FINDINGS.md`](NOTIFICATIONS_FINDINGS.md) and
[`NOTIFICATIONS_REWORK.md`](NOTIFICATIONS_REWORK.md). The rework chunks N1 to N5
close every finding of the earlier review as claimed; this pass looks for what is
left, with most of the attention on the two chunks that have had no review yet
(N5 and T) and on the seams between chunks.

**Date:** 2026-08-04.

**Overall:** the branch holds up. The push hardening landed the way the rework
plan described it, the error hierarchy in `notify/push.py` is genuinely
delete-safe (the exact-type clause for `PushSubscriptionGone` and the base-class
clause for the refusal cannot be swapped into something destructive), and the
frontend lifecycle work in N5 covers the account-switch and logout paths with
real tests. Three things are worth acting on, one of which is a release blocker
for a particular deployment shape. Two small fixes are applied in the working
tree and marked as such; everything else is documented only.

| # | Severity | Area | One line | State |
|---|---|---|---|---|
| 1 | **High** | push, dispatcher | A push-only instance queues push rows and never drains them | **fixed** |
| 2 | Medium | time zone, chunk T | The re-sync runs on every board mount, so the picker cannot hold a value | documented |
| 3 | Low | time zone, chunk T | A stored literal `"UTC"` left the picker showing nothing | **fixed** |
| 4 | Low | push, rate limit | The hourly push cap silently ignores pushes with no feed row | documented |
| 5 | Low | push, service worker | `pushsubscriptionchange` re-subscribes with the *old* VAPID key | documented |

Test gaps are in section 6.

---

## 1. High: a push-only instance queues push rows and never drains them (fixed)

**Where:** [`notify/dispatcher.py`](../../CheckCheck/backend/checkcheckserver/notify/dispatcher.py),
`channels_enabled` and its two callers.

The gate read:

```python
return bool(cfg.EMAIL_ENABLED or cfg.NOTIFY_WEBHOOK_ENABLED)
```

Push was never added to it when chunk P1 added the channel. Two consequences, in
rising order:

* `dispatch_once` returns an empty `DrainResult` before calling `drain_once`
  whenever `channels_enabled` is false ("nothing can have queued anything"). On an
  instance with `NOTIFY_PUSH_ENABLED: true`, `EMAIL_ENABLED: false` and
  `NOTIFY_WEBHOOK_ENABLED: false`, `_queue_push` still writes rows (it only asks
  `resolve_mode`, which is live because `prefs.channel_enabled` *does* know about
  push), and those rows sit `pending` forever. The "send test push" button's
  `nudge()` wakes a loop that immediately returns.
* `dispatch_enabled` is built on the same function, so with
  `NOTIFY_FEED_RETENTION_DAYS: 0` and `reminder_due` in `NOTIFY_DISABLED_TYPES`
  the loop is not even started.

An operator with no SMTP server who wants lock-screen notifications is exactly the
deployment this describes, and `NOTIFY_PUSH_ENABLED`'s own documentation ("when
false the server never sends a push message") implies the opposite of what
happened.

Every other place that has to know about a channel handles push:
`prefs.channel_enabled`, `prefs.lock_reason`, `CHANNEL_MODES`,
`CODE_DEFAULT_MODES`, `outbox._validate_push_payload`,
`outbox._deliver_claimed`. This one gate was the outlier, and nothing caught it
because the E2E harness and every backend fixture that exercises push also set
`EMAIL_ENABLED: true`.

**Fix applied.** `channels_enabled` now includes `NOTIFY_PUSH_ENABLED`, with a
comment saying why `in_app` is still not in the list (the bell reads the
notification table directly and produces no outbox row). New test:
`tests_notification_push.py::test_a_push_only_instance_counts_as_having_a_channel`,
which asserts both halves (a push-only instance opens the drain, the same
instance with push off does not).

---

## 2. Medium: chunk T's re-sync runs on every board mount, so the picker cannot hold a value

**Where:** [`pages/index.vue`](../../CheckCheck/frontend/pages/index.vue) (the
`onMounted` hook and `bootLocalFirst`),
[`utils/timezoneSync.ts`](../../CheckCheck/frontend/utils/timezoneSync.ts),
the note at
[`components/NotificationSettingsModal.vue`](../../CheckCheck/frontend/components/NotificationSettingsModal.vue)
`data-testid="notification-timezone-sync-note"`.

`syncTimezoneWithDevice()` is called from the board page's `onMounted`, which
fires on every full page load: a reload, a new tab, returning to `/` after a
`/login` bounce, a cold start of the installed PWA. Not "at login".

So a user who deliberately picks a zone other than their device's in the settings
dialog watches the control say **Saved**, and gets it overwritten on their next
page load, on the same device, with nothing on screen. The picker is fully
editable and cannot hold any value except the device's own zone.

The copy next to it describes a much narrower behaviour:

> Kept in sync with this device: signing in from a device in another zone updates
> this.

Decision 4 of the system-notifications plan did choose the silent overwrite
deliberately, and the trade-off it names (a pinned zone cannot survive) is real
and accepted. But it framed it as happening at *login*, and both the plan's
handoff text and this sentence in the UI say so. What shipped is stronger, and
the difference is exactly the case a user would notice: not "my zone followed me
to another country", but "my choice does not stick on this machine".

Three ways out, in rough order of how much they preserve:

1. **Only write when the stored zone is empty.** The bug the chunk exists to fix
   ("a user who never opened the dialog gets a digest at 08:00 UTC") is entirely
   in that case. Costs the moved-country case, which is the one decision 4 cared
   about most.
2. **Remember the last synced device zone client-side** (localStorage, per user)
   and write only when the *device's* zone has actually changed since the last
   sync. A deliberate pick then survives until the user really moves, which is
   what most calendar apps do.
3. **Keep the behaviour and make the UI honest**: reword the note to say the zone
   follows this device on every load, and render the picker as read-only (or drop
   it), because a control that silently reverts is worse than no control.

Whichever is chosen, the note and the picker's editability should agree with it.
Not fixed here: this is a product call, not a defect with one right answer.

**Resolved 2026-08-04, option 2**, alongside chunk S2 of
[`SETTINGS_UX_REWORK.md`](SETTINGS_UX_REWORK.md), which rewrote that part of the
dialog anyway. `utils/timezoneSync.ts` now remembers, per account and per
browser, the device zone it last synced, and writes only when the device reports
a different one. A zone picked in the dialog survives every reload of that
machine; a user who travels still has their digest hour follow them. The note
beside the picker says exactly that, and both halves have unit tests plus an
E2E that reloads after picking a zone.

---

## 3. Low: a stored literal `"UTC"` left the picker showing nothing (fixed)

**Where:** [`utils/notificationSettings.ts`](../../CheckCheck/frontend/utils/notificationSettings.ts)
(`timezoneItems`, `UTC_VALUE`) and the three places
`NotificationSettingsModal.vue` assigned `res.timezone ?? UTC_VALUE` to the
select.

`timezoneItems` deliberately carries UTC exactly once, as the `UTC_VALUE` (empty
string) entry, and deletes any `"UTC"` from the rest of the list. The modal bound
the raw stored value, so a row holding the literal string `"UTC"` selected an
item that does not exist and the picker rendered empty.

That row value is reachable, and chunk T is what makes it likely:
`shouldSyncTimezone("Europe/Berlin", "UTC")` is true, so a user whose device
reports UTC (a CI browser, a server-side workstation, anyone with `TZ=UTC`) has
the literal written over their stored zone at the next boot.

**Fix applied.** New `timezoneSelectValue()` folds `"UTC"`, `null`, `undefined`
and `""` onto `UTC_VALUE`; the modal uses it in all three assignments. Unit test
added to `tests/unit/notificationSettings.spec.ts`.

**Related, not fixed.** The two paths disagree about how "this user is on UTC" is
stored: the manual picker sends `null` (`timezonePatchValue`), the re-sync sends
the string `"UTC"`. Having `syncTimezoneWithDevice` send `null` for a detected
`"UTC"` would remove the divergence at the source. It is a one-line change but it
alters what the server stores, so it is left for the maintainer.

---

## 4. Low: the hourly push cap silently ignores pushes with no feed row

**Where:** [`notify/outbox.py`](../../CheckCheck/backend/checkcheckserver/notify/outbox.py)
`count_recent_for_user`, consumed by
[`db/notification.py`](../../CheckCheck/backend/checkcheckserver/db/notification.py)
`_queue_email` **and** `_queue_push`.

N1 narrowed the cap query to rows carrying a `notification_id`, which was right
for finding 7. The N1 deviations section then documents the side effect honestly,
but only for mail: a user who has the **in-app** channel switched off for a type
gets no feed row, so `_queue_email` has no notification id and the mail stops
counting against `NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR`.

The same is true of `NOTIFY_PUSH_MAX_PER_USER_PER_HOUR`, and it matters slightly
more there: the push cap's stated reason is "a phone buzzing repeatedly is worse
than a full inbox", and the combination (in-app off, push on) is a coherent
choice a user might actually make rather than an oddity. Nothing breaks, the cap
is a flood backstop rather than a correctness rule, but the deviation note should
say so for both channels, or the cap should count by `(user_id, channel)` and
exclude only the producers that key on a sender (the invitation and the test
messages).

---

## 5. Low: `pushsubscriptionchange` re-subscribes with the old VAPID key

**Where:** [`public/sw-push.js`](../../CheckCheck/frontend/public/sw-push.js),
the `pushsubscriptionchange` handler.

The worker re-subscribes with `event.oldSubscription.options.applicationServerKey`,
which is the right key in the ordinary case (the push service rotated an
endpoint) and the wrong one after an operator rotates the VAPID pair. The new
endpoint is then minted for a key the server no longer signs with, every push to
it is a 403, and N3 correctly refuses to treat that as "the device is gone", so
the row fails permanently and the device is silently dead.

The dialog path already handles this: N5 gave `enable()` an
`applicationServerKeyMatches` check that unsubscribes and resubscribes on a
mismatch. The worker cannot reuse it without reading the current key, which means
one `fetch("/api/public-config")` inside the handler, falling back to the old key
if that fails. Worth doing if VAPID rotation is ever expected to be routine; the
recovery today is "the user opens the notification settings once".

---

## 6. Test gaps

Listed roughly in the order I would write them.

1. **(closed) The push-only dispatcher gate.** Added with the fix in section 1.
   Note that no *behavioural* test drains a real push row on a push-only
   instance; the new test asserts the gate, which is where the defect was.
2. **Chunk T's overwrite behaviour is untested in the direction that matters.**
   `tests/e2e/notification-settings.spec.ts` covers the intended path (a device in
   another zone updates the stored value at login) and `tests/unit/timezoneSync.spec.ts`
   covers the comparison. Nothing covers "pick a zone in the dialog, reload, see
   what the picker holds", which is the promise the note next to it makes and the
   behaviour section 2 is about. Whatever is decided there, that is the test.
3. **`public/sw-push.js` has no test at all.** Three event handlers, one of which
   re-registers a subscription over HTTP, and the file is plain JS whose only
   dependency is `self`. A vitest file with a fake `self` would cover the `push`
   handler's payload fallbacks (a push with no data, a body that is not JSON), the
   `notificationclick` focus-versus-`openWindow` branch, and the
   `pushsubscriptionchange` re-POST including the key question in section 5.
   Everything else on this branch that a user can trigger has unit coverage; this
   is the one exception, and it is the piece that runs when no tab is open.
4. **No test of the push cap with `notification_id is None`** (section 4). One
   case either way, whichever the intended behaviour turns out to be, so the
   deviation is pinned rather than remembered.
5. **`render._within_push_budget`'s give-up branch is uncovered.** The
   `available < 2 * PUSH_BODY_RESERVE_BYTES` path (an absurd `SERVER_PUBLIC_URL`)
   logs a warning and returns the payload unshortened. The three budget tests
   cover truncation, ordering and the no-op case, not this one. Cheap to add and
   it is the branch that returns something knowingly oversized.

---

## 7. Verified, for the record

Things this pass checked and found correct, so a later reader does not have to
redo them:

* **N3's delete safety.** `_deliver_push` keys its delete clause on the exact
  `PushSubscriptionGone` type and its refusal clause on the `PermanentPushError`
  base, so reordering the handlers cannot turn a refusal into a delete. The
  transient-beats-refused precedence is deliberate and documented.
* **N4's cap and ownership.** `_enforce_subscription_cap` runs after the upsert
  and evicts by `last_seen_at` within the user, and the row just written always
  has the newest `last_seen_at`, so it cannot evict itself; `last_seen_at` is
  non-nullable with a default, so the ordering is well defined on both backends.
  Both `upsert` branches (the plain one and the `IntegrityError` race) refuse to
  re-assign `user_id`.
* **The push payload budget's arithmetic.** `_json_cost` is additive across the
  two fields, which is what makes the split computable rather than searched, and
  the body reserve means truncation can never produce the empty body that
  `_validate_push_payload` rejects.
* **The recurrence math and the reminder claim.** Wall-clock stepping in local
  time, the monthly anchor recomputed from intent on a `PATCH`, the
  claim-is-the-roll-forward ordering, and the fire-time access re-check borrowing
  `_add_user_has_access_query` rather than restating it.
* **The unsubscribe token and the public-link email endpoint.** Per-user secret,
  constant-time compare, GET shows and POST acts, no address echoed in any
  response or error, sender-scoped hourly limit enforced against the outbox.
* **N5's logout ordering.** The server `DELETE` really does precede the logout
  POST, is time-boxed, and the E2E asserts the row is gone rather than merely
  ignored.

---

## 8. What this review changed

Two fixes and their tests, committed by the maintainer as `e3799e8 minor fixes`
together with this document:

* `CheckCheck/backend/checkcheckserver/notify/dispatcher.py` (section 1)
* `CheckCheck/backend/tests/tests_notification_push.py` (one new test)
* `CheckCheck/frontend/utils/notificationSettings.ts` (section 3)
* `CheckCheck/frontend/components/NotificationSettingsModal.vue` (three call
  sites plus the import)
* `CheckCheck/frontend/tests/unit/notificationSettings.spec.ts` (one new test)
* this document

Gates run: `./run_backend_tests_with_sqlite.sh` (545 passed, 10 skipped),
`./run_backend_tests_with_postgres.sh`, and the frontend unit suite
(`bun run vitest run`, 337 passed, 18 files). The Playwright suite was not run:
nothing here touches a path it exercises, and the modal change is covered by the
unit test above.
