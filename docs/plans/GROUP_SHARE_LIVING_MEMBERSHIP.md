# Plan: first-class, living group shares

**Status:** planning · **Owner:** — · **Created:** 2026-07-19

Turn the ShareModal's "Share with a group" from a one-shot expansion into a
first-class, persistent, *living* share — like the per-user "Invite specific
people" surface: an add-box **plus** a list of the groups a card is shared with,
each at its own permission, removable as a unit.

This is a multi-session change. It touches the core access model and ships a
migration onto a **running productive instance**, so it is phased so each phase
is independently reviewable and the DB change is isolated and reversible.

---

## Why (the gaps today)

`ShareModal/ShareWithGroup.vue` lets the owner pick **one** OIDC group + **one**
level and click Share. The backend
(`routes_checklist_share.py::share_with_group`) **expands** the group to its
current members and writes an ordinary `CheckListCollaborator` row per member — a
**snapshot**. Nothing records that the card is "shared with group X", so:

- there is no list of shared groups (unlike `PeopleList` for people),
- you cannot revoke a group as a unit,
- new members who join the group later never gain access (must re-share),
- you can only act on one group at a time.

## What we're building (decided with the maintainer)

- **Living membership.** The group share is the source of truth; a member of the
  group has access as long as the share exists and they are in the group. New
  members gain access; leavers lose it.
- **Revoke = remove the members it granted, keep individual shares.** Revoking a
  group share removes access for members who only had it *via* the group; anyone
  with an explicit individual share keeps that.

### The key constraint that shapes the design

Every access-scoped read — the grid, counts, the delta feed, `list_access_ids`,
`user_has_checklist_access` — funnels through
`CheckListCRUD._add_user_has_access_query`, which requires **both**:

