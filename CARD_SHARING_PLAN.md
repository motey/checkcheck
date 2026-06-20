# Card Sharing — Backend Implementation Plan & Tracker

Status tracker for the **card (checklist) sharing** feature. Backend only for now;
the frontend is a separate effort.

Today's baseline: `CheckListCollaborator` already exists as a **binary** access grant
(owner / collaborator-with-full-edit), every checklist/item/label/position route gates
on `user_has_checklist_access`, and cross-user sync already fans out to
`owner + collaborators` (see `db/sync_notification.py::_resolve_target_user_ids`). The
broken `CheckListExternalShare` model is scaffolding to be replaced.

## Goals

- Share a card with another user at one of four levels: **view**, **check items**,
  **full edit**, **pass ownership**.
- Optional **searchable** user-selection (gated by a backend config flag).
- For OIDC users, optionally **restrict search to the searching user's own groups**.
- **Public URL share** so anonymous visitors can open a card at **view / check / edit**
  (no ownership transfer for public links).
- Keep **cross-user sync** working for every share level (already works for
  collaborators; extend to anonymous public-link visitors).

## Decisions (locked)

| Topic | Decision |
|---|---|
| Anonymous sync (SSE) | **Token-keyed SSE** — fan-out targets active public-share tokens, not just user ids, so anonymous viewers get live updates. |
| Pass ownership | Old owner is **demoted to an `edit` collaborator** (keeps access; not removed). |
| Anonymous personal settings (ordering, labels, `checked_items_*`) | Anonymous visitors use the **owner's** personal settings (these are per-user data). |
| Regular user search default | **ON** (`SHARING_USER_SEARCH_ENABLED=True`). |

## Permission model

