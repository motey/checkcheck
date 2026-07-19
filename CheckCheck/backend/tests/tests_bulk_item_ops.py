"""Integration tests for the bulk item operations:

* ``POST /api/checklist/{id}/items/uncheck-all``   (requires ``check``)
* ``POST /api/checklist/{id}/items/delete-checked`` (requires ``edit``)

Both must surface through the delta feed (``GET /api/changes``) so offline
collaborators converge — which is only true if the CRUD mutates ORM objects (so
the ``before_update`` mapper event bumps ``server_seq``) rather than issuing a
Core bulk ``UPDATE``/``DELETE``. The ``server_seq``-advanced assertions below are
the regression guard for that (bulk-op plan §1.2): rewrite the CRUD as a Core
bulk statement and these fail loudly.

The global default login (conftest) is the admin/owner; collaborators get their
own tokens and are passed as ``access_token=``.
"""

from typing import Dict, List, Optional

from utils import (
    req,
    authorize_for_access_token,
    create_test_user,
    find_first_dict_in_list,
)


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _user_id(token: str) -> str:
    return req("api/user/me", access_token=token)["id"]


def _share(checklist_id: str, user_id: str, permission: str) -> Dict:
    return req(
        f"api/checklist/{checklist_id}/shares/{user_id}",
        "put",
        b={"permission": permission},
    )


def _changes(since: int = 0, token: Optional[str] = None) -> Dict:
    return req("api/changes", q={"since": since}, access_token=token)


def _cursor(token: Optional[str] = None) -> int:
    return _changes(since=0, token=token)["next_cursor"]


def _new_item(cl_id: str, text: str, checked: bool = False) -> str:
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": text})["id"]
    if checked:
        req(f"api/checklist/{cl_id}/item/{item_id}/state", "patch", b={"checked": True})
    return item_id


# ── uncheck-all ───────────────────────────────────────────────────────────────


def test_uncheck_all_flips_only_checked_items():
    cl_id = req("api/checklist", "post", b={"name": "bulk uncheck"})["id"]
    a = _new_item(cl_id, "a", checked=True)
    b = _new_item(cl_id, "b", checked=True)
    c = _new_item(cl_id, "c", checked=False)

    res = req(f"api/checklist/{cl_id}/items/uncheck-all", "post")
    assert res["affected"] == 2
    assert res["item_count"] == 3
    assert res["item_checked_count"] == 0
    assert res["item_unchecked_count"] == 3

    for item_id in (a, b, c):
        st = req(f"api/checklist/{cl_id}/item/{item_id}/state")
        assert st["checked"] is False

    req(f"api/checklist/{cl_id}", "delete")


def test_uncheck_all_is_idempotent_on_replay():
    cl_id = req("api/checklist", "post", b={"name": "bulk uncheck replay"})["id"]
    _new_item(cl_id, "a", checked=True)

    first = req(f"api/checklist/{cl_id}/items/uncheck-all", "post")
    assert first["affected"] == 1
    # Replay: everything is already unchecked → a no-op that still succeeds.
    second = req(f"api/checklist/{cl_id}/items/uncheck-all", "post")
    assert second["affected"] == 0
    assert second["item_checked_count"] == 0

    req(f"api/checklist/{cl_id}", "delete")


def test_uncheck_all_advances_server_seq():
    """Regression guard for §1.2: the flipped items must surface in the delta feed
    (proving each dirtied state row got a fresh server_seq via before_update)."""
    cl_id = req("api/checklist", "post", b={"name": "uncheck seq"})["id"]
    a = _new_item(cl_id, "a", checked=True)
    b = _new_item(cl_id, "b", checked=True)

    before = _cursor()
    req(f"api/checklist/{cl_id}/items/uncheck-all", "post")

    delta = _changes(since=before)
    ids = [it["id"] for it in delta["items"]]
    assert a in ids and b in ids, "uncheck-all must surface flipped items in delta"
    for it in delta["items"]:
        if it["id"] in (a, b):
            assert it["state"]["checked"] is False

    req(f"api/checklist/{cl_id}", "delete")


