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


def test_list_checklists_returns_own_position_on_shared_card():
    """Regression test for the list endpoint's eager-load of CheckList.position.
    A shared card has one position row per user, but CheckList.position is a
    scalar (uselist=False) relationship: an unscoped eager-load pulls every
    user's row into the single slot and SQLAlchemy picks one arbitrarily, so a
    caller could see another user's pinned/archived/index. Each caller's listing
    must embed *their own* position. Uses `pinned` (does not filter the card out
    of the default archived=False listing) so both users still see the card."""
    viewer_token = _make_user_token("share-list-position-viewer")
    viewer_id = _user_id(viewer_token)
    checklist_id = _create_checklist("PerUserListPos")
    _share(checklist_id, viewer_id, "view")  # now two position rows exist

    # Owner pins their layout; viewer leaves theirs unpinned.
    req(f"api/checklist/{checklist_id}/position", "patch", b={"pinned": True})  # owner
    req(
        f"api/checklist/{checklist_id}/position",
        "patch",
        b={"pinned": False},
        access_token=viewer_token,
    )

    owner_card = find_first_dict_in_list(
        req("api/checklist")["items"], {"id": checklist_id}
    )
    viewer_card = find_first_dict_in_list(
        req("api/checklist", access_token=viewer_token)["items"], {"id": checklist_id}
    )
    dict_must_contain(owner_card["position"], {"pinned": True})
    dict_must_contain(viewer_card["position"], {"pinned": False})


def test_get_checklist_returns_own_position_on_shared_card():
    """Regression test for GET /checklist/{id} (get_checklist).

    Same arbitrary-pick hazard as the list endpoint: CheckList.position is a
    scalar (uselist=False) joined relationship, so on a shared card (N position
    rows) the base get() collapses them into one slot and picks arbitrarily.
    get_checklist must re-scope the position to the caller. This is the path the
    frontend hits when it refreshes a single card after a share_added /
    checklist_position SSE event — the bug surfaced as the owner's card silently
    unpinning the moment it was shared (and refusing to stay pinned)."""
    viewer_token = _make_user_token("share-get-position-viewer")
    viewer_id = _user_id(viewer_token)
    checklist_id = _create_checklist("PerUserGetPos")

    # Owner pins their layout *before* sharing (repro of the reported flow).
    req(f"api/checklist/{checklist_id}/position", "patch", b={"pinned": True})
    _share(checklist_id, viewer_id, "view")  # now two position rows exist
    req(
        f"api/checklist/{checklist_id}/position",
        "patch",
        b={"pinned": False},
        access_token=viewer_token,
    )

    owner_card = req(f"api/checklist/{checklist_id}")
    viewer_card = req(f"api/checklist/{checklist_id}", access_token=viewer_token)
    dict_must_contain(owner_card["position"], {"pinned": True})
    dict_must_contain(viewer_card["position"], {"pinned": False})


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


# ── share-management authorization guards ────────────────────────────────────


def test_non_owner_cannot_manage_shares():
    """Only the owner may add/update shares. An 'edit' collaborator is not an
    owner and must be rejected when trying to share the card with someone else —
    otherwise any editor could grant access (privilege escalation)."""
    editor_token = _make_user_token("share-editor-noescalate")
    editor_id = _user_id(editor_token)
    target_token = _make_user_token("share-escalation-target")
    target_id = _user_id(target_token)

    checklist_id = _create_checklist("NoEscalation")
    _share(checklist_id, editor_id, "edit")

    # editor tries to share the card with a third user -> 403 (owner-only)
    req(
        f"api/checklist/{checklist_id}/shares/{target_id}",
        "put",
        b={"permission": "edit"},
        access_token=editor_token,
        expected_http_code=403,
    )
    # the third user genuinely got no access
    req(f"api/checklist/{checklist_id}", access_token=target_token, expected_http_code=401)


