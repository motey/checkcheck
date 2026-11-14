"""Integration tests for the two card-copy actions:

* ``POST /api/checklist/{id}/duplicate``           (requires ``view``)
* ``POST /api/checklist/{id}/items/move-checked``  (requires ``edit``)

Both create a new card owned by the caller in one transaction. The delta-feed
assertions are the regression guard that every new or tombstoned row got a fresh
``server_seq`` (ORM objects, no Core bulk statements), so other devices and
collaborators converge.

The global default login (conftest) is the admin; other users get their own
tokens and are passed as ``access_token=``.
"""

import datetime
import uuid
from typing import Dict, List, Optional

from utils import req, authorize_for_access_token, create_test_user


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _user_id(token: Optional[str] = None) -> str:
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


def _new_item(
    cl_id: str,
    text: str,
    checked: bool = False,
    indentation: int = 0,
    token: Optional[str] = None,
) -> str:
    item_id = req(
        f"api/checklist/{cl_id}/item",
        "post",
        b={"text": text, "position": {"indentation": indentation}},
        access_token=token,
    )["id"]
    if checked:
        req(
            f"api/checklist/{cl_id}/item/{item_id}/state",
            "patch",
            b={"checked": True},
            access_token=token,
        )
    return item_id


def _items(cl_id: str, token: Optional[str] = None) -> List[Dict]:
    return req(f"api/checklist/{cl_id}/item", access_token=token)["items"]


def _label(name: str, token: Optional[str] = None) -> str:
    return req("api/label", "post", b={"display_name": name}, access_token=token)["id"]


def _attach(cl_id: str, label_id: str, token: Optional[str] = None) -> None:
    req(f"api/checklist/{cl_id}/label/{label_id}", "put", access_token=token)


def _source_card(name: str = "Packing list") -> Dict:
    color_id = req("api/color")[0]["id"]
    card = req(
        "api/checklist",
        "post",
        b={
            "name": name,
            "text": "for the **weekend**",
            "color_id": color_id,
            "checked_items_seperated": False,
            "suggest_existing_items": False,
        },
    )
    return card


# ── duplicate ─────────────────────────────────────────────────────────────────


def test_duplicate_copies_fields_items_and_labels_all_unchecked():
    src = _source_card("Packing list")
    a = _new_item(src["id"], "tent", checked=True)
    b = _new_item(src["id"], "stove", indentation=1)
    c = _new_item(src["id"], "map", checked=True)
    label_id = _label("trips")
    _attach(src["id"], label_id)

    copy = req(f"api/checklist/{src['id']}/duplicate", "post")

    assert copy["id"] != src["id"]
    assert copy["name"] == "Packing list (copy)"
    assert copy["text"] == src["text"]
    assert copy["color_id"] == src["color_id"]
    assert copy["color"]["id"] == src["color_id"]
    assert copy["checked_items_seperated"] is False
    assert copy["suggest_existing_items"] is False
    assert copy["owner_id"] == _user_id()
    assert copy["my_permission"] == "owner"
    assert copy["position"]["pinned"] is False
    assert copy["position"]["archived"] is False
    assert [l["id"] for l in copy["labels"]] == [label_id]

    items = _items(copy["id"])
    assert [i["text"] for i in items] == ["tent", "stove", "map"]
    assert all(i["state"]["checked"] is False for i in items)
    assert [i["position"]["indentation"] for i in items] == [0, 1, 0]
    assert not {i["id"] for i in items} & {a, b, c}, "copies get new ids"

    # The source is untouched.
    src_items = _items(src["id"])
    assert [i["id"] for i in src_items] == [a, b, c]
    assert [i["state"]["checked"] for i in src_items] == [True, False, True]

    # The new card lands on top of the caller's board.
    assert copy["position"]["index"] > src["position"]["index"]

    req(f"api/checklist/{copy['id']}", "delete")
    req(f"api/checklist/{src['id']}", "delete")


