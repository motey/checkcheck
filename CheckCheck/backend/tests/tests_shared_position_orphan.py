"""Regression: re-scoping a shared card's per-user ``position`` must return the
right per-user row AND leave every other user's position row intact.

``CheckList.position`` is a ``lazy="joined"``, ``uselist=False`` relationship with
**delete-orphan cascade**, but a shared card has one position row PER user (owner
+ each collaborator). Every checklist-returning route re-scopes that slot to the
caller (or, on the anonymous public surface, to the owner). Doing so by plain
assignment (``checklist.position = row``) orphans whatever row the unscoped
joined-load arbitrarily picked; delete-orphan then DELETEs that row on the next
flush, and the deletion *persists* if any commit follows in the request. The
authed ``PATCH /checklist`` path commits after re-scoping and so deterministically
corrupted the owner's row — that persistent case is covered by
``tests_shared_position_scope.py``.

These routes — the anonymous public GET, the public ``join`` and the invite
``accept`` — re-scope position too, but happen not to commit *after* the
reassignment, so today the orphaned DELETE is rolled back at session close: a
latent landmine, not active corruption. The fix routes every re-scope through
``access.scope_position_to_caller`` (``set_committed_value``, no dirtying), so a
future commit added after the reassignment can never orphan a sibling row.

This module locks the observable per-user scoping invariant across those three
routes: after each operation, every user still sees THEIR OWN position (own
pinned/index), so the re-scope neither leaks another user's row nor disturbs it.
"""

import pytest

from utils import (
    req,
    server_config,
    authorize_for_access_token,
    create_test_user,
)


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _user_id(token: str) -> str:
    return req("api/user/me", access_token=token)["id"]


def _pin(checklist_id: str, index: int, access_token: str = None) -> dict:
    return req(
        f"api/checklist/{checklist_id}/position",
        "patch",
        b={"pinned": True, "index": index},
        access_token=access_token,
    )


def _grid_entry(token: str, checklist_id: str) -> dict:
    """The caller's own grid row for the card, or None if absent — the grid
    inner-joins the caller's position, so a deleted position row makes the card
    disappear here entirely (the sharpest signal that a re-scope disturbed it)."""
    listing = req("api/checklist", access_token=token)
    return next((c for c in listing["items"] if c["id"] == checklist_id), None)


def get_owner_token() -> str:
    """The conftest stores the admin/owner token as the global default login."""
    from utils import get_access_token

    return get_access_token()


requires_invite_off = pytest.mark.skipif(
    server_config.SHARING_REQUIRE_INVITE_ACCEPT,
    reason="uses instant-add sharing (a collaborator with an immediate position) — "
    "runs in the default suite (flag off).",
)
requires_invite_on = pytest.mark.skipif(
    not server_config.SHARING_REQUIRE_INVITE_ACCEPT,
    reason=(
        "needs the server booted with SHARING_REQUIRE_INVITE_ACCEPT=1 — run the "
        "invite-flow pass (CHECKCHECK_TEST_SHARING_REQUIRE_INVITE_ACCEPT=1)."
    ),
)


# ── public anonymous GET (routes_checklist_public.get_public_checklist) ────────