def test_collaborator_cannot_revoke_another_collaborator():
    """delete_share lets the owner revoke anyone and lets a user revoke
    *themselves*, but a non-owner must not be able to remove a *different*
    collaborator's access."""
    collab_a_token = _make_user_token("share-revoke-a")
    collab_a_id = _user_id(collab_a_token)
    collab_b_token = _make_user_token("share-revoke-b")
    collab_b_id = _user_id(collab_b_token)

    checklist_id = _create_checklist("RevokeGuard")
    _share(checklist_id, collab_a_id, "edit")
    _share(checklist_id, collab_b_id, "edit")

    # A tries to kick B -> 403, and B still has access afterwards
    req(
        f"api/checklist/{checklist_id}/shares/{collab_b_id}",
        "delete",
        access_token=collab_a_token,
        expected_http_code=403,
    )
    req(f"api/checklist/{checklist_id}", access_token=collab_b_token, expected_http_code=200)


def test_cannot_share_with_owner_or_unknown_user():
    """upsert_share rejects adding the owner as a collaborator (400) and adding a
    user that does not exist (404)."""
    checklist_id = _create_checklist("UpsertValidation")
    owner_id = req("api/user/me")["id"]

    # owner already has full access -> 400
    req(
        f"api/checklist/{checklist_id}/shares/{owner_id}",
        "put",
        b={"permission": "edit"},
        expected_http_code=400,
    )
    # unknown user id -> 404
    req(
        f"api/checklist/{checklist_id}/shares/00000000-0000-0000-0000-000000000000",
        "put",
        b={"permission": "edit"},
        expected_http_code=404,
    )


def test_transfer_ownership_validation():
    """transfer-ownership rejects transferring to the current owner (400) and to
    a non-existent user (404)."""
    checklist_id = _create_checklist("TransferValidation")
    owner_id = req("api/user/me")["id"]

    # transferring to the current owner is a no-op error
    req(
        f"api/checklist/{checklist_id}/transfer-ownership",
        "post",
        b={"new_owner_id": owner_id},
        expected_http_code=400,
    )
    # transferring to a user that does not exist
    req(
        f"api/checklist/{checklist_id}/transfer-ownership",
        "post",
        b={"new_owner_id": "00000000-0000-0000-0000-000000000000"},
        expected_http_code=404,
    )


def test_labels_are_per_user_on_shared_card():
    """Labels are a per-user organisational layer (each CheckListLabel carries a
    user_id, and a user may only attach labels they own). On a shared card every
    collaborator keeps their *own* labels — one user must never see another user's
    private labels, via the single GET, the /label endpoint, or the grid list."""
    viewer_token = _make_user_token("share-label-viewer")
    viewer_id = _user_id(viewer_token)
    checklist_id = _create_checklist("LabelScope")
    _share(checklist_id, viewer_id, "view")

    owner_label = req("api/label", "post", b={"display_name": "owner-only-label"})
    req(f"api/checklist/{checklist_id}/label/{owner_label['id']}", "put")

    viewer_label = req(
        "api/label",
        "post",
        b={"display_name": "viewer-only-label"},
        access_token=viewer_token,
    )
    req(
        f"api/checklist/{checklist_id}/label/{viewer_label['id']}",
        "put",
        access_token=viewer_token,
    )

    # viewer sees only their own label everywhere
    via_get = req(f"api/checklist/{checklist_id}", access_token=viewer_token)
    assert [l["display_name"] for l in via_get["labels"]] == ["viewer-only-label"]

    via_label_ep = req(f"api/checklist/{checklist_id}/label", access_token=viewer_token)
    assert [l["display_name"] for l in via_label_ep] == ["viewer-only-label"]

    listing = req("api/checklist", access_token=viewer_token)
    entry = find_first_dict_in_list(listing["items"], {"id": checklist_id})
    assert [l["display_name"] for l in entry["labels"]] == ["viewer-only-label"]

    # and the owner sees only their own
    owner_get = req(f"api/checklist/{checklist_id}")
    assert [l["display_name"] for l in owner_get["labels"]] == ["owner-only-label"]


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
