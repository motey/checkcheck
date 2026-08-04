# Review findings: notification transports, date reminders, system notifications

**Scope:** every commit on `feature/email-notifications` (`9f54542` through `72cc22f`), reviewed
against [`EMAIL_NOTIFICATIONS.md`](EMAIL_NOTIFICATIONS.md), [`DATE_REMINDERS.md`](DATE_REMINDERS.md)
and [`SYSTEM_NOTIFICATIONS.md`](SYSTEM_NOTIFICATIONS.md).

**Date:** 2026-08-03. **Nothing here is fixed.** This is a findings list only; two one-line
suggestions are noted inline where the fix is obvious, but no code was changed.

**Overall:** the email and reminder chunks (E1 to E7, R1 to R4) hold up well. The
suppression/coalescing/retry machinery, the SSRF guard on the webhook channel, the unsubscribe
token, the ownership scoping on reminders and the recurrence math are all careful, and the test
suites are genuinely thorough (recurrence covers DST both ways, the 31st clamp, and the missed
occurrence loop; the reminder API covers the 404-not-403 rule on every verb).

**The push channel (P1/P2) is the weak spot.** It was built as "the webhook channel minus the
target" but it did not inherit the webhook channel's two defining protections: the destination URL
is never validated, and there is no cap. Findings 1 to 4 are all in that chunk and three of them
are, in my reading, release blockers.

Severity is my own judgement, marked so you can overrule it:

| # | Severity | Area | One line |
|---|---|---|---|
| 1 | **High** | push, security | A user-supplied push `endpoint` is never validated: SSRF with no guard at all |
| 2 | **High** | push, correctness | Any 4xx other than 404/410 deletes the subscription, so one bad VAPID key wipes every device on the instance |
| 3 | Medium | push, security | `upsert` re-binds an existing endpoint to whoever POSTs it, across accounts |
| 4 | Medium | push, security | No cap on subscriptions per user: unbounded fan-out and unbounded table |
| 5 | Medium | push, frontend | A stale browser subscription dead-ends the Enable button; logout leaves push live |
| 6 | Medium | email, correctness | A newline in a card name or a reminder note dead-letters every mail about it |
| 7 | Low | email, correctness | The hourly mail cap counts invitations the user *sent* against mail they *receive* |
| 8 | Low | push, correctness | The drain deletes a dead subscription by `endpoint`, not by id |
| 9 | Low | push, config | The VAPID pair is not checked for correspondence at boot |
| 10 | Nit | frontend | `?card=` is interpolated into a route path unvalidated |
| 11 | Nit | email | The unsubscribe `form_action` HTML-escapes the token but does not URL-encode it |
| 12 | Nit | reminders | Truncated comment in `ReminderScanResult` |

Test gaps are collected in section 13, open questions for you in section 14.

---

## 1. High: the push `endpoint` is never validated, so the push channel is an unguarded SSRF primitive

**Where:**
[`api/routes/routes_notification_settings.py:513-579`](../../CheckCheck/backend/checkcheckserver/api/routes/routes_notification_settings.py)
(`PushSubscriptionRegister`, `register_push_subscription`),
[`db/push_subscription.py:42-99`](../../CheckCheck/backend/checkcheckserver/db/push_subscription.py)
(`upsert`),
[`notify/push.py:133-153`](../../CheckCheck/backend/checkcheckserver/notify/push.py) (`_send_sync`).

`endpoint` is accepted as any string up to 1024 characters. There is no scheme check, no host
check, and no resolve-then-judge guard. It is stored verbatim and handed straight to `pywebpush`,
which does not validate it either: the only check in that library is `if "endpoint" not in
subscription_info` (`pywebpush/__init__.py:166`), after which it calls
`requests.post(endpoint, ...)` with whatever string it was given, following redirects.

So any authenticated user can do this:

```
POST /api/user/me/push-subscriptions
{"endpoint": "http://127.0.0.1:5432/", "keys": {"p256dh": "<a real P-256 point>", "auth": "<16 bytes>"}}
POST /api/user/me/notification-settings/test-push
```

