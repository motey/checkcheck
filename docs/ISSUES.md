# Known issues

Running log of known bugs / rough edges that are out of scope for the change
that discovered them. Newest first.

---

## Checklist ITEMS won't reorder via touch drag (cards work) — synthetic drag inside the editor UModal

**Status:** fix applied, awaiting on-device verification · **Severity:** medium · **Discovered:** 2026-07-18

**Fix applied (2026-07-18) — longPress on items, awaiting on-device check**

Reading the *installed* `@formkit/drag-and-drop@0.6.1` source narrowed the
diagnosis away from the transform:
- The synth clone is a **top-layer popover** (`popover="manual"` + `showPopover()`,
  [index.mjs:2358](../CheckCheck/frontend/node_modules/@formkit/drag-and-drop/index.mjs#L2358)),
  not a plain `position:fixed` element — a transformed ancestor can only offset
  where the clone *renders* (cosmetic).
- Sort hit-testing uses **raw viewport coords**: `getElFromPoint` →
  `document.elementFromPoint(clientX, clientY)`
  ([index.mjs:2915](../CheckCheck/frontend/node_modules/@formkit/drag-and-drop/index.mjs#L2915)),
  with the clone `pointer-events:none` so it's skipped. That path is
  transform-independent, so the transform alone can't explain "nothing sorts".

The one config difference from the working board cards is `longPress`. Without
it, the synth drag arms on the **first** pointermove
([index.mjs:1305](../CheckCheck/frontend/node_modules/@formkit/drag-and-drop/index.mjs#L1305)),
which the nested `overflow-y-auto` scroll container tends to claim as a scroll
instead. So the applied fix mirrors the card path in
[CheckListItemCollection/index.vue](../CheckCheck/frontend/components/CheckListItemCollection/index.vue):
`longPress:true`, `longPressDuration:250`, `longPressClass:"list-item-longpress"`
(+ a subtle background-only press cue — no scale, to avoid adding a second
transformed ancestor for the clone). Desktop native mouse drag returns early
before the longPress gate ([index.mjs:1293](../CheckCheck/frontend/node_modules/@formkit/drag-and-drop/index.mjs#L1293)),
so `item-movement.spec.ts` is unaffected.

**Verify on a real phone / DevTools touch:** open the card editor, press-and-hold
an item's grip (~250 ms, row highlights), then drag — it should sort. A normal
touch-drag on the list should still scroll. If it still won't sort, fall back to
neutralising the modal transform (option 3 below).

**Original status:** open · **Discovered:** 2026-07-18

**Symptom**

On a mobile/touch device, dragging a checklist **item** inside the open card
editor does not work: you can grab the item by its grip handle, but it will not
snap into any other slot — nothing moves/sorts. Dragging whole checklist **cards**
on the board *does* work on touch (fixed 2026-07-18 via longPress — see memory
`mobile-dnd-longpress`). Item reorder on **desktop** (mouse) also works
(`tests/e2e/item-movement.spec.ts` passes).

**Diagnosis (strong hypothesis, not yet on-device-confirmed)**

Items are the only draggable surface rendered **inside a modal**:
[CheckListEditModal.vue](../CheckCheck/frontend/components/CheckListEditModal.vue)
wraps the card in Nuxt UI's `UModal` (Reka UI dialog). The modal content sits
under a CSS **transform** (centering/animation). `@formkit/drag-and-drop`'s
touch path uses a **synthetic drag**: it appends a `position: fixed` clone and
hit-tests with `document.elementFromPoint` to decide sort targets. A transformed
ancestor breaks both — `fixed` becomes relative to the transformed element and
the pointer→element math is offset — so `validateSort`/`sort` never fires. This
matches the observed matrix exactly:

| surface | path on touch | in transformed modal? | works? |
|---|---|---|---|
| board cards | synthetic (longPress) | no | ✅ |
| items, desktop | **native** mouse drag (no clone) | yes | ✅ |
| items, touch | synthetic (grip handle) | **yes** | ❌ |

Native mouse drag has no clone/fixed-positioning, so the transform doesn't
affect it — which is why desktop item reorder passes but touch fails.

Secondary suspect: the item list is also inside a nested scroll container —
`flex-1 min-h-0 overflow-y-auto overscroll-contain` at
[CheckList.vue:37](../CheckCheck/frontend/components/CheckList.vue#L37) — which
can compound synthetic-drag coordinate/scroll issues. Rule this in/out after the
transform theory.

**Current item DnD config** (no longPress; grip handle only):
[CheckListItemCollection/index.vue:98](../CheckCheck/frontend/components/CheckListItemCollection/index.vue#L98)
— `dragHandle: ".list-item-drag-handle"`, `plugins:[animations()]`. The handle
span has `touch-none select-none`
([CheckListItem.vue:6](../CheckCheck/frontend/components/CheckListItem.vue#L6)).

**Fixes to try (cheapest first; each needs on-device verification — see below)**

1. **Confirm the transform theory quickly:** on a phone (or DevTools device mode
   with touch), open the editor, and while dragging an item inspect whether the
   synthetic clone is offset from the finger / lands in the wrong place. Or
   temporarily remove the modal transform (e.g. `transform: none` on the UModal
   content) and see if item drag starts sorting.
2. **Tell FormKit where to append the synthetic clone / hit-test root.** Check the
   installed `@formkit/drag-and-drop` config for a `root` / synthetic-parent /
   `synthDragScrolling` type option (grep `dist`/`index.mjs` for `root`,
   `insertPoint`, `getRootNode`, `appendTo`). If the clone can be appended inside
   the (transformed) modal content or the transform accounted for, sorting should
   resume. This is the most likely *correct* fix.
3. **Neutralise the transform** on the specific ancestor the clone/positioning
   depends on (a transformed scroll/positioning context is the actual breaker),
   without breaking the modal's centering/animation.
4. **Lower-value fallbacks:** add `longPress` to items to match cards (unlikely to
   fix the sort — longPress only gates *when* the drag arms, not the clone math),
   and/or widen `touch-action: none` beyond the grip.

**How to test** (touch reorder is NOT automatable — see below)

There is a `mobile` Playwright project (Pixel 7) + `tests/e2e/touch-movement.spec.ts`,
but it deliberately only asserts tap-opens-editor and that press-hold *arms* the
card drag. **Full touch drag-to-reorder cannot be reproduced in Playwright**:
FormKit's synthetic sort only advances for a real device's pointer-before-touch
event ordering — neither hand-dispatched PointerEvents (moves reach `synthMove`
but the `remap`/`currentTargetValue` state machine never lands a sort) nor CDP
`Input.dispatchTouchEvent` reproduce it. So verify this fix by **hand on a real
phone** (or Chrome DevTools device-mode touch emulation). Desktop item reorder is
still guarded by `item-movement.spec.ts`; keep it green.

**References**
- Memory: `mobile-dnd-longpress` (Phase 1 card fix + the two library gotchas:
  lib sets no `touch-action` itself; longPressClass only added on a *cancelable*
  pointerdown), `flaky-e2e-dnd-sharing`.
- Key files: `CheckListItemCollection/index.vue` (item DnD config),
  `CheckListItem.vue` (grip handle), `CheckListEditModal.vue` (UModal wrapper),
  `CheckList.vue` (scroll container), `CheckListBoard.vue` (working card longPress
  reference), `playwright.config.ts` (`mobile` project), `touch-movement.spec.ts`.

---

## Client stays "Offline" after the server recovers — SSE never reconnects

**Status:** resolved (2026-07-18) · **Severity:** medium · **Discovered:** 2026-07-18

**Resolution**

Added a manual, capped-backoff reconnect to the `/api/sync` `EventSource` in
[useSync.ts](../CheckCheck/frontend/composables/useSync.ts). `onerror` now
distinguishes the two close states: `readyState === CONNECTING` is a transient
blip the browser is already retrying (log only, unchanged), while
`readyState === EventSource.CLOSED` is the *permanent* HTTP-error close (a 502/503
from a bounced backend behind Traefik) the browser will never retry — that now
schedules a `disconnect()` + `connect()` rebuild on an exponential backoff
(`1s → 30s` cap). A successful `onopen` clears the pending timer and resets the
backoff to its floor, restoring the `setConnectivity(true)` path so the outbox
resumes draining and the online-only surfaces (WI-12) re-enable without a reload.

The rebuild preserves `hasOpened` across the reconnect (it is otherwise reset by
`disconnect()`), so the first `onopen` after recovery runs the reconcile
delta-pull — catching up on everything that changed while the server was down —
rather than treating the reconnect as a fresh initial load. `disconnect()` also
clears any pending reconnect timer so an explicit teardown can't leave a rebuild
in flight.

**Symptom**

When the backend is briefly unreachable while the browser's own network
interface stays up (a redeploy / container restart behind Traefik is the common
case), the client flips to "Offline" and **never returns to "Online" once the
server is back** — until a manual page reload. While stuck, the outbox stops
draining and the online-only surfaces (share / invite / notifications, WI-12)
stay disabled. Reproduced by watching the tab in the foreground across a server
bounce.

**Root cause**

Recovery from a *server-only* outage depends entirely on the `/api/sync`
`EventSource` auto-reconnecting and firing `onopen`
([useSync.ts:80](../CheckCheck/frontend/composables/useSync.ts#L80) →
`setConnectivity(true)`). But `EventSource` only auto-reconnects after a
*network-level* drop or a clean stream end. When the stream fails on an **HTTP
error status** — exactly what a down backend behind Traefik returns (502/503) —
the spec requires the browser to fail the connection *permanently*: `onerror`
fires once, `readyState` goes to `CLOSED`, and it never retries.
[`es.onerror`](../CheckCheck/frontend/composables/useSync.ts#L109) sets
connectivity `false` on the stated assumption that "`onopen` flips it back true
on reconnect" — but for an HTTP-error close there is no reconnect: nothing
inspects `readyState === CLOSED` to re-create the stream, and there is no
periodic reconnect/probe timer. The window `online` event can't rescue it either
(the interface never went down). The only recovery paths are a full reload or the
`visibilitychange → probe()` in
[`onVisible`](../CheckCheck/frontend/composables/useSync.ts#L60), which never
fires while the tab stays in the foreground — the reported scenario.

**Fix direction**

In `onerror`, detect `es.readyState === EventSource.CLOSED` and schedule a manual
reconnect (`disconnect()` + `connect()`) on a capped backoff; and/or run a
periodic `probe()` while the signal is `false` that re-`connect()`s on the first
success. Either restores the `onopen → setConnectivity(true)` path for a
server-only outage without a reload. (Related cosmetic effect: `server_version`
in the sidebar is fetched once per page load and memoized
([publicConfig.ts:40](../CheckCheck/frontend/stores/publicConfig.ts#L40)), so an
open tab shows the version as-of last load; a working reconnect/refresh would let
it track the running server too.)

---

## Sharing a card breaks pinning — `PATCH /checklist/{id}` returns another user's position

**Status:** resolved (2026-07-12) · **Severity:** high · **Discovered:** 2026-07-12

**Resolution**

Two-part fix in [routes_checklist.py](../CheckCheck/backend/checkcheckserver/api/routes/routes_checklist.py):

1. `update_checklist` (`PATCH /checklist/{id}`) now re-scopes the returned
   `CheckList.position` to the caller via `CheckListPositionCRUD.get(...)`, exactly
   as `get_checklist` already did — this was the missed sibling of the 2026-07-06
   `get_checklist` fix below.
2. Both routes now use `set_committed_value(obj, "position", user_position)` instead
   of plain attribute assignment. The `position` relationship has delete-orphan
   cascade, so `obj.position = user_position` orphaned the arbitrary joined-loaded
   row and **deleted/NULLed another user's position on the next autoflush**,
   corrupting their pinned/archived/index. `set_committed_value` overrides the
   loaded value for serialization without dirtying the session.

Deterministic Postgres regression test:
`test_patch_shared_checklist_returns_callers_own_position` in
`tests/tests_shared_position_scope.py` (collaborator edits a card the owner has
pinned; pre-fix the response leaks the owner's `pinned=True`, and the owner's own
next edit loses its position to the orphan corruption).

**Symptom**

Create three checklists, share the second → pinning any checklist appears broken.
The single-card sibling below (`get_checklist`, resolved 2026-07-06) covered the
`share_added` → `GET /checklist/{id}` refresh path, but the `PATCH` path stayed
unscoped, and the plain-assignment re-scope in both routes could orphan-delete a
sibling user's position row.

**Root cause**

Same `CheckList.position` `lazy="joined"`, `uselist=False`, per-user hazard as the
issue below — an unscoped eager-load collapses N per-user rows into one slot and
picks arbitrarily. Only `get_checklist` had been re-scoped; `update_checklist` had
not. Postgres makes the arbitrary pick genuinely undefined (SQLite masked it).

---

## Systemic: per-user relationship reassignment on shared cards (orphan-delete + unscoped serialization)

**Status:** resolved (2026-07-12) · **Severity:** high · **Discovered:** 2026-07-12

**Resolution**

Centralised the per-user position re-scope in one helper,
`access.scope_position_to_caller`, which always uses `set_committed_value` (never
plain assignment), and routed every checklist-returning site through it:

- `routes_checklist_public.py` — `get_public_checklist` (owner scope) and
  `join_public_checklist` (joiner scope).
- `routes_checklist_share.py` — `accept_invite` (accepter scope).
- `routes_checklist.py` — the `create_checklist` idempotent-replay branch, plus
  `get_checklist` / `update_checklist` refactored onto the helper (they already
  used `set_committed_value` inline).

**Root-cause clarification found while fixing**

The orphan DELETE only *persists* when a **commit follows** the plain reassignment
in the same request. The base CRUD `create`/`update` commit, so `update_checklist`
(which does `sync_crud.create` after re-scoping) deterministically corrupted the
owner's row — that path was the one already fixed, and is covered deterministically
by `tests/tests_shared_position_scope.py`. The remaining sites (public get/join,
invite accept, create-replay) re-scope but do **not** commit afterward, so their
orphaned DELETE was rolled back at session close: a latent landmine, not active
corruption. The helper removes the landmine regardless of what runs afterwards.

The `labels` reassignment carries no delete-orphan hazard (`cascade_delete=False`,
link-model M2M) and was already the last statement before return on every site, so
its dirty state is never flushed; left as-is.

New invariant test `tests/tests_shared_position_orphan.py` locks per-user position
scoping across the three read/join/accept routes (each user keeps their own
pinned/index; the re-scope neither leaks nor disturbs a sibling row).

**Original scope**

The prior fix hardened only `get_checklist` and `update_checklist`. The same
pattern — re-scoping a shared card's per-user `position` by **plain assignment**
onto a session-attached ORM object — appeared in several more routes and carried
the same hazards:

- **Orphan-delete corruption:** `position` has delete-orphan cascade, so
  `checklist.position = user_position` orphans the arbitrarily joined-loaded row
  and deletes/NULLs *another user's* position once a commit follows.
- **Unscoped leak:** any checklist-returning route that does NOT re-scope position
  serves another user's pinned/archived/index for shared cards.

**Suspect sites (all audited/fixed)**

- `routes_checklist_public.py` `get_public_checklist`, `join_public_checklist`.
- `routes_checklist_share.py` `accept_invite`.
- `routes_checklist.py` create idempotent-replay branch.
- `create_checklist`'s main path assigns a freshly-created card's only position
  row (no cross-user sibling), so it was left as plain assignment.

---

## Username validation rejects underscores — breaks OIDC provisioning

**Status:** resolved (2026-07-12) · **Severity:** medium · **Discovered:** 2026-07-12

**Resolution**

Took the low-risk option: added `_` to the allowed set on both `UserRegisterAPI`
and `_UserWithName` — `^[a-zA-Z0-9._-]+$`
([user.py:60](../CheckCheck/backend/checkcheckserver/model/user.py#L60),
[user.py:106](../CheckCheck/backend/checkcheckserver/model/user.py#L106)). This
unbreaks the `PREFIX_USERNAME_WITH_PROVIDER_SLUG` `f"{slug}_{user_name}"` prefix
(which was violating its own pattern) and OIDC usernames containing underscores.
Regression test `test_username_with_underscore_is_accepted_and_can_log_in` in
`tests/tests_auth.py` creates an underscore user and logs in as them.

**Symptom**

The `user_name` constraint is `pattern=r"^[a-zA-Z0-9.-]+$"` (letters, digits, dot,
hyphen) on both `UserRegisterAPI` and `_UserWithName`
([user.py:60](../CheckCheck/backend/checkcheckserver/model/user.py#L60),
[user.py:106](../CheckCheck/backend/checkcheckserver/model/user.py#L106)). It
rejects underscores (and spaces, `@`, unicode). Underscore is URL-safe and a very
common username character, so allowing `.`/`-` but not `_` is an arbitrary,
undocumented inconsistency.

**Why it's more than cosmetic**

OIDC provisioning builds a real `UserCreate` from the IdP's `preferred_username`
via `UserCreate.from_oidc_userinfo`
([user.py:137](../CheckCheck/backend/checkcheckserver/model/user.py#L137)), which
inherits the same pattern. External IdPs (Keycloak, Azure AD, …) routinely issue
usernames containing underscores, so those users would fail Pydantic validation
and **could never log in**. Worse, the app's own
`PREFIX_USERNAME_WITH_PROVIDER_SLUG` option constructs `f"{slug}_{user_name}"`
([user.py:145](../CheckCheck/backend/checkcheckserver/model/user.py#L145)) — with
an underscore — which then violates its own pattern, so enabling that option would
break every OIDC login.

**Suggested fix**

Either add `_` to the allowed set (`^[a-zA-Z0-9._-]+$`) — the low-risk option that
also unbreaks the provider-slug prefix — or sanitize/relax the pattern on the OIDC
path specifically. Add a regression test creating a user (and an OIDC login) with
an underscore username.

---

## Sidebar count badges: `shared_by_me` not adjusted on the actor's own archive

**Status:** resolved (2026-07-12, Chunk B1) · **Severity:** low · **Discovered:** 2026-07-11 (WI-15, flag-on flip)

**Resolution**

Took option (b). The outbox now flags a drain as `countsDirty` when a
count-affecting op replays (`affectsSidebarCounts` — card create/delete/archive
and label attach/detach), carries that on its `idle` event, and `useSyncNotices`
fires one debounced `fetchCounts` (server truth) per dirty drain. That refetch is
exact for every bucket including `shared_by_me`, so the actor's own archive
reconciles the badge on drain without needing a `collaborator_count` DTO field.
Same mechanism also fixed the broader create/delete/label counts staleness
(Chunk B1 in `docs/archive/2.0_REVIEW_FINDINGS.md`).

**Symptom**

Under the local-first default, archiving/unarchiving one of *your own* cards
that you have shared can leave the "Shared by me" sidebar badge off by one until
the next unrelated counts refetch.

**Root cause**

A delta pull that *confirms the actor's own* optimistic change is blind to it:
the optimistic update already moved the local field (`position.archived`), so
`mergeDelta` sees `existing == merged`, never sets `cardLevelChanged`, and skips
the `fetchCounts`. So the actor's own edits can't refresh the sidebar counts via
the delta path — they must be adjusted optimistically at the action site.
`stores/checklist.ts::_adjustCountsForArchive` does this for `home`, `archived`,
`labels`, and `shared_with_me` (keyed off `owner_id`), but **`shared_by_me`**
needs "does this card have ≥1 collaborator", which the `CheckListApiWithSubObj`
DTO doesn't carry, so it is left for the next absolute `fetchCounts` (another
user's edit, a reload) to reconcile.

**Impact**

Minor and self-healing — off-by-one on one badge for your own cards until any
counts refetch. No data loss.

**Suggested fix**

(a) Add a `collaborator_count`/`is_shared` flag to the card DTO so the client can
adjust `shared_by_me` too; or (b) trigger one `fetchCounts` (server truth) when
the archive outbox op drains — exact for every bucket, at the cost of a request.

## Shared-card listing eager-loads an arbitrary user's `CheckListPosition`

**Status:** resolved (2026-07-05) · **Severity:** low-to-medium · **Discovered:** 2026-06-23

**Resolution**

`CheckListCRUD.list(...)` now scopes the position eager-load to the caller with
`with_loader_criteria(CheckListPosition, CheckListPosition.user_id == user_id)`
alongside the `selectinload(CheckList.position)`
([checklist.py:280](../CheckCheck/backend/checkcheckserver/db/checklist.py#L280)),
so each viewer's listing embeds their own position row (no more `uselist=False`
warning / arbitrary pick). Regression test:
`test_list_checklists_returns_own_position_on_shared_card` in
`tests/tests_sharing.py`.


**Symptom**

Listing a shared checklist logs:

```
SAWarning: Multiple rows returned with uselist=False for eagerly-loaded
attribute 'CheckList.position'
```

**Where**

- `CheckListCRUD.list(...)` eager-loads the position with
  `selectinload(CheckList.position)` —
  [CheckCheck/backend/checkcheckserver/db/checklist.py:254](../CheckCheck/backend/checkcheckserver/db/checklist.py#L254).
- `CheckList.position` is a one-to-one (`uselist=False`) relationship, but
  `CheckListPosition` is **per-user**: a card shared with N users has N position
  rows (one per collaborator + the owner).

**Root cause**

The `selectinload(CheckList.position)` is **not user-scoped**, so for a shared
card it loads every user's position row and then collapses them into the single
`uselist=False` slot — SQLAlchemy warns and picks one row arbitrarily.

The access query (`_add_user_has_access_query`) *does* inner-join
`CheckListPosition` scoped to the current user (for filtering/ordering), but that
scoping is not carried into the eager-load of the `position` relationship.

**Impact**

For a shared card, the `position` returned to the caller (archived / pinned /
index) may be **another user's** position rather than the caller's. This can
surface as wrong pinned/archived state or ordering on shared cards. It is
pre-existing (any shared-card list triggers it) and was only made more visible
by the new `?shared=with_me|by_me` filters, which list shared cards directly.

**Suggested fix**

Scope the position eager-load to the current user, e.g. replace the unscoped
`selectinload(CheckList.position)` with a per-user loader criteria
(`with_loader_criteria(CheckListPosition, CheckListPosition.user_id == user_id)`)
or load the caller's position explicitly and attach it, mirroring how labels are
already re-scoped per user in the `list_checklists` route. Add a regression test
that lists a card shared with two users and asserts each caller sees **their own**
position (distinct pinned/archived/index).

## Sharing a card with another user removes the pin for the sharing user

**Status:** resolved (2026-07-06) · **Severity:** medium · **Discovered:** 2026-07-06

**Resolution**

`get_checklist` (`GET /checklist/{id}`) now re-scopes the returned
`CheckList.position` to the caller before responding — it loads the caller's own
`CheckListPosition` via `CheckListPositionCRUD.get(...)` and assigns it, mirroring
`accept_invite` and the user-scoped eager-load already used in
`CheckListCRUD.list(...)`
([routes_checklist.py:266](../CheckCheck/backend/checkcheckserver/api/routes/routes_checklist.py#L266)).
Regression test: `test_get_checklist_returns_own_position_on_shared_card` in
`tests/tests_sharing.py`.

**Symptom**

When having a pinned checklist and sharing this to another user, the card is not
pinned anymore in the moment the share is added. Also it is not possible anymore
to pin the card anymore. The receiving user still can pin the new card.

**Root cause**

`CheckList.position` is a scalar (`uselist=False`) `lazy="joined"` relationship,
but `CheckListPosition` is **per-user**: a shared card has N position rows. The
base `CheckListCRUD.get(...)` used by `get_checklist` did not scope the eager-load
to the caller, so SQLAlchemy collapsed all N rows into the single slot and picked
one **arbitrarily** — often the fresh collaborator's `pinned=False` row.

The frontend refreshes a single card via `GET /checklist/{id}` whenever it
receives a `share_added` or `checklist_position` SSE event
([useSync.ts](../CheckCheck/frontend/composables/useSync.ts), `checkListStore.refresh`).
So the moment the owner shared the card (or tried to re-pin it, which re-broadcasts
`checklist_position`), the owner's client overwrote its correct in-memory pinned
state with another user's arbitrary position — the card kept unpinning.

This is the single-card sibling of the already-fixed `list()` eager-load bug
above; that fix never reached the `get_checklist` path.


## Logo and create button not distinguishable on mobile

**Status:** resolved (2026-07-18) · **Severity:** low · **Discovered:** 2026-07-18

**Resolution**

The navbar logo is now hidden on mobile (`hidden md:flex`) at
[Navbar.vue:17](../CheckCheck/frontend/components/Navbar.vue#L17). The mobile bar
reduces to hamburger · search · create — three distinct controls with no
button-like decorative tile competing with the create button. Branding is
retained in the slide-menu drawer header
([SideMenuDrawer.vue:10](../CheckCheck/frontend/components/SideMenuDrawer.vue#L10)),
where the logo is a real home link.

**Symptom**

My first test user told me, the he was not sure that the logo was a logo or an button.

**Root cause**

On mobile the logo rendered as a colored rounded tile with a `list-checks` icon
sitting immediately next to the emerald `list-plus` create button — two similar
colored icon-tiles. Worse, the navbar logo was not wrapped in a link, so it
looked tappable but did nothing.