Stored level on `CheckListCollaborator.permission`: `Literal["view","check","edit"]`
(default `edit` to preserve existing rows' behavior). "Pass ownership" is an **action**,
not a stored level. Public links carry the same `view|check|edit` set.

Route → required level mapping (the audit in Phase 2 makes this concrete):

- **view**: all GET (checklist, items, item-state, labels, positions).
- **check**: item-state toggle (check / uncheck).
- **edit**: create/update/delete items, item text, checklist name/text/color edits.
- **owner**: delete the list, manage shares, transfer ownership.
- **per-user / always allowed with any access**: `CheckListPosition` (ordering, archived,
  collapse) and `CheckListLabel` — these are the *viewer's own* layout, not the card's data.

---

## Phases

### Phase 1 — Foundations ✅ DONE
- [x] Config flags in `config.py`: `SHARING_ENABLED`, `SHARING_USER_SEARCH_ENABLED`,
      `SHARING_PUBLIC_LINKS_ENABLED` (all default `True`).
- [x] OIDC sub-config `OpenIDConnectProvider.RESTRICT_USER_SEARCH_TO_OWN_GROUPS`
      (default `False`).
- [x] Persist OIDC groups: `User.oidc_groups` (JSON list), written on every OIDC
      login (`from_oidc_userinfo` + the login sync path in `routes_auth.py`).
- [x] `CheckListCollaborator.permission` as the `SharePermission` enum (`view|check|edit`,
      default `edit`) — enum so it surfaces in the OpenAPI schema.
- [x] Alembic migration `0005` (oidc_groups + collaborator.permission), backfills existing
      collaborator rows to `edit`.

### Phase 2 — Authorization layer ✅ DONE
- [x] `ChecklistAccessLevel` enum (`view|check|edit|owner`) + rank map in `api/access.py`.
- [x] Extended `UserChecklistAccess` with `permission_level()`, `has_at_least()`,
      `can_view/can_check/can_edit`, `user_is_owner()`.
- [x] `require_checklist_permission(level: ChecklistAccessLevel)` dependency factory.
- [x] Audited every checklist/item/label/position route and applied the required level
      (10× view, 1× check, 9× edit; `delete_checklist` keeps any-access for "leave list").
- [x] Fixed a latent bug in `delete_checklist`: `user_is_collaborator`/`user_is_owner`
      were referenced as bare methods (always truthy) — now called.

### Phase 3 — Share-management API ✅ DONE
- [x] New `routes_checklist_share.py` (registered in `routers_map.py`, tag "Checklist Sharing"):
  - [x] `GET /checklist/{id}/shares` — list collaborators + levels (owner only).
  - [x] `PUT /checklist/{id}/shares/{user_id}` — add/update share at a level (owner only);
        also creates the target's `CheckListPosition` so the card appears in their grid.
  - [x] `DELETE /checklist/{id}/shares/{user_id}` — revoke; self-delete = "leave list".
  - [x] `POST /checklist/{id}/transfer-ownership` — set new owner, demote old to `edit`.
- [x] New sync `upd_prop` values: `share_added`, `share_removed`.
- [x] Whole API gated by `SHARING_ENABLED` (404 when off).

### Phase 4 — User search ✅ DONE
- [x] `GET /user/search?q=` for any authenticated user, gated by
      `SHARING_USER_SEARCH_ENABLED`; `q` min length 2; **never returns email**.
- [x] Minimal `UserSearchResult` response model (`id`, `user_name`, `display_name`).
- [x] When the caller's auth is OIDC and that provider has
      `RESTRICT_USER_SEARCH_TO_OWN_GROUPS`, results are intersected on shared `oidc_groups`.

### Drive-by fix (found while testing)
- [x] `CheckListPositionCRUD.get` used a Python `and` in its where-clause, so it filtered by
      only one column — fine with one position per checklist, but once a card is shared
      (one position row per user) it returned the wrong user's row / risked
      `MultipleResultsFound`. Fixed to `and_(...)`, and fixed `GET /checklist/{id}/position`
      which passed `user_name` as `user_id` (only "worked" because of that same bug).
      Covered by `test_checklist_position_is_per_user_on_shared_card`.

### Sync fan-out fixes (found on bug review) ✅ DONE
The delete flows resolved their notification recipients from *live* DB state via
`SyncNotifiationCRUD._resolve_target_user_ids`, but they did so **after** deleting the
rows that identify those recipients — so the wrong users (or nobody) were notified.
- [x] Added `SyncNotification.target_user_ids` (nullable JSON) + migration `0006`, and an
      optional `target_user_ids` arg on `SyncNotifiationCRUD.create` (plus public
      `resolve_target_user_ids`). When set, delivery is pinned to that captured set; the
      SQLite drain honours the stored targets, Postgres puts them in the `pg_notify` payload.
      `target_user_ids` is stripped from the SSE payload sent to clients.
- [x] **Owner deletes a shared checklist** — was notifying *nobody* (checklist +
      collaborators already gone → empty target set). Now captures `owner + collaborators`
      before deletion so every client gets `checklist_deleted`.
- [x] **Collaborator "leaves" a card** — was broadcasting `checklist_deleted` to the owner
      and remaining collaborators (whose card still exists). Now the leaver gets
      `checklist_deleted` (pinned) and everyone else gets `share_removed`.
- [x] **Owner revokes a share** — the removed user was excluded from their own removal.
      Now the removed user is pinned for `checklist_deleted`; others get `share_removed`.
- [x] **User search group restriction** applied the DB `limit` *before* the Python
      `oidc_groups` intersection, so OIDC-restricted callers could get too few / zero
      results. Now over-fetches (bounded) then filters then truncates.
- [x] SQLite SSE stream used a bare `await queue.get()` and never re-checked client
      disconnect; added a bounded `wait_for` so disconnects are noticed promptly (mirrors
      the Postgres path) — also keeps graceful shutdown fast.
- [x] Tests in `tests/tests_sharing_sync.py` connect a real SSE client to `/api/sync` and
      assert who receives which `upd_prop` for each delete flow (all three bugs above).

### Authorization / data-scoping fixes (found on second bug hunt) ✅ DONE
These surfaced once cards can be shared (multiple users + multiple item/label/position
rows per card). Both are cross-user leaks the Phase 2 audit missed because it gated on
the *checklist* in the path but not on the *item* addressed by id, nor on the per-user
label dimension.
- [x] **Cross-checklist item IDOR.** `require_checklist_permission(...)` authorizes the
      checklist named in the path, but the item / item-state / item-position routes address
      the item by its own id and never checked it belonged to that checklist. A user with
      any access to checklist A could read or mutate an item in checklist B via
      `/checklist/{A}/item/{item_in_B}/...` (`update`/`delete` item already checked this;
      GET item, GET/PATCH state, GET/PATCH position and the move endpoints did not). Added
      `verify_item_belongs_to_checklist` dependency in `api/access.py` and wired it into every
      affected route (404 to avoid revealing the foreign item). Covered by
      `tests/tests_cross_checklist_access.py`.
- [x] **Per-user labels leaked across a shared card.** Labels are a per-user layer (each
      `CheckListLabel` carries `user_id`), but `GET /checklist/{id}`, `GET /checklist/{id}/label`
      and the grid `GET /checklist` all read the unscoped `CheckList.labels` relationship, so a
      collaborator saw every other user's private labels. (The `list` CRUD *tried* to scope via
      `with_loader_criteria`, but that does not reach a m2m secondary, so it silently did
      nothing.) Added `ChecklistLabelCRUD.list_labels_for_user[_by_checklist]` and scope labels
      per caller in all three routes. Covered by
      `test_labels_are_per_user_on_shared_card` in `tests/tests_sharing.py`.