and the server makes an outbound POST to an address only it can reach. Generating a valid
`p256dh`/`auth` pair locally is a few lines, so the encryption requirement is not a barrier.
Loopback, RFC 1918, and `169.254.169.254` are all reachable. It is blind (no response body comes
back to the caller), but the transient/permanent classification in `_send_sync` is an oracle for
"did something answer, and with what status", and the retry policy means each row is 6 attempts.

This is exactly the threat [`notify/webhooks.py`](../../CheckCheck/backend/checkcheckserver/notify/webhooks.py)'s
module docstring describes and defends against in three named steps ("resolve then judge", "connect
to the address that was judged", "no redirects"). None of the three applies here. The webhook
channel additionally sits behind a master switch that is off by default *and* a per-user URL an
operator can reason about; the push channel is the one an operator is most likely to turn on.

**Suggested direction (not applied):** reject at registration anything that is not `https://` with
a public unicast host, reusing `webhooks._is_public_address` / `check_webhook_target`, and re-check
at delivery time for the same rebinding reason the webhook guard documents. An operator-configurable
allowlist of push-service hosts (`fcm.googleapis.com`, `updates.push.services.mozilla.com`,
`*.notify.windows.com`, `web.push.apple.com`) would be stricter still, but the public-address guard
is the minimum and is code that already exists in this repo.

**See also finding 4:** without a per-user cap, this is also an amplifier.

---

## 2. High: any 4xx other than 404/410 deletes the subscription, so one VAPID misconfiguration wipes every device

**Where:** [`notify/push.py:154-171`](../../CheckCheck/backend/checkcheckserver/notify/push.py)
(`_send_sync`'s classification) plus
[`notify/outbox.py:556-568`](../../CheckCheck/backend/checkcheckserver/notify/outbox.py)
(`_deliver_push`'s `PermanentPushError` branch).

The classification is:

* 404, 410 → `PermanentPushError` ("this subscription is gone"),
* 429 or >= 500 → `TransientPushError`,
* **any other status that is not None → `PermanentPushError`**,
* no response → `TransientPushError`.

and `_deliver_push` responds to *every* `PermanentPushError` by calling
`push_subscription.delete_by_endpoint(...)`. The two errors are conflated: "the push service says
this subscription no longer exists" and "the push service refused this request" are not the same
event, and only the first one justifies deleting the row.

The consequences, in rising order of unpleasantness:

* **401 / 403** is what FCM and Mozilla's autopush return for a bad, expired or mismatched VAPID
  signature. That is a *server* misconfiguration (see finding 9: a mismatched key pair boots
  cleanly). The first notification after such a boot deletes every subscription on the instance,
  device by device, and the users have to re-enable push on every browser by hand. Nothing in the
  system records that this happened beyond a debug line per row.
* **413** (payload too large) is a server fault too. A long card name plus a long reminder note in
  `full` push content mode can plausibly exceed a push service's 4 KB limit, and the answer is to
  delete the user's device rather than to truncate the payload.
* **400** from a push service that dislikes a header is likewise unrecoverable-but-not-the-user's-fault.

Plan section 4 says the per-subscription delete is for "a 404 or 410 ... the browser un-registered
it, the user cleared site data, and so on", and the P1 handoff note repeats that. The implementation
is wider than the plan in the one direction where being wider is destructive.

**Suggested direction:** delete only on 404 and 410. Treat every other 4xx as a whole-row permanent
failure (`failed`, kept for inspection, no subscription touched), which is what the outbox's
`failed` state is for and is what an operator needs in order to notice a broken VAPID key.

---

## 3. Medium: `upsert` re-binds an existing endpoint to whoever POSTs it

**Where:** [`db/push_subscription.py:62-72`](../../CheckCheck/backend/checkcheckserver/db/push_subscription.py).

```python
existing = await get_by_endpoint(session, endpoint)
if existing is not None:
    existing.user_id = user_id      # <- no check that it was already this user's
```

`endpoint` is globally unique, and `get_by_endpoint` is not scoped to the caller, so registering an
endpoint that already belongs to account A silently moves the row to account B. The victim stops
receiving push on that device with no signal anywhere, and pushes to their device are from then on
encrypted with the other account's keys.

Two things keep this from being high severity: an endpoint is high entropy, and it is never
disclosed cross-user (`GET /user/me/push-subscriptions` is scoped, and nothing logs it). But the
model docstring explicitly says these fields are *not* secrets
([`model/push_subscription.py:9-15`](../../CheckCheck/backend/checkcheckserver/model/push_subscription.py):
"None of them is a secret in the sense the rest of this codebase uses that word"), and the upsert
then treats possession of the endpoint as authorisation to re-bind it. Those two positions
contradict each other, and the second one is the one with the security consequence.

There is a legitimate case underneath: a shared browser where user B logs in after user A really
should be able to subscribe. That is finding 5, and it deserves an explicit mechanism (the browser
proving it holds the subscription, or an unsubscribe on logout) rather than falling out of an
unscoped upsert.

---

## 4. Medium: no cap on push subscriptions per user

**Where:** [`db/push_subscription.py`](../../CheckCheck/backend/checkcheckserver/db/push_subscription.py)
(no cap anywhere), consumed at
[`notify/outbox.py:536-579`](../../CheckCheck/backend/checkcheckserver/notify/outbox.py).

Every other user-writable table in this feature set has a bound: reminders have
`MAX_PENDING_PER_CARD = 10` and `MAX_PENDING_PER_USER = 200`, mail has
`NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR`, the personal note on an invitation has
`MAX_PERSONAL_MESSAGE_LENGTH`. Push subscriptions have none. One account can register an unbounded
number of rows, and `_deliver_push` loops over all of them **sequentially, in-band on the dispatcher
tick**, each with a 10 second timeout.

So `NOTIFY_PUSH_MAX_PER_USER_PER_HOUR = 20` bounds the number of *rows*, not the number of outbound
requests, which is rows x subscriptions. With finding 1 that is a request amplifier aimed wherever
the attacker likes; without finding 1 it is still a way for one account to stall the shared
dispatcher loop that everybody's mail also goes through.

**Suggested direction:** a module constant in the `MAX_PENDING_PER_USER` style (10 to 20 devices is
generous), enforced in `register_push_subscription` with a 409, plus oldest-`last_seen_at` eviction
if you would rather not fail the call.

---

## 5. Medium: a stale browser subscription dead-ends the Enable button, and logout leaves push live

**Where:**
[`composables/usePushSubscription.ts:71`](../../CheckCheck/frontend/composables/usePushSubscription.ts)
(`isSubscribedHere = !!currentEndpoint`), `:77-86` (`syncCurrentEndpoint`), `:125-143` (`enable`),
[`components/NotificationSettingsModal.vue:273`](../../CheckCheck/frontend/components/NotificationSettingsModal.vue)
(`:disabled="pushIsSubscribedHere"`),
[`utils/localSnapshot.ts:148-197`](../../CheckCheck/frontend/utils/localSnapshot.ts)
(`resetBoardStores` / `clearLocalState`, which do not touch push).

`currentEndpoint` is read from the **browser** (`registration.pushManager.getSubscription()`) and is
never reconciled against the device list the **server** just returned. Two consequences:

1. **Account switch dead-ends.** User A enables push in a browser and logs out. User B logs in on
   the same browser and opens the notification settings: the device list is empty (correctly, the
   row belongs to A), but `getSubscription()` still returns A's subscription, so
   `isSubscribedHere` is true and the Enable button is **disabled**. B cannot enable push on that
   device at all, and there is nothing in the UI explaining why. If B clicks anything that does
   reach `enable()`, it re-POSTs A's endpoint and silently steals it (finding 3).
2. **Logout leaves push live.** `clearLocalState()` drops the snapshot, the cursor and the outbox,
   deliberately, as "explicit-logout hygiene". It does not unsubscribe from push and does not
   `DELETE` the server row. So after logout, A's notification titles (card names, actor names, the
   text of A's own reminders, in `full` content mode) keep arriving on that device's lock screen,
   where whoever is now using the browser can read them. On a shared or work machine that is a real
   disclosure, and it is the exact surface plan decision 3 called "a more exposed surface than an
   email inside an inbox app".

Note that `enable()` also reuses an existing `getSubscription()` result without checking that its
`applicationServerKey` matches the server's current VAPID key, so a key rotation leaves devices
subscribed to a key the server no longer signs with, producing the 403 that finding 2 then turns
into a mass delete.

**Suggested direction:** in `refresh()`, if `currentEndpoint` is set but is not in the returned
device list, either re-register it or unsubscribe it locally, and drive the button off *that*
rather than off the raw browser state; and unsubscribe plus `DELETE` on explicit logout, next to
`clearLocalState()`.

---

## 6. Medium: a newline in a card name or a reminder note dead-letters every mail about it

**Where:** [`notify/transports.py:123`](../../CheckCheck/backend/checkcheckserver/notify/transports.py)
(`mime["Subject"] = email.subject`), subjects built in
[`notify/render.py:141-197`](../../CheckCheck/backend/checkcheckserver/notify/render.py) and
[`notify/invitation.py:82-90`](../../CheckCheck/backend/checkcheckserver/notify/invitation.py),
classification in
[`notify/outbox.py:401-421`](../../CheckCheck/backend/checkcheckserver/notify/outbox.py).

`CheckList.name` is free text with no constraint
([`model/checklist.py:51-53`](../../CheckCheck/backend/checkcheckserver/model/checklist.py)), and
`ScheduledNotification.note` is capped at 200 characters but not filtered. Both are interpolated
straight into an email Subject.

The good news first: this is **not** header injection. Python's `email.policy.default` raises
`ValueError("Header values may not contain linefeed or carriage return characters")` on assignment,
verified against this machine's Python 3.13. The bad news is what happens next: that `ValueError`
is raised inside `transport.send(message)`, and `_deliver_claimed`'s handler is

```python
except Exception as exc:  # includes TransientEmailError
    if not isinstance(exc, TransientEmailError):
        log.exception(...)
    ... _record_transient_failure ...
```

so an error that can never succeed is retried six times with growing backoff and then dead-letters.
Every notification email about that card (and every member of a coalesced group or digest that
includes it, since `_email_from_group` re-renders the whole group) is silently lost, and the only
trace is `log.exception` per attempt.

A user can do this to themselves by pasting a multi-line name; a collaborator with `edit` can do it
to everybody else's mail about that card. The reminder note path means a user can do it to their
own reminders, which is the feature people will trust most.

**Suggested direction:** collapse control characters (`\r`, `\n`, and ideally all C0) to spaces when
a subject is built, in `_subject_for_one` / `_subject_for_many` / `invitation._subject`, since those
are the three places a user string reaches a header. Separately, classifying `ValueError` from
message construction as permanent would stop any future variant of this burning six attempts.

---

## 7. Low: the hourly mail cap counts invitations the user *sent* against mail they *receive*

**Where:** [`db/notification.py:329-345`](../../CheckCheck/backend/checkcheckserver/db/notification.py)
(`_queue_email`'s cap) and
[`notify/outbox.py:875-896`](../../CheckCheck/backend/checkcheckserver/notify/outbox.py)
(`count_recent_for_user`).

`count_recent_for_user` counts every `notification_outbox` row on the email channel with that
`user_id` in the last hour, whatever produced it. Three producers write rows keyed on a user id that
is the **sender**, not the recipient:

* `POST /checklist/{id}/public-share/email`
  ([`routes_checklist_share.py:1235`](../../CheckCheck/backend/checkcheckserver/api/routes/routes_checklist_share.py),
  `user_id=sender.id`),
* `POST .../test-email`,
* and, less importantly, rows the user's own digests already produced.

With the default `NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR = 20` and
`SHARING_PUBLIC_LINK_EMAIL_MAX_PER_HOUR = 10`, a user who mails out ten public links and fires a few
test mails has spent most of an anti-flood budget that exists to protect their own inbox, and
notifications addressed *to* them are then dropped (not delayed: `_queue_email` returns without
queueing) for the rest of the hour, with only a `log.warning` to show for it.

**Suggested direction:** count only rows that represent mail *to* this user, e.g. add
`.where(NotificationOutbox.notification_id.is_not(None))` to the cap query, or give the invitation
rows a `user_id` of `None` and track the sender in the payload. The first is one line and keeps the
existing per-sender limit doing its own separate job.

---

## 8. Low: the drain deletes a dead subscription by `endpoint`, not by id

**Where:** [`notify/outbox.py:561`](../../CheckCheck/backend/checkcheckserver/notify/outbox.py)
calling
[`db/push_subscription.py:119-122`](../../CheckCheck/backend/checkcheckserver/db/push_subscription.py).

The loop holds `subscription.id` and uses `subscription.endpoint` to delete. Today those select the
same row, but combined with finding 3 (an endpoint can be re-bound between the drain reading the
list and the delete committing) the drain can delete a row that now belongs to somebody else. It is
also just less precise than the code immediately around it, which logs `subscription.id`.

`delete_by_endpoint` has no other caller, so this is a free change: delete by primary key.

---

## 9. Low: the VAPID pair is not checked for correspondence at boot

**Where:** [`notify/push.py:227-255`](../../CheckCheck/backend/checkcheckserver/notify/push.py)
(`validate_vapid_keys`), called from
[`config.py:1056-1066`](../../CheckCheck/backend/checkcheckserver/config.py).

The check validates the public key's shape (65 bytes, leading `0x04`), the private key's shape (32
bytes) and that `py_vapid` can load the private key. It never checks that the public key is the one
derived from that private key. An operator who pastes a public key from one `gen_vapid_keys.sh` run
and a private key from another boots cleanly, hands the mismatched public key to every browser as
`applicationServerKey`, and then every push is rejected by the push service, which under finding 2
deletes every subscription.

The error message the config raises even claims otherwise: "VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY are
not a usable key pair" is only ever about one of them individually.

**Suggested direction:** derive the public point from the loaded private scalar
(`Vapid02.from_string(...).public_key.public_bytes(...)` in uncompressed form) and compare. Six
lines in a function that already imports the machinery, and it converts a silent mass-unsubscribe
into a startup failure.

---

## 10. Nit: `?card=` is interpolated into a route path unvalidated

[`utils/notificationDeepLink.ts:101`](../../CheckCheck/frontend/utils/notificationDeepLink.ts):
`await ctx.replace({ path: `/card/${link.cardId}`, query: link.cardQuery })`. `cardId` is whatever
the URL carried. It stays same-origin (it is a router path, not a URL), so this is a nit rather than
an open redirect, but a UUID shape check before the rewrite costs nothing and keeps a crafted link
from producing a confusing route.

## 11. Nit: the unsubscribe `form_action` is HTML-escaped but not URL-encoded

[`routes_notification_settings.py:823`](../../CheckCheck/backend/checkcheckserver/api/routes/routes_notification_settings.py):
`form_action=f"{unsubscribe.UNSUBSCRIBE_PATH}?token={token}"`. Jinja's autoescape handles the HTML
context (so no XSS), and a real token is base64url plus one `.`, so nothing breaks today. But the
token is caller-controlled and reaches a URL context without `quote()`; `unsubscribe_url()` does
call `quote()` for the same value, so the two paths disagree.

## 12. Nit: truncated comment in `ReminderScanResult`

[`notify/reminders.py:76`](../../CheckCheck/backend/checkcheckserver/notify/reminders.py):
`cancelled: int = 0  # rows whose card is gone, or whose access is` — the sentence stops mid-clause.

---

## 13. Test gaps

The three suites are strong on the paths the plans named; the gaps line up almost exactly with the
findings above, which is the usual pattern (nobody writes the test for the case they did not think
of). Listed roughly in the order I would write them.

**Backend, push (`tests_notification_push.py`, 20 cases today):**

1. **No test that a subscription cannot be hijacked across accounts.** There is
   `test_delete_only_removes_the_owners_own_subscription`, which is the delete half of the same
   question; the register half is missing, and the current behaviour is takeover (finding 3).
2. **No test of endpoint validation**, because none exists (finding 1). The test to write is "an
   endpoint that is not https, or resolves into a private range, is refused", mirroring
   `tests_notification_webhooks.py`'s `169.254.169.254` / `127.0.0.1` cases, which are the model to
   copy.
3. **No test that a non-404/410 4xx leaves the subscription in place.**
   `test_send_sync_classifies_push_service_status_codes` covers the classification in isolation but
   asserts the current (in my reading wrong) mapping; nothing covers what `_deliver_push` then does
   to the row for a 401 or 413 (finding 2).
4. **No test of a per-user subscription cap** (none exists, finding 4).
5. **No test of the boot check on a mismatched-but-individually-valid key pair** (finding 9).
   `test_boot_accepts_a_real_vapid_key_pair` would pass today with two unrelated halves.

**Backend, email:**

6. **No test that a subject containing a newline still delivers** (finding 6). The natural place is
   `tests_email_templates.py`, next to the escaping cases.
7. **No test that the hourly cap ignores mail the user sent rather than received** (finding 7).
   `tests_public_link_email.py` covers the per-sender limit; nothing covers the interaction with
   `NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR`.

**Frontend:**

8. **`composables/usePushSubscription.ts` has no unit test at all.** `tests/unit/push.spec.ts`
   covers the pure functions in `utils/push.ts` (13 cases, good ones), but `enable()`, `refresh()`
   and `disable()` are where finding 5 lives, and the composable is mockable (the store, the
   `navigator.serviceWorker` shim and `Notification` are all injectable the way the E2E specs
   already stub them).
9. **No coverage of the account-switch case**: browser holds a subscription the current user does
   not own. This is the one I would most want, in either vitest or Playwright, because the symptom
   (a permanently disabled Enable button) is silent.
10. **No coverage of logout hygiene for push**, matching the existing account-switch tests for the
    snapshot and outbox.

**Documented and accepted, listed for completeness:**

11. **Chunk T (time zone re-sync on login) is not started**, per the plan's own progress table, so
    its two tests are outstanding by design. Note that decision 4 also required a line of copy next
    to the picker ("kept in sync with this device"); since T has not shipped, the modal correctly
    does not claim it. Worth re-reading decision 4 before T lands: the note says every login
    silently overwrites a deliberately pinned zone, and reminders snapshot their zone at creation
    (decision 5), so a re-sync will not move existing recurring reminders but will move the daily
    digest hour.
12. **No end-to-end round trip against a real push service.** Correctly out of scope and stated as
    such in both the P1 and P2 handoffs.

---

## 14. Questions for you

1. **Finding 1, scope of the fix.** Public-address guard only (reuses `webhooks.py`, accepts any
   host that is not private), or an operator-configurable allowlist of known push-service hosts
   (stricter, but breaks a self-hosted push relay and needs a config setting plus docs)? My
   recommendation is the guard now and the allowlist never, unless you know of a deployment that
   needs the strictness.
2. **Finding 2 vs 4, what should a 413 do?** Deleting the subscription is clearly wrong; failing the
   row is my suggestion. But it may be worth truncating the push payload at render time instead,
   given `NOTIFY_PUSH_CONTENT_MODE: full` plus a long card name plus a long reminder note is a
   plausible ordinary case rather than an attack. Do you want a length budget in `push_payload`?
3. **Finding 5, what should logout do to a push subscription?** Unsubscribe the browser and delete
   the row (cleanest, but a user who logs out and back in has to re-enable), or keep the row and
   only clear it on an account *switch* (finding 5.1) while accepting the shared-device disclosure
   (finding 5.2)? This is a genuine product trade-off and I do not think the plan settled it.
4. **Finding 3.** Given the answer to 3, is re-binding an endpoint to a new account something you
   want at all, or should a re-registration for an endpoint owned by someone else be a 409 and let
   the browser resubscribe with a fresh endpoint?
5. **Finding 6.** Sanitise the subject (my suggestion), or constrain `CheckList.name` at the model
   level? The second is a wider change with sync and migration implications, but "a card name is one
   line" may be a rule you want anyway.
6. **Priority.** If you want to ship the email and reminder features before the push channel, the
   cheapest safe move is `NOTIFY_PUSH_ENABLED: false` (which it already is by default) and treating
   findings 1 to 5 as a P3 chunk. Findings 6 and 7 are the only ones that affect an
   email-only instance, and neither is a blocker.
