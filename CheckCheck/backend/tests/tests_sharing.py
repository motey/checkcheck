"""Integration tests for card (checklist) sharing — Phases 1–4.

Covers permission-level enforcement, per-user data staying writable for any
access level, the share lifecycle (add / update / revoke / leave), ownership
transfer (old owner demoted to edit), and user search. Public-link sharing is a
deferred Phase 5 and is not covered here.

The global default login (set by conftest) is the admin user, who acts as the
card *owner* throughout. Collaborators get their own tokens via
``authorize_for_access_token`` and are passed to ``req`` as ``access_token=``.
"""

from typing import Dict

from utils import (
    req,
    authorize_for_access_token,
    create_test_user,
    dict_must_contain,
    find_first_dict_in_list,
    list_contains_dict_that_must_contain,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _create_checklist(name: str) -> str:
    """Create a checklist as the admin/owner; return its id."""
    res = req("api/checklist", "post", b={"name": name, "color_id": "yellow"})
    return res["id"]


def _create_item(checklist_id: str, text: str) -> str:
    res = req(f"api/checklist/{checklist_id}/item", "post", b={"text": text})
    return res["id"]


def _share(checklist_id: str, user_id: str, permission: str) -> Dict:
    return req(
        f"api/checklist/{checklist_id}/shares/{user_id}",
        "put",
        b={"permission": permission},
    )


def _user_id(token: str) -> str:
    return req("api/user/me", access_token=token)["id"]


# ── permission-level enforcement ─────────────────────────────────────────────


def test_share_permission_levels_enforced():
    viewer_token = _make_user_token("share-viewer")
    viewer_id = _user_id(viewer_token)

    checklist_id = _create_checklist("Perms")
    item_id = _create_item(checklist_id, "milk")

    # ── view ──────────────────────────────────────────────────────────────
    _share(checklist_id, viewer_id, "view")

    # can read
    req(f"api/checklist/{checklist_id}", access_token=viewer_token, expected_http_code=200)
    req(f"api/checklist/{checklist_id}/item", access_token=viewer_token, expected_http_code=200)
    # cannot toggle state (needs check), edit text or the card (needs edit), or create
    req(
        f"api/checklist/{checklist_id}/item/{item_id}/state",
        "patch",
        b={"checked": True},
        access_token=viewer_token,
        expected_http_code=403,
    )
    req(
        f"api/checklist/{checklist_id}/item/{item_id}",
        "patch",
        b={"text": "nope"},
        access_token=viewer_token,
        expected_http_code=403,
    )
    req(
        f"api/checklist/{checklist_id}",
        "patch",
        b={"name": "nope"},
        access_token=viewer_token,
        expected_http_code=403,
    )
    req(
        f"api/checklist/{checklist_id}/item",
        "post",
        b={"text": "nope"},
        access_token=viewer_token,
        expected_http_code=403,
    )

    # ── check ─────────────────────────────────────────────────────────────
    _share(checklist_id, viewer_id, "check")
    req(
        f"api/checklist/{checklist_id}/item/{item_id}/state",
        "patch",
        b={"checked": True},
        access_token=viewer_token,
        expected_http_code=200,
    )
    # still cannot edit text
    req(
        f"api/checklist/{checklist_id}/item/{item_id}",
        "patch",
        b={"text": "nope"},
        access_token=viewer_token,
        expected_http_code=403,
    )

    # ── edit ──────────────────────────────────────────────────────────────
    _share(checklist_id, viewer_id, "edit")
    req(
        f"api/checklist/{checklist_id}/item/{item_id}",
        "patch",
        b={"text": "oat milk"},
        access_token=viewer_token,
        expected_http_code=200,
    )
    req(
        f"api/checklist/{checklist_id}/item",
        "post",
        b={"text": "bread"},
        access_token=viewer_token,
        expected_http_code=200,
    )
    # ...but an editor is not an owner: cannot manage shares or transfer
    req(
        f"api/checklist/{checklist_id}/shares",
        access_token=viewer_token,
        expected_http_code=403,
    )
    req(
        f"api/checklist/{checklist_id}/transfer-ownership",
        "post",
        b={"new_owner_id": viewer_id},
        access_token=viewer_token,
        expected_http_code=403,
    )


def test_unrelated_user_has_no_access():
    outsider_token = _make_user_token("share-outsider")
    checklist_id = _create_checklist("Private")
    # No share at all -> 401 from user_has_checklist_access
    req(
        f"api/checklist/{checklist_id}",
        access_token=outsider_token,
        expected_http_code=401,
    )


def test_per_user_data_writable_for_view_collaborator():
    """Ordering/position is the viewer's own layout, so even a view-only
    collaborator may change their CheckListPosition."""
    viewer_token = _make_user_token("share-layout-viewer")
    viewer_id = _user_id(viewer_token)
    checklist_id = _create_checklist("Layout")
    _share(checklist_id, viewer_id, "view")

    req(
        f"api/checklist/{checklist_id}/position",
        "patch",
        b={"archived": True},
        access_token=viewer_token,
        expected_http_code=200,
    )


def test_checklist_position_is_per_user_on_shared_card():
    """Regression test for CheckListPositionCRUD.get: once a card is shared it has
    multiple position rows (one per user). get() must return the *caller's* row
    (filtered by both checklist_id and user_id) — not another user's, and not
    raise MultipleResultsFound. Also covers GET /checklist/{id}/position, which
    previously passed user_name instead of user_id."""
    viewer_token = _make_user_token("share-position-viewer")
    viewer_id = _user_id(viewer_token)
    checklist_id = _create_checklist("PerUserPos")
    _share(checklist_id, viewer_id, "view")  # now two position rows exist

    # Each user changes only their own position.
    req(f"api/checklist/{checklist_id}/position", "patch", b={"archived": True})  # owner
    req(
        f"api/checklist/{checklist_id}/position",
        "patch",
        b={"archived": False},
        access_token=viewer_token,
    )

    owner_pos = req(f"api/checklist/{checklist_id}/position")
    viewer_pos = req(f"api/checklist/{checklist_id}/position", access_token=viewer_token)
    dict_must_contain(owner_pos, {"archived": True})
    dict_must_contain(viewer_pos, {"archived": False})


# ── share lifecycle ──────────────────────────────────────────────────────────


def test_share_lifecycle_and_cross_user_visibility():
    collab_token = _make_user_token("share-lifecycle-collab")
    collab_id = _user_id(collab_token)
    checklist_id = _create_checklist("Lifecycle")

    # not visible before sharing
    listing = req("api/checklist", access_token=collab_token)
    assert not list_contains_dict_that_must_contain(
        listing["items"], {"id": checklist_id}, raise_if_not_fullfilled=False
    )

    # share -> appears in the collaborator's own checklist listing
    _share(checklist_id, collab_id, "edit")
    listing = req("api/checklist", access_token=collab_token)
    assert list_contains_dict_that_must_contain(listing["items"], {"id": checklist_id})

    # owner can list shares and see the level
    shares = req(f"api/checklist/{checklist_id}/shares")
    entry = find_first_dict_in_list(shares, {"user_id": collab_id})
    dict_must_contain(entry, {"permission": "edit"}, required_keys=["user_name", "display_name"])

    # owner revokes -> gone, and access denied
    req(f"api/checklist/{checklist_id}/shares/{collab_id}", "delete", expected_http_code=204)
    req(f"api/checklist/{checklist_id}", access_token=collab_token, expected_http_code=401)
    listing = req("api/checklist", access_token=collab_token)
    assert not list_contains_dict_that_must_contain(
        listing["items"], {"id": checklist_id}, raise_if_not_fullfilled=False
    )


def test_collaborator_can_leave_shared_card():
    collab_token = _make_user_token("share-leaver")
    collab_id = _user_id(collab_token)
    checklist_id = _create_checklist("Leaveable")
    _share(checklist_id, collab_id, "edit")

    # self-removal allowed
    req(
        f"api/checklist/{checklist_id}/shares/{collab_id}",
        "delete",
        access_token=collab_token,
        expected_http_code=204,
    )
    req(f"api/checklist/{checklist_id}", access_token=collab_token, expected_http_code=401)


# ── ownership transfer ───────────────────────────────────────────────────────


def test_transfer_ownership_demotes_previous_owner():
    new_owner_token = _make_user_token("share-new-owner")
    new_owner_id = _user_id(new_owner_token)
    checklist_id = _create_checklist("Transferable")

    res = req(
        f"api/checklist/{checklist_id}/transfer-ownership",
        "post",
        b={"new_owner_id": new_owner_id},
    )
    dict_must_contain(res, {"new_owner_id": new_owner_id, "checklist_id": checklist_id})

    # new owner can manage shares (owner-only)
    req(f"api/checklist/{checklist_id}/shares", access_token=new_owner_token, expected_http_code=200)

    # previous owner (admin, global token) keeps edit access but is no longer owner
    req(f"api/checklist/{checklist_id}", expected_http_code=200)
    req(f"api/checklist/{checklist_id}", "patch", b={"text": "still editable"}, expected_http_code=200)
    req(f"api/checklist/{checklist_id}/shares", expected_http_code=403)

    # the demoted previous owner shows up as an 'edit' collaborator
    shares = req(f"api/checklist/{checklist_id}/shares", access_token=new_owner_token)
    admin_id = req("api/user/me")["id"]
    entry = find_first_dict_in_list(shares, {"user_id": admin_id})
    dict_must_contain(entry, {"permission": "edit"})


# ── user search ──────────────────────────────────────────────────────────────


def test_user_search_finds_users_without_leaking_email():
    _make_user_token("search-target-alice")
    results = req("api/user/search", q={"q": "search-target-alice"})
    assert isinstance(results, list) and len(results) >= 1
    hit = find_first_dict_in_list(results, {"user_name": "search-target-alice"})
    dict_must_contain(hit, required_keys=["id", "user_name", "display_name"])
    assert "email" not in hit, "user search must not expose email addresses"


def test_user_search_requires_min_query_length():
    # q has min_length=2 -> a single char is a validation error
    req("api/user/search", q={"q": "a"}, expected_http_code=422)