1. `owner_id == user` **OR** a `CheckListCollaborator` row for the user, **and**
2. a per-user `CheckListPosition` row (inner join — this is what hides pending
   invites and is what puts a card on someone's board).

So "living membership" cannot be a purely query-derived side-channel: a card only
appears on a member's board if that member has a **position row**, and their
`my_permission` / delta-feed visibility only work if they have a **collaborator
row**. The pragmatic, low-risk realization is therefore **materialize +
reconcile**: keep the group share as source of truth, and **materialize**
`CheckListCollaborator` + `CheckListPosition` rows for current members,
reconciled at the moments group membership actually changes. Every existing
sync/access/notification path then keeps working unchanged because it still sees
ordinary collaborator + position rows.

### When does "current membership" change, and where we reconcile

`user.oidc_groups` is a JSON snapshot persisted on the User, rewritten on **every
OIDC login** (`routes_auth.py` callback: `user_crud.update(... oidc_groups=...)`)
— that is the only moment an OIDC group membership change reaches this app. So
the reconciliation triggers are:

1. **On login**, right after `oidc_groups` is (re)written — reconcile *this user's*
   group-derived access across all cards (grant newly-qualified, remove
   no-longer-qualified). Only run when the group set actually changed.
2. **On group-share create/update** — expand the group's current members and
   materialize (this is the immediate-effect path the owner sees).
3. **On group-share revoke** — remove group-derived rows for that group's members
   who are not still justified by another group share and hold no explicit share.

This is "as live as the data source allows": OIDC membership only propagates at
login anyway, so reconcile-on-login is the correct granularity. Document this
clearly in the UI copy and `SYNC_PROTOCOL`/`administration` docs.

---

## Data model

### New table `checklist_group_share` (`model/checklist_group_share.py`)

Source of truth for "card C is shared with group G at level L".

| column | type | notes |
|---|---|---|
| `checklist_id` | UUID | FK `checklist.id`, **PK**, `ondelete="CASCADE"` |
| `group` | str | **PK** (composite with `checklist_id`) |
| `permission` | `SharePermission` | `sa_type=String` (view/check/edit) |
| `created_by` | UUID | FK `user.id` — the owner who shared |
| `created_at`/`updated_at` | naive-UTC | via `TimestampedModel` |

- **No `server_seq`.** It is a config/source table; access changes reach clients
  through the *materialized* collaborator/position rows, which already
  participate in the delta feed. Keeping it out of `server_seq` avoids touching
  the seq-stamping mapper events.
- Follow the `_base_model` `Field` gotcha: PK/FK/index must use `SQLField`
  (sqlmodel), not the pydantic `Field` re-exported in `_base_model`. Mirror
  `CheckListCollaboratorCreate`'s exact style.

### Provenance marker on `checklist_collaborator`

Add one nullable column:

- `via_group: Optional[str] = None` — `NULL` ⇒ an **explicit** individual share
  (today's behavior, and every pre-existing row); non-`NULL` ⇒ a row
  **materialized** from that group (managed by the reconciler; stores one
  contributing group name for display, but the reconciler always recomputes
  across *all* matching group shares).

Default `NULL` means every existing row is treated as an explicit share — exactly
the desired back-compat (see Migration/back-compat below).

---

## Semantics (precise rules)

For a `(checklist, user)` pair, let `G` = the max `permission` among group shares
on the checklist whose `group ∈ user.oidc_groups` (or none).

- **Explicit wins, independently.** If the user has a collaborator row with
  `via_group IS NULL`, it is authoritative and the reconciler never touches it —
  no auto-upgrade and no auto-downgrade from group membership. (Documented
  decision: an individually-set level is respected as set.)
- **Group fills gaps.** If the user has *no* explicit row and `G` exists,
  materialize a collaborator row at level `G` with `via_group` set (and a
  position row). If `G` changes (joined a higher group, a group's level was
  raised), update the row's level. Honor the invite gate
  (`SHARING_REQUIRE_INVITE_ACCEPT`) via the existing `_grant_share_to_user`.
- **Losing group access.** If a `via_group IS NOT NULL` row is no longer
  justified by any current group share ∩ the user's groups, delete the
  collaborator + position rows and pin a `checklist_deleted` poke to that user
  (mirror `delete_share`, incl. the `position.touch` seq-advance so offline
  clients actually drop the card).

Edge cases to keep correct (and test):

- User both explicitly shared (view) *and* in a group shared (edit) → stays at
  the explicit view; revoking the group leaves view intact.
- Owner is never a collaborator — skip `owner_id` in every expansion.
- `caller_restricted_to_own_groups` still gates *which* groups an owner may
  target (unchanged rule from today's `share_with_group`).

---

## Reconciliation service (`api/group_share_reconcile.py` — new module)

Pure functions taking the CRUDs (reuse `_grant_share_to_user`, `_ensure_position`,
and the delete/`touch`/sync-poke sequence from `routes_checklist_share.py`;
extract shared helpers if it reads cleaner). Two entry points:

1. `async reconcile_user(user, *, cruds…)` — for one user across all cards
   group-shared with any of their current groups. Grant newly-qualified, remove
   stale `via_group` rows. Called from the OIDC login path (guarded by "groups
   changed").
2. `async reconcile_group_share(checklist, group, *, cruds…)` — for one card+group
   after create/update: expand `find_by_oidc_group(group)`, materialize each
   member (skip owner + members holding an explicit row). A `remove=True` variant
   (or a sibling `revoke_group_share`) handles delete.

Emit exactly one broadcast `share_added` per batch that instant-added anyone
(matching today's group-share), plus the per-recipient invite/notification the
helper already does.

---

## Endpoints (`routes_checklist_share.py`, owner-only, under the sharing gate)

- `GET  /checklist/{id}/shares/group` → `List[GroupShareRead]`
  (`{group, permission, created_at}`) — the shared-groups list for the modal.
- `PUT  /checklist/{id}/shares/group/{group}` → **evolve** the existing endpoint:
  now **persist** a `CheckListGroupShare` row (upsert level) *and* call
  `reconcile_group_share`. Keep returning `GroupShareResult`
  (total/added/skipped) for the toast.
- `DELETE /checklist/{id}/shares/group/{group}` → delete the source row + reconcile
  removal. 204.
- `GET /user/me/groups` — unchanged.

Wire `reconcile_user` into the OIDC callback in `routes_auth.py` after the
`oidc_groups` update (only when changed).

---

## Migration (Alembic `0012`)

Autogenerate after the model changes (`alembic revision --autogenerate -m
"group shares living membership"`), then hand-verify:

- `create_table("checklist_group_share", …)` with the composite PK + FKs.
- `add_column("checklist_collaborator", Column("via_group", sa.String(),
  nullable=True))`.
- `downgrade()` drops both.

**Back-compat on the productive DB:** existing group-shared members are already
plain collaborator rows; after the migration they have `via_group = NULL`, so
they are treated as **explicit** shares — they keep access exactly as-is. They are
simply not yet governed by a `CheckListGroupShare` record, so re-sharing that
group (owner action) is what makes them "living" going forward. This is safe and
non-destructive; note it in the migration docstring and `UPGRADING.md`.

On a fresh DB, `create_all` builds both from the models and `0012` is a no-op
(the post-2.0 workflow — see `[[no-migrations-pre-production]]`).

---

## Frontend

- `stores/share.ts`: add `groupShares: Record<string, GroupShareRead[]>` +
  `listGroupShares`, `upsertGroupShare` (PUT, then re-read list + shares),
  `revokeGroupShare` (DELETE). Keep the `skipErrorToast` + friendly-message
  convention already in this store.
- `ShareModal/ShareWithGroup.vue`: becomes a reusable **add-box** (group select +
  level + Add) that stays usable for adding multiple groups in a row (mirror
  `AddPeople.vue`), and excludes groups already in the list.
- New `ShareModal/GroupShareList.vue` (mirror `PeopleList.vue`): each shared group
  with an editable level `USelect` + a remove button; empty state hidden.
- `ShareModal.vue`: render add-box + list inside the existing "Share with a group"
  card; fetch group shares in `onMounted` (owner only), alongside `listShares`.
- Regenerate OpenAPI types (`bun run postinstall`) — see `[[openapi-regen]]`.
- Keep `data-testid`s stable and add ones the E2E needs
  (`share-group-select`, a per-row `share-group-remove`, etc.).

---

## Tests (do not skip)

**Backend** (`tests/tests_group_share.py`, plus deltas in `tests_changes.py`):

- create → row persisted + current members materialized (owner skipped).
- list / revoke (204) shape.
- reconcile-on-login: a user newly in the group gains a card; a leaver loses it
  (collaborator + position gone, `checklist_deleted` pinned).
- explicit share preserved when its group share is revoked; group-derived removed.
- no auto-upgrade over an explicit lower level; no-downgrade / idempotent re-share.
- invite gate honored when `SHARING_REQUIRE_INVITE_ACCEPT` on (pending, no
  position until accept).
- `caller_restricted_to_own_groups` 403 for a non-member owner.
- Postgres run is authoritative (`./run_backend_tests_with_postgres.sh`).

**Frontend** (`tests/unit`): share-store group-share actions (mock `$checkapi`).

**E2E** (`tests/e2e`): add a group share → appears in list; add a second group;
change a group's level; remove a group. Reuse existing sharing-spec setup;
mind the known DnD/sharing flakiness note.

---

## Phasing (session boundaries)

- **Phase 1 — backend foundation + API + backend tests + migration.** Model,
  `via_group`, CRUD, reconcile service, the three endpoints, login hook,
  migration `0012`, `tests_group_share.py`. Independently shippable/testable
  behind the existing sharing config. **← start here.**
- **Phase 2 — frontend.** Store + `ShareWithGroup` add-box + `GroupShareList` +
  ShareModal wiring + regen types + vitest + E2E.
- **Phase 3 — polish/docs.** `CHANGELOG.md`, `UPGRADING.md`, `SYNC_PROTOCOL`/
  `administration` notes on the reconcile-on-login timing, and a memory entry.

Each phase updates the **Progress / handoff** section below so a fresh session can
resume without re-deriving context.

---

## Progress / handoff

- 2026-07-19: **plan written**.
- 2026-07-19: **Phases 1 + 2 implemented and verified.** What landed:
  - Backend: `model/checklist_group_share.py` (+ `via_group` on
    `checklist_collaborator`), `db/checklist_group_share.py`,
    `api/share_ops.py` (extracted `ensure_position` / `grant_share_to_user` /
    `remove_user_access` — routes + public-join now import these),
    `api/group_share_reconcile.py` (`reconcile_group_share` + `reconcile_user`),
    endpoints `GET`/`PUT`/`DELETE /checklist/{id}/shares/group[/{group}]`, and the
    reconcile-on-login hook in `routes_auth.py` (guarded by "groups changed").
  - Migration `0012_group_shares_living_membership.py` — hand-written (create_all
    hides the diff from autogenerate), **validated** by applying + reverting on a
    simulated pre-change SQLite DB.
  - Backend tests: `tests/tests_sharing_groups.py` updated for living semantics +
    new list/revoke/reconcile-on-login cases. **9 pass on Postgres**; the broad
    sharing suite (70 tests) still green after the `share_ops` refactor.
  - Frontend: `stores/share.ts` (`groupShares` state + `listGroupShares` /
    `upsertGroupShare` / `revokeGroupShare`), `ShareWithGroup.vue` (repeatable
    add-box, hides already-shared groups), new `GroupShareList.vue`, `ShareModal`
    wiring, OpenAPI + open-fetch types regenerated (`GroupShareRead`). E2E:
    add-then-remove-a-group test in `sharing-modal.spec.ts`.
  - Docs: `CHANGELOG.md`, `UPGRADING.md` (migration `0012` note).
- 2026-07-19: **E2E verified.** The new `sharing-modal` group test
  ("owner can share with a group and remove it via the dialog") passes against a
  real build (`./run_e2e_tests.sh --grep "share with a group"` → 2 passed). The
  Chromium headless shell runs here; the Playwright OS-deps *installer* errors on
  `libasound2` (renamed on this Debian variant) but that does not block the run.
- 2026-07-19: **Phase 3 loose ends resolved.**
  - **Token-refresh reconcile hook — investigated, nothing to implement.** The
    OIDC token-refresh path does *not* re-read `oidc_groups`, so there is no
    membership signal to reconcile off. `security.py::get_current_user_auth`
    refreshes via `oidc_refresh_access_token` (`auth/utils.py`), which uses only
    the `refresh_token` grant (`fetch_access_token`): it rewrites the encrypted
    token blob + expiry on `UserAuth`/`UserSession` and never loads the `User`,
    never calls `get_userinfo_from_token_or_endpoint`, and never touches
    `user.oidc_groups`. Group membership therefore only reaches the app in the
    login callback (`routes_auth.py`, which *does* fetch userinfo and rewrite
    `oidc_groups`). So the reconcile-on-login granularity is as live as the data
    source allows; membership changes still land on the next full login (already
    documented in UPGRADING). No hook added.
  - **Bulk-revoke poke noise — done.** `share_ops.remove_user_access` gained an
    `emit_removed_broadcast` flag (default `True`, so the single-user
    `delete_share` route is unchanged). `group_share_reconcile.reconcile_group_share`
    now passes `emit_removed_broadcast=False` per member and emits a **single**
    card-scoped `share_removed` broadcast after the loop when any member was
    removed — one poke for the whole batch instead of N. The per-user pinned
    `checklist_deleted` pokes stay targeted (emitted inside `remove_user_access`
    regardless of the flag). `reconcile_user` (login path) keeps the default,
    since its removals span distinct cards and each needs its own card-scoped
    poke. New test
    `tests_sharing_groups.py::test_bulk_group_revoke_emits_single_share_removed_broadcast`
    connects an SSE collector (reused from `tests_sharing_sync._SSECollector`) and
    asserts the owner receives exactly one `share_removed` while a removed member
    still gets its targeted `checklist_deleted`. Full group suite (10 pass, 1
    invite-gate skip) + broader sharing/sync suites (58 pass) green on Postgres.