def test_duplicate_without_body_and_empty_card():
    src = req("api/checklist", "post", b={"name": ""})
    copy = req(f"api/checklist/{src['id']}/duplicate", "post")
    assert copy["name"] == "(copy)"
    assert _items(copy["id"]) == []
    req(f"api/checklist/{copy['id']}", "delete")
    req(f"api/checklist/{src['id']}", "delete")


def test_duplicate_does_not_copy_shares_public_links_or_reminders():
    other = _make_user_token("copy-share-target")
    src = _source_card("Shared source")
    _new_item(src["id"], "x")
    _share(src["id"], _user_id(other), "edit")
    req(f"api/checklist/{src['id']}/public-links", "post", b={"permission": "view"})
    req(
        f"api/checklist/{src['id']}/reminders",
        "post",
        b={"remind_at": datetime.datetime(2090, 1, 1, 9, 0).isoformat()},
        expected_http_code=201,
    )

    copy = req(f"api/checklist/{src['id']}/duplicate", "post")

    assert req(f"api/checklist/{copy['id']}/shares") == []
    assert req(f"api/checklist/{copy['id']}/public-links") == []
    assert req(f"api/checklist/{copy['id']}/reminders") == []
    # The share target of the source cannot see the copy.
    req(f"api/checklist/{copy['id']}", access_token=other, expected_http_code=403)

    req(f"api/checklist/{copy['id']}", "delete")
    req(f"api/checklist/{src['id']}", "delete")


def test_duplicate_is_idempotent_with_client_id():
    src = _source_card("Replay")
    _new_item(src["id"], "a")
    new_id = str(uuid.uuid4())

    first = req(f"api/checklist/{src['id']}/duplicate", "post", b={"id": new_id})
    assert first["id"] == new_id
    # More items appear in the source before the replay: the replay must still
    # return the first copy unchanged, never copy again.
    _new_item(src["id"], "b")
    second = req(f"api/checklist/{src['id']}/duplicate", "post", b={"id": new_id})
    assert second["id"] == new_id
    assert [i["text"] for i in _items(new_id)] == ["a"]

    names = [c["name"] for c in req("api/checklist", q={"limit": 999})["items"]]
    assert names.count("Replay (copy)") == 1

    req(f"api/checklist/{new_id}", "delete")
    # A tombstoned copy is gone for good: a late replay must not resurrect it.
    req(
        f"api/checklist/{src['id']}/duplicate",
        "post",
        b={"id": new_id},
        expected_http_code=410,
    )
    req(f"api/checklist/{src['id']}", "delete")


def test_duplicate_rejects_an_id_owned_by_someone_else():
    other = _make_user_token("copy-id-collision")
    foreign = req("api/checklist", "post", b={"name": "theirs"}, access_token=other)
    src = _source_card("Mine")
    req(
        f"api/checklist/{src['id']}/duplicate",
        "post",
        b={"id": foreign["id"]},
        expected_http_code=409,
    )
    req(f"api/checklist/{src['id']}", "delete")
    req(f"api/checklist/{foreign['id']}", "delete", access_token=other)


def test_duplicate_uses_client_item_ids_where_given():
    src = _source_card("Mapped")
    a = _new_item(src["id"], "a")
    b = _new_item(src["id"], "b")
    mapped = str(uuid.uuid4())

    copy = req(
        f"api/checklist/{src['id']}/duplicate",
        "post",
        b={
            "position": {"index": 12345.5},
            # One mapped item, plus a key that is not an item of the source
            # (ignored).
            "item_ids": {a: mapped, str(uuid.uuid4()): str(uuid.uuid4())},
        },
    )
    assert copy["position"]["index"] == 12345.5
    ids = [i["id"] for i in _items(copy["id"])]
    assert ids[0] == mapped
    assert ids[1] not in (a, b, mapped)

    # Reusing an id that an item already has is a conflict ...
    req(
        f"api/checklist/{src['id']}/duplicate",
        "post",
        b={"item_ids": {a: mapped}},
        expected_http_code=409,
    )
    # ... and so is mapping two items to one id (invalid request).
    same = str(uuid.uuid4())
    req(
        f"api/checklist/{src['id']}/duplicate",
        "post",
        b={"item_ids": {a: same, b: same}},
        expected_http_code=422,
    )

    req(f"api/checklist/{copy['id']}", "delete")
    req(f"api/checklist/{src['id']}", "delete")