# ── delete-checked ────────────────────────────────────────────────────────────


def test_delete_checked_removes_only_checked_items():
    cl_id = req("api/checklist", "post", b={"name": "bulk delete"})["id"]
    a = _new_item(cl_id, "a", checked=True)
    b = _new_item(cl_id, "b", checked=False)
    c = _new_item(cl_id, "c", checked=True)

    res = req(f"api/checklist/{cl_id}/items/delete-checked", "post")
    assert res["affected"] == 2
    assert res["item_count"] == 1
    assert res["item_checked_count"] == 0
    assert res["item_unchecked_count"] == 1

    remaining = [i["id"] for i in req(f"api/checklist/{cl_id}/item")["items"]]
    assert remaining == [b]
    assert a not in remaining and c not in remaining

    req(f"api/checklist/{cl_id}", "delete")


def test_delete_checked_is_idempotent_on_replay():
    cl_id = req("api/checklist", "post", b={"name": "bulk delete replay"})["id"]
    _new_item(cl_id, "a", checked=True)
    keep = _new_item(cl_id, "b", checked=False)

    first = req(f"api/checklist/{cl_id}/items/delete-checked", "post")
    assert first["affected"] == 1
    # Replay: the checked item is already tombstoned → skipped, still success.
    second = req(f"api/checklist/{cl_id}/items/delete-checked", "post")
    assert second["affected"] == 0

    remaining = [i["id"] for i in req(f"api/checklist/{cl_id}/item")["items"]]
    assert remaining == [keep]

    req(f"api/checklist/{cl_id}", "delete")


def test_delete_checked_advances_server_seq():
    """Regression guard for §1.2: tombstoned items must surface in the delta feed's
    ``item_tombstones`` (proving each row's deleted_at write bumped server_seq)."""
    cl_id = req("api/checklist", "post", b={"name": "delete seq"})["id"]
    a = _new_item(cl_id, "a", checked=True)
    b = _new_item(cl_id, "b", checked=True)

    before = _cursor()
    req(f"api/checklist/{cl_id}/items/delete-checked", "post")

    delta = _changes(since=before)
    assert a in delta["item_tombstones"], "deleted item must surface as tombstone"
    assert b in delta["item_tombstones"]

    req(f"api/checklist/{cl_id}", "delete")


# ── permission enforcement ────────────────────────────────────────────────────


def test_uncheck_all_requires_check_permission():
    viewer = _make_user_token("bulk-viewer")
    viewer_id = _user_id(viewer)
    cl_id = req("api/checklist", "post", b={"name": "uncheck perms"})["id"]
    _new_item(cl_id, "a", checked=True)

    # A view collaborator cannot untick all.
    _share(cl_id, viewer_id, "view")
    req(
        f"api/checklist/{cl_id}/items/uncheck-all",
        "post",
        access_token=viewer,
        expected_http_code=403,
    )
    # A check collaborator can.
    _share(cl_id, viewer_id, "check")
    req(
        f"api/checklist/{cl_id}/items/uncheck-all",
        "post",
        access_token=viewer,
        expected_http_code=200,
    )

    req(f"api/checklist/{cl_id}", "delete")


def test_delete_checked_requires_edit_permission():
    collab = _make_user_token("bulk-checker")
    collab_id = _user_id(collab)
    cl_id = req("api/checklist", "post", b={"name": "delete perms"})["id"]
    _new_item(cl_id, "a", checked=True)

    # A check collaborator cannot delete ticked items (needs edit).
    _share(cl_id, collab_id, "check")
    req(
        f"api/checklist/{cl_id}/items/delete-checked",
        "post",
        access_token=collab,
        expected_http_code=403,
    )
    # An edit collaborator can.
    _share(cl_id, collab_id, "edit")
    req(
        f"api/checklist/{cl_id}/items/delete-checked",
        "post",
        access_token=collab,
        expected_http_code=200,
    )

    req(f"api/checklist/{cl_id}", "delete")
