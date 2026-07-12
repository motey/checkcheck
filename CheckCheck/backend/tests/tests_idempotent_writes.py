"""WI-3 — client-generated IDs + idempotent writes.

Offline clients replay their outbox against the existing REST endpoints, so every
mutating write must be safe to apply twice (a network retry or a reconnect
double-send must not duplicate a row or corrupt state):

* **Creates** accept an optional client-supplied UUID. Replaying the same create
  returns the *existing* row (200) instead of duplicating it. Replaying a create
  whose id was since tombstoned is ``410 Gone`` (terminal, must not resurrect);
  an id already owned by someone else / another card is ``409 Conflict`` (a
  genuine UUID collision, also terminal).
* **PATCH / PUT** ops are naturally replay-safe (field-level last-writer-wins):
  applying the same op twice yields identical state.

The terminal-vs-retryable error set the outbox keys off (WI-7):
  * ``409`` — id collision (create) → terminal
  * ``410`` — row tombstoned → terminal (also covered by tests_tombstones)
  * ``403`` — access revoked / insufficient permission → terminal (also covered
    by tests_cross_checklist_access)
  * ``404`` — never existed → terminal
  * network / ``5xx`` → retryable

These drive the real REST routes so model + CRUD + endpoint behaviour are all
exercised together.
"""

import uuid

from utils import req, authorize_for_access_token, create_test_user


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Idempotent create: checklist ──────────────────────────────────────────────


def test_checklist_create_with_client_id_is_idempotent():
    cl_id = _new_id()
    first = req("api/checklist", "post", b={"id": cl_id, "name": "client-id card"})
    assert first["id"] == cl_id, "server must honor the client-supplied id"

    # Replay the exact same create → the existing card, not a duplicate.
    second = req("api/checklist", "post", b={"id": cl_id, "name": "client-id card"})
    assert second["id"] == cl_id

    grid_ids = [c["id"] for c in req("api/checklist")["items"]]
    assert grid_ids.count(cl_id) == 1, "replayed create duplicated the checklist"

    req(f"api/checklist/{cl_id}", "delete")


def test_checklist_create_without_id_still_works():
    # Omitting the id keeps the legacy behaviour: the server assigns one.
    created = req("api/checklist", "post", b={"name": "server-id card"})
    assert created["id"], "server must assign an id when the client omits it"
    req(f"api/checklist/{created['id']}", "delete")


def test_checklist_create_replay_after_delete_is_410():
    cl_id = _new_id()
    req("api/checklist", "post", b={"id": cl_id, "name": "gone"})
    req(f"api/checklist/{cl_id}", "delete")

    # A create replay after the card was tombstoned must not resurrect it.
    req(
        "api/checklist",
        "post",
        b={"id": cl_id, "name": "zombie"},
        expected_http_code=410,
    )
    grid_ids = [c["id"] for c in req("api/checklist")["items"]]
    assert cl_id not in grid_ids, "create replay resurrected a tombstoned checklist"


def test_checklist_create_id_collision_other_user_is_409():
    cl_id = _new_id()
    other_token = _make_user_token("wi3-cl-collide")
    req("api/checklist", "post", b={"id": cl_id, "name": "theirs"}, access_token=other_token)

    # The default (admin) user tries to claim an id that already belongs to
    # someone else's card → 409, and their id is never leaked into admin's grid.
    req("api/checklist", "post", b={"id": cl_id, "name": "mine"}, expected_http_code=409)
    grid_ids = [c["id"] for c in req("api/checklist")["items"]]
    assert cl_id not in grid_ids

    req(f"api/checklist/{cl_id}", "delete", access_token=other_token)


# ── Idempotent create: item ───────────────────────────────────────────────────


def test_item_create_with_client_id_is_idempotent():
    cl_id = req("api/checklist", "post", b={"name": "item host"})["id"]
    item_id = _new_id()

    first = req(f"api/checklist/{cl_id}/item", "post", b={"id": item_id, "text": "a"})
    assert first["id"] == item_id

    second = req(f"api/checklist/{cl_id}/item", "post", b={"id": item_id, "text": "a"})
    assert second["id"] == item_id

    ids = [i["id"] for i in req(f"api/checklist/{cl_id}/item")["items"]]
    assert ids.count(item_id) == 1, "replayed create duplicated the item"

    req(f"api/checklist/{cl_id}", "delete")


def test_item_create_replay_after_delete_is_410():
    cl_id = req("api/checklist", "post", b={"name": "item tombstone host"})["id"]
    item_id = _new_id()
    req(f"api/checklist/{cl_id}/item", "post", b={"id": item_id, "text": "x"})
    req(f"api/checklist/{cl_id}/item/{item_id}", "delete")

    req(
        f"api/checklist/{cl_id}/item",
        "post",
        b={"id": item_id, "text": "zombie"},
        expected_http_code=410,
    )
    ids = [i["id"] for i in req(f"api/checklist/{cl_id}/item")["items"]]
    assert item_id not in ids

    req(f"api/checklist/{cl_id}", "delete")


def test_item_create_id_collision_other_card_is_409():
    card_a = req("api/checklist", "post", b={"name": "A"})["id"]
    card_b = req("api/checklist", "post", b={"name": "B"})["id"]
    item_id = _new_id()
    req(f"api/checklist/{card_a}/item", "post", b={"id": item_id, "text": "in A"})

    # Same item id, different card → collision, not a replay.
    req(
        f"api/checklist/{card_b}/item",
        "post",
        b={"id": item_id, "text": "in B"},
        expected_http_code=409,
    )
    b_ids = [i["id"] for i in req(f"api/checklist/{card_b}/item")["items"]]
    assert item_id not in b_ids

    req(f"api/checklist/{card_a}", "delete")
    req(f"api/checklist/{card_b}", "delete")