def test_duplicate_by_a_view_collaborator_is_their_own_card():
    viewer = _make_user_token("copy-viewer")
    viewer_id = _user_id(viewer)
    src = _source_card("Shared template")
    _new_item(src["id"], "a", checked=True)
    owner_label = _label("owner-only")
    _attach(src["id"], owner_label)
    _share(src["id"], viewer_id, "view")
    viewer_label = _label("viewer-label", token=viewer)
    _attach(src["id"], viewer_label, token=viewer)

    owner_cursor = _cursor()
    copy = req(f"api/checklist/{src['id']}/duplicate", "post", access_token=viewer)

    assert copy["owner_id"] == viewer_id
    assert copy["my_permission"] == "owner"
    # Only the viewer's own labels travel; the owner's label stays private.
    assert [l["id"] for l in copy["labels"]] == [viewer_label]
    items = _items(copy["id"], token=viewer)
    assert [(i["text"], i["state"]["checked"]) for i in items] == [("a", False)]
    # The source owner cannot see the copy, and it is not in their delta feed.
    req(f"api/checklist/{copy['id']}", expected_http_code=403)
    owner_delta = _changes(since=owner_cursor)
    assert copy["id"] not in [c["id"] for c in owner_delta["checklists"]]

    req(f"api/checklist/{copy['id']}", "delete", access_token=viewer)
    req(f"api/checklist/{src['id']}", "delete")


def test_duplicate_surfaces_in_the_delta_feed_with_all_items():
    src = _source_card("Delta dup")
    _new_item(src["id"], "a")
    _new_item(src["id"], "b", checked=True)
    before = _cursor()

    copy = req(f"api/checklist/{src['id']}/duplicate", "post")

    delta = _changes(since=before)
    assert copy["id"] in [c["id"] for c in delta["checklists"]]
    copied = [i for i in delta["items"] if i["checklist_id"] == copy["id"]]
    assert sorted(i["text"] for i in copied) == ["a", "b"]
    assert all(i["state"]["checked"] is False for i in copied)

    req(f"api/checklist/{copy['id']}", "delete")
    req(f"api/checklist/{src['id']}", "delete")


def test_duplicate_needs_access():
    stranger = _make_user_token("copy-stranger")
    src = _source_card("Private")
    req(
        f"api/checklist/{src['id']}/duplicate",
        "post",
        access_token=stranger,
        expected_http_code=403,
    )
    req(f"api/checklist/{src['id']}", "delete")


# ── move checked items ────────────────────────────────────────────────────────


def test_move_checked_moves_checked_items_in_order():
    src = _source_card("Shopping")
    a = _new_item(src["id"], "milk", checked=True)
    b = _new_item(src["id"], "bread")
    c = _new_item(src["id"], "eggs", checked=True, indentation=1)
    label_id = _label("groceries")
    _attach(src["id"], label_id)

    done = req(f"api/checklist/{src['id']}/items/move-checked", "post")

    assert done["name"] == "Shopping (done)"
    assert done["text"] == src["text"]
    assert done["color_id"] == src["color_id"]
    assert [l["id"] for l in done["labels"]] == [label_id]
    moved = _items(done["id"])
    assert [i["text"] for i in moved] == ["milk", "eggs"]
    assert all(i["state"]["checked"] is True for i in moved)
    assert [i["position"]["indentation"] for i in moved] == [0, 1]

    # Only the unchecked item stays in the source; the moved originals are
    # tombstoned (410), not hard-deleted, so the removal syncs.
    assert [i["id"] for i in _items(src["id"])] == [b]
    for gone in (a, c):
        req(f"api/checklist/{src['id']}/item/{gone}", expected_http_code=410)

    req(f"api/checklist/{done['id']}", "delete")
    req(f"api/checklist/{src['id']}", "delete")


