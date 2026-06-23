"""Integration tests for the ?shared=with_me|by_me list filter on
GET /api/checklist.

Semantics:
  * ``with_me`` — cards owned by someone else that the caller accepted a share on.
  * ``by_me``   — cards the caller owns that have >=1 accepted collaborator.

Both AND with the existing label/search/archived filters. The default test
config runs with SHARING_REQUIRE_INVITE_ACCEPT off, so every share is created
``accepted`` immediately (and gets a CheckListPosition), which is exactly what
``by_me`` / ``with_me`` rely on.

The global default login (set by conftest) is the admin user; it owns the cards
throughout. Collaborators get their own tokens via authorize_for_access_token.
"""

from typing import Dict, List

from utils import (
    req,
    authorize_for_access_token,
    create_test_user,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _create_checklist(name: str, access_token: str = None) -> str:
    res = req(
        "api/checklist", "post", b={"name": name, "color_id": "yellow"},
        access_token=access_token,
    )
    return res["id"]


def _share(checklist_id: str, user_id: str, permission: str = "edit") -> Dict:
    return req(
        f"api/checklist/{checklist_id}/shares/{user_id}",
        "put",
        b={"permission": permission},
    )


def _user_id(token: str) -> str:
    return req("api/user/me", access_token=token)["id"]


def _create_label(display_name: str) -> str:
    return req("api/label", "post", b={"display_name": display_name})["id"]


def _assign_label(checklist_id: str, label_id: str) -> None:
    req(f"api/checklist/{checklist_id}/label/{label_id}", "put")


def _list_ids(shared: str = None, label_id: str = None, access_token: str = None) -> List[str]:
    q: Dict = {"limit": 100}
    if shared is not None:
        q["shared"] = shared
    if label_id is not None:
        q["label_id"] = label_id
    res = req("api/checklist", q=q, access_token=access_token)
    return [item["id"] for item in res["items"]]


# ── shared=with_me ───────────────────────────────────────────────────────────


def test_shared_with_me_lists_only_others_shared_cards():
    collab_token = _make_user_token("swm-collab")
    collab_id = _user_id(collab_token)

    owned_by_admin_unshared = _create_checklist("swm-admin-unshared")
    owned_by_admin_shared = _create_checklist("swm-admin-shared")
    _share(owned_by_admin_shared, collab_id)
    # A card the collaborator owns themselves must NOT count as "shared with me".
    owned_by_collab = _create_checklist("swm-collab-own", access_token=collab_token)

    ids = _list_ids(shared="with_me", access_token=collab_token)

    assert owned_by_admin_shared in ids
    assert owned_by_admin_unshared not in ids  # collaborator has no access at all
    assert owned_by_collab not in ids  # owned by caller, not shared *with* them


# ── shared=by_me ─────────────────────────────────────────────────────────────


def test_shared_by_me_lists_only_owned_shared_cards():
    collab_token = _make_user_token("sbm-collab")
    collab_id = _user_id(collab_token)

    unshared = _create_checklist("sbm-unshared")
    shared = _create_checklist("sbm-shared")
    _share(shared, collab_id)

    ids = _list_ids(shared="by_me")  # default login = admin/owner

    assert shared in ids
    assert unshared not in ids


def test_shared_by_me_no_duplicate_with_multiple_collaborators():
    """A card shared with several collaborators must appear exactly once (the
    access predicate is an EXISTS, not a row-multiplying join)."""
    collab_a = _user_id(_make_user_token("sbm-multi-a"))
    collab_b = _user_id(_make_user_token("sbm-multi-b"))

    shared = _create_checklist("sbm-multi")
    _share(shared, collab_a)
    _share(shared, collab_b)

    res = req("api/checklist", q={"shared": "by_me", "limit": 100})
    matching = [item for item in res["items"] if item["id"] == shared]
    assert len(matching) == 1, f"expected card once, got {len(matching)}"


# ── combines with label filter (AND) ─────────────────────────────────────────


def test_shared_filter_ands_with_label():
    collab_id = _user_id(_make_user_token("sbm-label-collab"))
    label_id = _create_label(f"SbmLabel-{collab_id[:8]}")

    shared_labeled = _create_checklist("sbm-shared-labeled")
    _share(shared_labeled, collab_id)
    _assign_label(shared_labeled, label_id)

    shared_unlabeled = _create_checklist("sbm-shared-unlabeled")
    _share(shared_unlabeled, collab_id)

    ids = _list_ids(shared="by_me", label_id=label_id)

    assert shared_labeled in ids
    assert shared_unlabeled not in ids  # excluded by the label half of the AND
