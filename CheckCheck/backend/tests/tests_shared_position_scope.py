"""Regression: PATCH /checklist/{id} must return the CALLER's own position row.

`CheckList.position` is a `lazy="joined"`, `uselist=False` relationship, but a
shared card has one position row PER user (owner + each collaborator). An
unscoped eager-load collapses those rows into the single slot and picks one
arbitrarily, so a shared card can report ANOTHER user's pinned/archived/index.

GET /checklist/{id} already re-scopes the position to the caller; the PATCH
(update_checklist) route did not, so editing a shared card returned a stranger's
position — e.g. the owner pins a card, shares it, and the next edit round-trips a
collaborator's `pinned=False`, silently unpinning it (the user-reported "after
sharing, pinning is broken").

Deterministic reproduction: the OWNER pins the card (pinned=True), a COLLABORATOR
(who defaults to pinned=False) edits it. Pre-fix the unscoped join returns the
owner's row → the collaborator's PATCH response wrongly reports pinned=True.
"""

from utils import req, authorize_for_access_token, create_test_user


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}-pw-secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _user_id(token: str) -> str:
    return req("api/user/me", access_token=token)["id"]


def test_patch_shared_checklist_returns_callers_own_position():
    # Owner (the global default admin login) creates and PINS a card.
    checklist_id = req("api/checklist", "post", b={"name": "PinScope"})["id"]
    owner_pos = req(
        f"api/checklist/{checklist_id}/position", "patch", b={"pinned": True, "index": 5}
    )
    assert owner_pos["pinned"] is True

    # Share it (edit) with a collaborator, who gets their OWN position row
    # (pinned defaults to False).
    collab_token = _make_user_token("pinscope-collab")
    collab_id = _user_id(collab_token)
    req(
        f"api/checklist/{checklist_id}/shares/{collab_id}",
        "put",
        b={"permission": "edit"},
    )

    # The collaborator edits the card. The response's position MUST be theirs
    # (pinned=False), not the owner's (pinned=True).
    res = req(
        f"api/checklist/{checklist_id}",
        "patch",
        b={"name": "PinScope-renamed"},
        access_token=collab_token,
    )
    assert res["position"]["pinned"] is False, (
        "PATCH /checklist returned another user's position — the shared card's "
        f"pinned state leaked across users: {res['position']}"
    )

    # And the owner's own edit must still report the owner's pinned=True.
    owner_res = req(
        f"api/checklist/{checklist_id}", "patch", b={"name": "PinScope-owner-edit"}
    )
    assert owner_res["position"]["pinned"] is True, (
        f"owner's PATCH lost their own pinned position: {owner_res['position']}"
    )