def test_move_checked_is_idempotent_with_client_id():
    src = _source_card("Move replay")
    _new_item(src["id"], "a", checked=True)
    later = _new_item(src["id"], "b")
    new_id = str(uuid.uuid4())

    first = req(
        f"api/checklist/{src['id']}/items/move-checked", "post", b={"id": new_id}
    )
    assert first["id"] == new_id
    # Something else gets checked before the replay arrives: the replay must not
    # move it (the first call already did the move).
    req(f"api/checklist/{src['id']}/item/{later}/state", "patch", b={"checked": True})
    second = req(
        f"api/checklist/{src['id']}/items/move-checked", "post", b={"id": new_id}
    )
    assert second["id"] == new_id
    assert [i["text"] for i in _items(new_id)] == ["a"]
    assert [i["id"] for i in _items(src["id"])] == [later]

    req(f"api/checklist/{new_id}", "delete")
    req(f"api/checklist/{src['id']}", "delete")


def test_move_checked_with_nothing_checked_creates_an_empty_card():
    src = _source_card("Nothing done")
    keep = _new_item(src["id"], "a")
    done = req(f"api/checklist/{src['id']}/items/move-checked", "post")
    assert _items(done["id"]) == []
    assert [i["id"] for i in _items(src["id"])] == [keep]
    req(f"api/checklist/{done['id']}", "delete")
    req(f"api/checklist/{src['id']}", "delete")


def test_move_checked_reaches_collaborators_through_the_delta_feed():
    collab = _make_user_token("move-delta-collab")
    src = _source_card("Shared move")
    _share(src["id"], _user_id(collab), "edit")
    a = _new_item(src["id"], "a", checked=True)
    b = _new_item(src["id"], "b", checked=True)
    _new_item(src["id"], "c")
    owner_before = _cursor()
    collab_before = _cursor(token=collab)

    done = req(f"api/checklist/{src['id']}/items/move-checked", "post")

    # The collaborator sees the items leave the source ...
    collab_delta = _changes(since=collab_before, token=collab)
    assert a in collab_delta["item_tombstones"]
    assert b in collab_delta["item_tombstones"]
    # ... but never gets the new card (it is the owner's alone).
    assert done["id"] not in [c["id"] for c in collab_delta["checklists"]]
    # The actor's other devices get the tombstones and the whole new card.
    owner_delta = _changes(since=owner_before)
    assert a in owner_delta["item_tombstones"]
    assert done["id"] in [c["id"] for c in owner_delta["checklists"]]
    moved = [i for i in owner_delta["items"] if i["checklist_id"] == done["id"]]
    assert sorted(i["text"] for i in moved) == ["a", "b"]
    assert all(i["state"]["checked"] is True for i in moved)

    req(f"api/checklist/{done['id']}", "delete")
    req(f"api/checklist/{src['id']}", "delete")


def test_move_checked_requires_edit_and_the_card_belongs_to_the_actor():
    collab = _make_user_token("move-perm-collab")
    collab_id = _user_id(collab)
    src = _source_card("Move perms")
    _new_item(src["id"], "a", checked=True)

    _share(src["id"], collab_id, "check")
    req(
        f"api/checklist/{src['id']}/items/move-checked",
        "post",
        access_token=collab,
        expected_http_code=403,
    )
    _share(src["id"], collab_id, "edit")
    done = req(
        f"api/checklist/{src['id']}/items/move-checked",
        "post",
        access_token=collab,
    )
    assert done["owner_id"] == collab_id
    assert [i["text"] for i in _items(done["id"], token=collab)] == ["a"]
    assert _items(src["id"]) == []
    # The source owner cannot see the collaborator's new card.
    req(f"api/checklist/{done['id']}", expected_http_code=403)

    req(f"api/checklist/{done['id']}", "delete", access_token=collab)
    req(f"api/checklist/{src['id']}", "delete")


def test_copy_of_a_deleted_card_is_gone():
    src = _source_card("Tombstoned source")
    req(f"api/checklist/{src['id']}", "delete")
    req(f"api/checklist/{src['id']}/duplicate", "post", expected_http_code=410)
    req(
        f"api/checklist/{src['id']}/items/move-checked",
        "post",
        expected_http_code=410,
    )