@requires_invite_off
def test_anonymous_public_read_leaves_collaborator_position_intact():
    """An anonymous read renders the card with the OWNER's position. It must not
    disturb a collaborator's own per-user position: after the read the
    collaborator still sees their own pinned=True / distinct index in their grid,
    and the owner still sees theirs."""
    checklist_id = req("api/checklist", "post", b={"name": "OrphanPublicGet"})["id"]
    _pin(checklist_id, index=5)  # owner pins (index 5)

    collab_token = _make_user_token("orphan-pubget-collab")
    collab_id = _user_id(collab_token)
    req(
        f"api/checklist/{checklist_id}/shares/{collab_id}",
        "put",
        b={"permission": "edit"},
    )
    _pin(checklist_id, index=9, access_token=collab_token)  # collaborator pins (9)

    link = req(
        f"api/checklist/{checklist_id}/public-links", "post", b={"permission": "view"}
    )
    token = link["token"]
    resp = req(f"api/public/checklist/{token}", suppress_auth=True)
    # The anonymous surface renders the owner's position (index 5), never a
    # collaborator's.
    assert resp["position"]["index"] == 5, resp["position"]

    # The collaborator's own position is untouched: still their index 9, pinned.
    entry = _grid_entry(collab_token, checklist_id)
    assert entry is not None, (
        "collaborator's card vanished from their grid — their CheckListPosition "
        "was disturbed by an anonymous public read"
    )
    assert entry["position"]["pinned"] is True
    assert entry["position"]["index"] == 9, entry["position"]

    # The owner's position is likewise intact.
    owner_entry = _grid_entry(get_owner_token(), checklist_id)
    assert owner_entry is not None
    assert owner_entry["position"]["index"] == 5, owner_entry["position"]


# ── public join (routes_checklist_public.join_public_checklist) ────────────────


def test_public_join_scopes_to_joiner_and_leaves_owner_position_intact():
    """A user joining a public link gets the card scoped to THEIR own position,
    while the owner's own position is left intact."""
    checklist_id = req("api/checklist", "post", b={"name": "OrphanPublicJoin"})["id"]
    _pin(checklist_id, index=7)  # owner pins (index 7)

    link = req(
        f"api/checklist/{checklist_id}/public-links", "post", b={"permission": "edit"}
    )
    token = link["token"]

    joiner_token = _make_user_token("orphan-join-user")
    joined = req(
        f"api/public/checklist/{token}/join", "post", access_token=joiner_token
    )
    # The joiner gets their own fresh position (index 0 from _ensure_position),
    # never the owner's index 7.
    assert joined["position"]["index"] != 7, joined["position"]

    # The owner's position must be untouched by the join.
    owner_entry = _grid_entry(get_owner_token(), checklist_id)
    assert owner_entry is not None, (
        "owner's card vanished from their grid — their CheckListPosition was "
        "disturbed by another user's public join"
    )
    assert owner_entry["position"]["pinned"] is True
    assert owner_entry["position"]["index"] == 7, owner_entry["position"]

    # The joiner sees the card in their own grid with their own position.
    joiner_entry = _grid_entry(joiner_token, checklist_id)
    assert joiner_entry is not None
    assert joiner_entry["position"]["index"] != 7, joiner_entry["position"]


# ── invite accept (routes_checklist_share.accept_invite) ──────────────────────


@requires_invite_on
def test_accept_invite_scopes_to_invitee_and_leaves_owner_position_intact():
    """Accepting an invite returns the card scoped to the accepting user's own
    fresh position, and leaves the owner's own pinned position intact."""
    checklist_id = req("api/checklist", "post", b={"name": "OrphanAccept"})["id"]
    _pin(checklist_id, index=3)  # owner pins (index 3)

    invitee_token = _make_user_token("orphan-accept-invitee")
    invitee_id = _user_id(invitee_token)
    req(
        f"api/checklist/{checklist_id}/shares/{invitee_id}",
        "put",
        b={"permission": "edit"},
    )  # pending invite (flag on)
    accepted = req(
        f"api/checklist/{checklist_id}/invites/accept",
        "post",
        access_token=invitee_token,
    )
    # The invitee's returned position is their own (never the owner's index 3).
    assert accepted["position"]["index"] != 3, accepted["position"]

    owner_entry = _grid_entry(get_owner_token(), checklist_id)
    assert owner_entry is not None, (
        "owner's card vanished from their grid — their CheckListPosition was "
        "disturbed when the invitee accepted"
    )
    assert owner_entry["position"]["pinned"] is True
    assert owner_entry["position"]["index"] == 3, owner_entry["position"]