# ── Idempotent create: label ──────────────────────────────────────────────────


def test_label_create_with_client_id_is_idempotent():
    label_id = _new_id()
    first = req("api/label", "post", b={"id": label_id, "display_name": "L"})
    assert first["id"] == label_id

    second = req("api/label", "post", b={"id": label_id, "display_name": "L"})
    assert second["id"] == label_id

    ids = [l["id"] for l in req("api/label")]
    assert ids.count(label_id) == 1, "replayed create duplicated the label"

    req(f"api/label/{label_id}", "delete")


def test_label_create_replay_after_delete_is_410():
    label_id = _new_id()
    req("api/label", "post", b={"id": label_id, "display_name": "temp"})
    req(f"api/label/{label_id}", "delete")

    req(
        "api/label",
        "post",
        b={"id": label_id, "display_name": "zombie"},
        expected_http_code=410,
    )
    ids = [l["id"] for l in req("api/label")]
    assert label_id not in ids


# ── PATCH / PUT replay-safety ─────────────────────────────────────────────────


def test_checklist_patch_is_replay_safe():
    cl_id = req("api/checklist", "post", b={"name": "orig"})["id"]

    one = req(f"api/checklist/{cl_id}", "patch", b={"name": "renamed"})
    two = req(f"api/checklist/{cl_id}", "patch", b={"name": "renamed"})
    assert one["name"] == two["name"] == "renamed"

    req(f"api/checklist/{cl_id}", "delete")


def test_item_patch_is_replay_safe():
    cl_id = req("api/checklist", "post", b={"name": "host"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "orig"})["id"]

    one = req(f"api/checklist/{cl_id}/item/{item_id}", "patch", b={"text": "edited"})
    two = req(f"api/checklist/{cl_id}/item/{item_id}", "patch", b={"text": "edited"})
    assert one["text"] == two["text"] == "edited"

    req(f"api/checklist/{cl_id}", "delete")


def test_item_state_patch_is_replay_safe():
    cl_id = req("api/checklist", "post", b={"name": "host"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "check me"})["id"]

    one = req(f"api/checklist/{cl_id}/item/{item_id}/state", "patch", b={"checked": True})
    two = req(f"api/checklist/{cl_id}/item/{item_id}/state", "patch", b={"checked": True})
    assert one["checked"] is True and two["checked"] is True

    req(f"api/checklist/{cl_id}", "delete")


def test_item_position_patch_is_replay_safe():
    cl_id = req("api/checklist", "post", b={"name": "host"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "movable"})["id"]

    one = req(
        f"api/checklist/{cl_id}/item/{item_id}/position", "patch", b={"index": 5.5}
    )
    two = req(
        f"api/checklist/{cl_id}/item/{item_id}/position", "patch", b={"index": 5.5}
    )
    assert one["index"] == two["index"] == 5.5

    req(f"api/checklist/{cl_id}", "delete")


def test_checklist_position_patch_is_replay_safe():
    cl_id = req("api/checklist", "post", b={"name": "host"})["id"]

    one = req(f"api/checklist/{cl_id}/position", "patch", b={"archived": True})
    two = req(f"api/checklist/{cl_id}/position", "patch", b={"archived": True})
    assert one["archived"] is True and two["archived"] is True

    req(f"api/checklist/{cl_id}", "delete")


def test_add_label_to_checklist_is_replay_safe():
    cl_id = req("api/checklist", "post", b={"name": "host"})["id"]
    label_id = req("api/label", "post", b={"display_name": "chip"})["id"]

    req(f"api/checklist/{cl_id}/label/{label_id}", "put")
    # Re-adding the same label is a no-op (exists_ok), not a duplicate/error.
    req(f"api/checklist/{cl_id}/label/{label_id}", "put")

    chips = [l["id"] for l in req(f"api/checklist/{cl_id}/label")]
    assert chips.count(label_id) == 1

    req(f"api/label/{label_id}", "delete")
    req(f"api/checklist/{cl_id}", "delete")


def test_remove_label_from_checklist_is_replay_safe():
    cl_id = req("api/checklist", "post", b={"name": "host"})["id"]
    label_id = req("api/label", "post", b={"display_name": "chip"})["id"]
    req(f"api/checklist/{cl_id}/label/{label_id}", "put")

    req(f"api/checklist/{cl_id}/label/{label_id}", "delete")
    # Replaying the removal of an already-removed link is a no-op success.
    req(f"api/checklist/{cl_id}/label/{label_id}", "delete")

    chips = [l["id"] for l in req(f"api/checklist/{cl_id}/label")]
    assert label_id not in chips

    req(f"api/label/{label_id}", "delete")
    req(f"api/checklist/{cl_id}", "delete")


def test_label_patch_body_id_cannot_repoint_primary_key():
    # LabelUpdate inherits the optional create-time `id`; a PATCH body carrying a
    # foreign id must never move the row (CRUDBase.update guards the PK).
    label_id = req("api/label", "post", b={"display_name": "stable"})["id"]
    bogus_id = _new_id()

    updated = req(
        f"api/label/{label_id}",
        "patch",
        b={"id": bogus_id, "display_name": "renamed"},
    )
    assert updated["id"] == label_id, "PATCH body id must not repoint the primary key"
    assert updated["display_name"] == "renamed"

    ids = [l["id"] for l in req("api/label")]
    assert bogus_id not in ids

    req(f"api/label/{label_id}", "delete")
