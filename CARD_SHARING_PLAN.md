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

### Phase 5 — Public URL share  ⏸️ DEFERRED TO A DEDICATED SESSION
Public/anonymous sharing is a larger, riskier slice (new anonymous auth surface +
sync changes) and is intentionally **not** part of this build. Implement it on its own
branch in a focused session. The full design is captured below so no context is lost.
See **"Phase 5 design (deferred)"** near the end of this document.

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
- _(Config-off and public-link tests belong to the deferred Phase 5 / a follow-up.)_

Full suite: **67 passed** on both `./run_backend_tests_with_sqlite.sh` and
`./run_backend_tests_with_postgres.sh`.

---

## Phase 2 (future ideas — not in the initial build)

These are deliberately deferred. Documented here so they're not lost.

- **Invite / accept flow** — instead of instantly adding a collaborator, send an invite the
  target must accept. Adds a `status: pending|accepted|declined` to the share row and an
  inbox endpoint. The initial build uses instant-add (matches the current model and is
  simpler); this is the natural upgrade for less-trusted multi-tenant deployments.
- **Expiring + password-protected public links** — `expires_at` is already in the model;
  add an optional bcrypt-hashed passphrase prompt before a public link resolves.
- **"Copy to my account" for public viewers** — let an anonymous/other user clone a public
  card into their own account (deep copy of checklist + items) instead of collaborating.
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
