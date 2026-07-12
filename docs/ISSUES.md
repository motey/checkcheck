# Known issues

Running log of known bugs / rough edges that are out of scope for the change
that discovered them. Newest first.

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
(Chunk B1 in `docs/plans/2.0_REVIEW_FINDINGS.md`).

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