### Phase 5 — Public URL share  ✅ IMPLEMENTED (automated tests deferred to a follow-up session)
Built on its own in a focused session. Behaviour confirmed by a throwaway smoke run
(deleted) + both existing suites green (80 passed on SQLite and Postgres). The
**automated** Phase 5 tests (`tests/tests_sharing_public.py`) are written in a
separate session — the cases are captured in `CARD_SHARING_PHASE5_TEST_NOTES.md`.

Delivered:
- [x] `CheckListPublicShare` model + CRUD (replaces the dead `CheckListExternalShare`
      scaffold, which was never wired into the schema). Token = `secrets.token_urlsafe(32)`,
      unique+indexed; never logged.
- [x] Migration `0007`: create `checklist_public_share` + add `syncnotification.target_tokens`
      (`sa.Uuid()` matches `create_all` on both backends; chains 0006 → single head 0007).
- [x] Anonymous auth path in `api/access.py`: `AnonymousPrincipal` (id=None), an optional
      `public_level` on `UserChecklistAccess`, `resolve_public_checklist_access(token)`
      (404 on missing/disabled/expired/off — no card-existence leak),
      `require_public_checklist_permission(level)`, and
      `verify_item_belongs_to_public_checklist` (token-based IDOR guard).
- [x] Owner-only link management in `routes_checklist_share.py` (gated by
      `require_public_links_enabled`): `POST` (token returned once), `GET` (token redacted),
      `PATCH`, `DELETE`.
