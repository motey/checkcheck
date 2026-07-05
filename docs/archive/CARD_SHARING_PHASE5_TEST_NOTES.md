# Phase 5 (Public URL Share) — Test Notes

> Captured **while implementing** so nothing is lost. The actual tests
> (`tests/tests_sharing_public.py`) are written in a **separate session**.
> Run on SQLite **and** Postgres (`run_backend_tests_with_*.sh`). Mirror the
> harness style of `tests/tests_sharing.py` (owner = global default login) and
> the SSE collector in `tests/tests_sharing_sync.py`.

## Harness notes / gotchas discovered while building
- The conftest boots a **single session-scoped server** with default config
  (`SHARING_PUBLIC_LINKS_ENABLED=True`). A real `SHARING_PUBLIC_LINKS_ENABLED=false`
  test needs a server booted with that env — either a dedicated module that starts
  its own subprocess, or a separate CI invocation. Note it; the existing Phase 1–4
  suite likewise deferred config-off tests.
- Token is returned **once** on `POST /checklist/{id}/public-links` and must be
  **absent/redacted** everywhere else (`GET` list). Assert the create response has
  `token`, and that list items do **not**.
- Anonymous calls use `req(..., suppress_auth=True)` so no bearer leaks in.
- Public routes live under `/api/public/checklist/{token}/...` (token in path).

## ✅ Verified by a throwaway smoke run during implementation (then deleted)
The following were confirmed working end-to-end on SQLite (manual smoke, not
committed): token returned once + redacted in list; anonymous GET card + items;
view can't check/edit (403); **unrelated** user → 401 vs non-owner **collaborator**
→ 403; check toggles state but can't edit text; edit can create; disabled link →
404; unknown token → 404; cross-card IDOR → 404; delete → 204. Both full existing
suites (SQLite + Postgres) stayed green (80 passed each). **Still unverified and
most important to cover in the real suite: token-keyed SSE** (live updates to/from
anonymous viewers) and the config-off behaviour.

## Link management API (owner-only) — `routes_checklist_share.py`
- `POST /checklist/{id}/public-links` `{permission, expires_at?}`:
  - owner → 200, response carries `token`, `permission`, `enabled=true`, `id`.
  - **unrelated** user → **401** (no access at all, raised by `user_has_checklist_access`).
  - non-owner **collaborator** (view/check/edit) → **403** (privilege-escalation guard).
  - `SHARING_ENABLED=false` or `SHARING_PUBLIC_LINKS_ENABLED=false` → 404.
- `GET /checklist/{id}/public-links`:
  - owner → list; **token field redacted/omitted** on every entry.
  - non-owner → 403.
- `PATCH /checklist/{id}/public-links/{link_id}`:
  - toggle `enabled`; change `permission`; set/clear `expires_at`. owner-only (403 else).
  - patching a link_id from another checklist → 404 (no cross-card leak).
- `DELETE /checklist/{id}/public-links/{link_id}`:
  - owner → 204, link gone (subsequent anonymous resolve → 404). non-owner → 403.

## Anonymous data API — `routes_checklist_public.py`
- `GET /public/checklist/{token}`:
  - valid view/check/edit token → 200, returns card + (separately) items.
  - renders with the **owner's** per-user settings (labels/ordering/collapse), NOT
    a collaborator's. (Create two collaborators with different labels; assert the
    public view shows the owner's label set.)
  - unknown / disabled / expired token → **404** (no card-existence leak).
- Level enforcement (mirror `test_share_permission_levels_enforced`):
  - `view` token: GET ok; PATCH state → 403; create/patch/delete item → 403.
  - `check` token: PATCH state ok; edit item text/create/delete → 403.
  - `edit` token: state + item create/patch/delete ok; **no** owner ops exist on
    the public surface at all (no share mgmt / transfer / delete-list endpoints).
- Cross-checklist IDOR: `/public/checklist/{tokenA}/item/{item_in_B}/...` → 404
  (the `verify_item_belongs_to_checklist` guard must be wired on public item routes too).

## Token-keyed SSE — `routes_sync_notification.py` (mirror `tests_sharing_sync.py`)
- Connect an anonymous SSE client to `/api/sync?token=<token>` (no bearer).
  - When an **authed editor** changes the card (item_state / item_text / item_created),
    the anonymous client receives the matching `upd_prop` for that `cl_id`.
  - When the **anonymous visitor** (check/edit link) changes the card, the **owner's**
    authed SSE client receives the update.
  - The anonymous client must **only** receive notifications for the checklist its
    token grants — never for an unrelated card.
  - `target_tokens` must be **stripped** from the SSE payload (assert clients never
    see a `target_tokens` field), same as `target_user_ids`.
- `disabled`/`expired`/`unknown` token on `/api/sync?token=` → stream rejected (no events).

## Phase 6 — Join via public link (`POST /public/checklist/{token}/join`)
> Self-service: a logged-in user adds the **same live card** to their deck as a collaborator
> at the link's level (not a copy). The one authenticated route on the `/public/...` surface.
- **Happy path**: User 2 (logged in) joins via a `view`/`check`/`edit` link →
  - response is the card; the card now appears in User 2's `GET /api/checklist` grid;
  - a new `CheckListCollaborator(user_2, level=link.level)` exists;
  - User 2 now syncs by `user.id` (a subsequent owner edit reaches User 2's authed
    `/api/sync` — and User 2's edit reaches the owner), i.e. ordinary collaborator sync.
- **Level matches the link**: join a `view` link → User 2 can read but PATCH state/item → 403
  (enforced by the normal collaborator guards, not the token path).
- **Logged-out → 401** (must have an account to own a deck slot); body should hint "log in".
- **Disabled / expired / unknown token → 404** (no card-existence leak, same as the read path).
- **Idempotent / no-downgrade**:
  - owner joins their own card → no-op, still owner (200, unchanged);
  - an existing `edit` collaborator joins via a `view` link → stays `edit` (never demoted);
  - joining twice does not create duplicate rows / does not error.
- **`share_added` sync on join**: the owner's authed SSE client receives `share_added` for the
  card when User 2 joins (mirror the collaborator-add assertion).
- **Config-off**: `SHARING_PUBLIC_LINKS_ENABLED=false` → `/join` → 404 (separate server boot).

## Config-off (separate server boot)
- `SHARING_PUBLIC_LINKS_ENABLED=false`: every `/public/...` endpoint, every
  `/checklist/{id}/public-links` endpoint, and `/sync?token=` → 404 / rejected.

## Lifecycle / cascade
- Deleting the checklist (owner) cascades and removes its `checklist_public_share`
  rows (FK `ondelete=CASCADE`); afterwards the token resolves → 404.