- [x] Anonymous data surface `routes_checklist_public.py` under `/public/checklist/{token}/...`
      (registered in `routers_map.py`): GET card (owner's per-user position+labels), GET items,
      PATCH state (check), POST/PATCH/DELETE item (edit). Existing authed routes untouched.
- [x] Token-keyed sync: `target_tokens` on `SyncNotification`; `_resolve_target_tokens`
      (active links); `create()` resolves tokens dynamically (so ordinary edits reach
      anonymous viewers); `/sync?token=` registers an anonymous SSE client; both Postgres and
      SQLite matchers deliver by `user.id` **or** token via `_principal_is_target`;
      `target_tokens` stripped from the client payload.

Known limitation (note for the test session): delete/revoke flows pin recipients by
captured **user ids** only; active **tokens** are resolved dynamically, so after a cascade
delete an anonymous viewer is not pushed `checklist_deleted` (their SSE just goes quiet).
Acceptable for v1; revisit if anonymous delete-notification is desired.

The original design is preserved below under **"Phase 5 design (deferred)"**.

### Phase 6 — Join via public link (self-service collaborator)  📋 PLANNED
A logged-in user who opens a public link can add the **same live card** to their own deck
as a real collaborator at the link's level. This is **not** a copy/fork — User 2 ends up on
the identical card, checking the same items as everyone else; the public link doubles as a
**self-service invite**. (Replaces the abandoned "Copy to my account" Stage-2 idea — that
forked the data, which is the opposite of what's wanted.)

**Decisions (locked):**
- Every active link is joinable at **its own level** (a `view` link → join as viewer). No
  separate "invite" flag in v1 — any leaked link is a leaked seat at that level (acceptable
  for the "drop it in the chat group" use case; an `allow_join` flag is the natural later
  refinement if read-only-but-not-joinable links are ever needed).
- **Idempotent**: if the caller is already the owner or a collaborator → no-op, and **never
  downgrade** (joining a `view` link while already an `edit` collaborator keeps `edit`).

**Endpoint:** `POST /api/public/checklist/{token}/join`
- Lives in `routes_checklist_public.py` for URL grouping — but it is the **one authenticated
  route** in that otherwise-anonymous file: `get_current_user` is **required** (you need an
  account to own a deck slot). Logged-out → **401** ("log in to add this card").
- Gated by `require_public_links_enabled` (same switch as the rest of public links).
- Resolve the `token` → enabled, non-expired link → `checklist_id` + level (404 on
  missing/disabled/expired, same as the read path — no card-existence leak).
- Then reuse the existing `upsert_share` machinery: `CheckListCollaboratorCRUD.upsert(
  checklist_id, current_user.id, permission=link.permission)` → `_ensure_position(...)` so the
  card lands in their grid → emit `share_added` sync (reaches owner + collaborators + the new
  joiner, so the owner sees the share list grow and the joiner's other devices update).
- Returns the `CheckListApiWithSubObj` for the new collaborator (labels scoped to
  `current_user` → empty until they add their own; position = the freshly-created one), so the
  frontend drops it straight into the deck.

**Reuse (no new table, no copy logic):** `_ensure_position`, `CheckListCollaboratorCRUD.upsert`,
`CheckListPositionCRUD`, the `share_added` sync path, the token lookup
(`CheckListPublicShareCRUD.get_by_token` + the enabled/expiry check from
`resolve_public_checklist_access`). The only genuinely new behaviour is *a user granting
themselves access via a capability* — which is exactly the invite-link model.

Cleanly dissolves the earlier "logged-in user on a public link gets no SSE" wrinkle: once they
join they're a real collaborator and sync by `user.id`, so they never need the token stream.

Test cases captured in `CARD_SHARING_PHASE5_TEST_NOTES.md` (written in the same follow-up
session as the Phase 5 tests).

### Tests — `tests/tests_sharing.py` (pytest, runs on SQLite + Postgres) ✅ DONE
- [x] Permission enforcement: view cannot check/edit, check can toggle but not edit text,
      edit can edit but cannot manage shares/transfer, owner can.
- [x] Unrelated user has no access (401).
- [x] Per-user data (position/ordering) remains writable for a view-only collaborator.
- [x] Per-user position correctness on a shared card (the drive-by `get` fix).
- [x] Share lifecycle: add → appears in target's listing; update level; revoke → gone;
      self-leave.
- [x] Ownership transfer: new owner can manage shares; old owner demoted to `edit` (keeps
      access, loses owner powers).
- [x] User search finds users, never leaks email, enforces min query length.
- [x] Share-management authorization guards (added on review): a non-owner editor cannot
      add shares (no privilege escalation); a non-owner cannot revoke *another*
      collaborator (only owner-or-self); cannot share with the owner (400) or an unknown
      user (404); transfer-ownership rejects the current owner (400) and unknown users (404).
- _(Config-off and public-link tests belong to the deferred Phase 5 / a follow-up.)_

Full suite: **74 passed** on `./run_backend_tests_with_sqlite.sh`
(67 baseline + 4 authorization-guard tests + 3 SSE fan-out regression tests).
Sharing + sync suites also green on `./run_backend_tests_with_postgres.sh`.

---

## Stage 2 (future ideas — not in the initial build)

These are deliberately deferred. Documented here so they're not lost.

- **Invite / accept flow** — instead of instantly adding a collaborator, send an invite the
  target must accept. Adds a `status: pending|accepted|declined` to the share row and an
  inbox endpoint. The initial build uses instant-add (matches the current model and is
  simpler); this is the natural upgrade for less-trusted multi-tenant deployments.
- **Expiring + password-protected public links** — `expires_at` is already in the model;
  add an optional bcrypt-hashed passphrase prompt before a public link resolves.
- **A real "copy / duplicate card"** (distinct from join) — a deep-copy primitive
  (`deep_copy_checklist`) for genuinely *forking* a card into an independent owned copy
  (templates, "duplicate my card"). Deliberately **not** the public-link flow — that is
  Phase 6 "join via link" (same live card), which is what the chat-group use case wants.
- **Share audit trail** — `created_by` + `created_at` on every share (collaborator and
  public), plus an optional activity log of who changed what level when.
- **Notifications on share** — notify a user (in-app / email) when a card is shared with
  them, and the owner when a public link is first opened.
- **Search hardening** — per-IP/per-user rate limiting on `/user/search`, enforced minimum
  query length, and opt-in "discoverable" flag per user so people can hide from search.
- **Revoke-all / bulk share management** — owner can clear all shares or all public links
  for a card in one call; "leave all shared cards" for a user.
- **Granular public-link scopes** — e.g. a public link that exposes only specific items, or
  a read-only "presentation" mode that hides checked-item separation.
- **Org/group sharing** — share a card with an entire OIDC group at once (requires the
  persisted `oidc_groups` from Phase 1 plus a group→members resolver).

---

## Phase 5 design (deferred) — Public URL share

> Implement this in its own session/branch. It is deferred because it introduces an
> **anonymous authentication surface** and changes the **sync fan-out**, both of which
> deserve isolated review and testing. Phases 1–4 are designed to not depend on it.

### Decisions already locked (from planning)
- Anonymous visitors get **token-keyed SSE** (live updates), not just a static load.
- Public links carry the same `view | check | edit` levels (no `owner`).
- Anonymous visitors render with the **owner's** per-user settings (ordering, labels,
  `checked_items_separated` / `checked_items_collapsed`), since those are per-user data
  and an anonymous visitor has no `User` row.

### Data model
New table `CheckListPublicShare` (replaces the broken `CheckListExternalShare` scaffold,
which should be deleted):

| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `checklist_id` | uuid FK → `checklist.id` (`ondelete=CASCADE`) | |
| `token` | str, unique, indexed | URL-safe secret (`secrets.token_urlsafe`); this is the capability — treat like a password, never log it |
| `permission` | `SharePermission` (`view\|check\|edit`) | reuse the Phase 1 enum |
| `enabled` | bool, default `True` | soft on/off without deleting |
| `expires_at` | optional epoch/datetime | null = never |
| `created_by` | uuid FK → `user.id` | audit |
| timestamps | from `TimestampedModel` | |

Migration `0006` creates the table and drops `checklist_external_share`.

### Authorization integration
- Add `ChecklistAccessLevel` already exists (Phase 2). The public path must produce a
  compatible access object so existing `require_checklist_permission(...)` route guards
  can be reused.
- Introduce a resolver dependency, e.g. `resolve_checklist_access_or_public(token?)`, that:
  - if a normal session/bearer user is present → behaves exactly like today;
  - else if a valid, enabled, non-expired `token` is supplied → yields a
    `UserChecklistAccess`-like object whose `permission_level()` is the link's level and
    whose "user" is an **anonymous sentinel** (no DB id).
- **Decision needed in that session:** either (a) make `UserChecklistAccess` accept an
  optional anonymous principal, or (b) introduce a sibling `PublicChecklistAccess` with the
  same `has_at_least()` interface. (a) keeps the guards untouched; lean that way.
- Anonymous principals must be barred from owner-only actions (share mgmt, transfer,
  delete-list) — they top out at `edit`.

### Endpoints (sketch)
- Owner-only, gated by `SHARING_PUBLIC_LINKS_ENABLED`:
  - `POST /checklist/{id}/public-links` — create a link `{permission, expires_at?}`,
    returns the token **once**.
  - `GET /checklist/{id}/public-links` — list (omit/redact token).
  - `PATCH /checklist/{id}/public-links/{link_id}` — toggle `enabled`, change level/expiry.
  - `DELETE /checklist/{id}/public-links/{link_id}` — revoke.
- Anonymous:
  - `GET /public/checklist/{token}` — resolve and return the card + items, using the
    owner's per-user settings as described above.
  - Reuse existing item/state/position endpoints behind the dual resolver, OR mirror a
    minimal `/public/...` surface — decide in-session (reuse is less code, more careful
    guard review).

### Sync (the careful part)
Today `SyncNotifiationCRUD._resolve_target_user_ids(cl_id)` returns `owner + collaborators`
and both fan-out paths key delivery by `user.id`:
- **Postgres**: `pg_notify` payload carries `target_user_ids`; `_pg_on_notify` matches
  `str(user.id) in targets`.
- **SQLite**: `_sqlite_drain` matches `user.id in noti.target_user_ids`.

For anonymous viewers there is no `user.id`, so:
- Give each connected anonymous SSE client a **token identity** (the public-share token, or
  a per-connection id mapped to a token).
- Extend the notification target set to also include **active public tokens** for the
  checklist (resolve from `CheckListPublicShare` where `enabled` and not expired).
- Extend the payload + both matchers to deliver to clients whose token is in the target set
  (in addition to the existing user-id matching).
- `/sync` must accept an anonymous subscription keyed by token (new query/path param), and
  must **only** stream notifications for checklists that token grants access to.
- SQLite remains dev-only — keep it correct, don't harden it (see `[[db-targets]]`).

### Security checklist for that session
- Tokens are capabilities: high entropy, never logged, compared in constant time where it
  matters, redacted in list responses.
- Disabled/expired/deleted links reject immediately (no leakage of card existence).
- Anonymous writes (check/edit links) still emit sync so the owner sees changes live.
- Rate-limit token resolution to blunt token guessing.

### Tests for that session (`tests/tests_sharing_public.py`)
- Create/list/patch/revoke links (owner-only; others 403).
- Anonymous access at each level; level enforcement (view can't check, check can't edit).
- Expired / disabled / unknown token → rejected.
- Anonymous viewer sees the owner's ordering/labels/collapse settings.
- Token-keyed SSE: an anonymous viewer receives a notification when an authed editor
  changes the card, and vice-versa.
- `SHARING_PUBLIC_LINKS_ENABLED=false` → all public endpoints disabled.

---

## Environment / how to run (for future sessions)

- Backend is pinned to **Python 3.13** (`CheckCheck/backend/pyproject.toml`,
  `requires-python = "==3.13.*"`). The repo venv lives at `./.venv`.
- Dependencies are pinned in `CheckCheck/backend/requirements*.txt`; `pytest` is in the
  `test` dependency group.
- Run the backend tests with the venv active:
  - `./run_backend_tests_with_sqlite.sh` — quick, Docker-free (dev path).
  - `./run_backend_tests_with_postgres.sh` — primary target; needs Docker.
  - `--dev` stops at first failure with verbose logs; a path or `-k` narrows the run.
- DB migrations run automatically on server start (`init_schema_and_migrations`):
  fresh DBs use `create_all` + `alembic stamp head`; existing DBs run `alembic upgrade head`.
