"""WI-2 — tombstones / soft delete.

Deletes of syncable *parent* rows (checklist, checklist_item, label) must become
tombstones: the row is soft-deleted (``deleted_at`` set), disappears from every
read, and can never be resurrected by a stale offline edit. Writes to a tombstoned
row return ``410 Gone`` (terminal for the outbox), distinct from ``404`` (never
existed); re-issuing a delete is idempotent success (safe outbox replay).

These drive the real REST routes so the model + CRUD masking + endpoint behaviour
are all exercised together.
"""

from utils import req, dict_must_contain


# ── Checklist tombstones ──────────────────────────────────────────────────────


def test_deleted_checklist_disappears_from_grid():
    keep = req("api/checklist", "post", b={"name": "keep me"})["id"]
    doomed = req("api/checklist", "post", b={"name": "delete me"})["id"]

    req(f"api/checklist/{doomed}", "delete")

    grid = req("api/checklist")["items"]
    ids = [c["id"] for c in grid]
    assert doomed not in ids, "tombstoned checklist must not appear in the grid"
    assert keep in ids, "unrelated checklist must still appear"

    req(f"api/checklist/{keep}", "delete")


def test_read_tombstoned_checklist_returns_410():
    cl_id = req("api/checklist", "post", b={"name": "gone soon"})["id"]
    req(f"api/checklist/{cl_id}", "delete")

    # A direct GET of a tombstoned card is 410 Gone (it existed and is gone),
    # never 404 (which would mean "never existed").
    req(f"api/checklist/{cl_id}", expected_http_code=410)


def test_stale_edit_cannot_resurrect_checklist():
    cl_id = req("api/checklist", "post", b={"name": "orig"})["id"]
    req(f"api/checklist/{cl_id}", "delete")

    # A late PATCH from a client that never learned about the delete is rejected
    # with 410 and must not bring the card back.
    req(f"api/checklist/{cl_id}", "patch", b={"name": "zombie"}, expected_http_code=410)

    grid_ids = [c["id"] for c in req("api/checklist")["items"]]
    assert cl_id not in grid_ids, "stale edit resurrected a tombstoned checklist"


def test_delete_checklist_is_idempotent():
    cl_id = req("api/checklist", "post", b={"name": "double delete"})["id"]
    req(f"api/checklist/{cl_id}", "delete")
    # Replaying the delete (outbox retry) is terminal-gone, not a server error.
    req(f"api/checklist/{cl_id}", "delete", tolerated_error_codes=[410])


# ── Item tombstones ───────────────────────────────────────────────────────────


def test_deleted_item_disappears_but_siblings_remain():
    cl_id = req("api/checklist", "post", b={"name": "item tombstones"})["id"]
    a = req(f"api/checklist/{cl_id}/item", "post", b={"text": "a"})["id"]
    b = req(f"api/checklist/{cl_id}/item", "post", b={"text": "b"})["id"]

    req(f"api/checklist/{cl_id}/item/{a}", "delete")

    items = req(f"api/checklist/{cl_id}/item")["items"]
    ids = [i["id"] for i in items]
    assert a not in ids, "tombstoned item must not be listed"
    assert b in ids, "sibling item must survive the delete"

    req(f"api/checklist/{cl_id}", "delete")


def test_stale_edit_cannot_resurrect_item():
    cl_id = req("api/checklist", "post", b={"name": "no zombie items"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "orig"})["id"]

    req(f"api/checklist/{cl_id}/item/{item_id}", "delete")

    # Stale text edit → 410, and the item stays gone.
    req(
        f"api/checklist/{cl_id}/item/{item_id}",
        "patch",
        b={"text": "zombie"},
        expected_http_code=410,
    )
    # A stale check/uncheck (item-state) is likewise terminal.
    req(
        f"api/checklist/{cl_id}/item/{item_id}/state",
        "patch",
        b={"checked": True},
        expected_http_code=410,
    )

    ids = [i["id"] for i in req(f"api/checklist/{cl_id}/item")["items"]]
    assert item_id not in ids, "stale edit resurrected a tombstoned item"

    req(f"api/checklist/{cl_id}", "delete")


def test_delete_item_is_idempotent():
    cl_id = req("api/checklist", "post", b={"name": "idem item"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "x"})["id"]

    assert req(f"api/checklist/{cl_id}/item/{item_id}", "delete") is True
    # Replaying the delete returns success (no duplicate work, no error).
    assert req(f"api/checklist/{cl_id}/item/{item_id}", "delete") is True

    req(f"api/checklist/{cl_id}", "delete")


def test_items_of_tombstoned_checklist_are_masked():
    cl_id = req("api/checklist", "post", b={"name": "parent tombstone"})["id"]
    req(f"api/checklist/{cl_id}/item", "post", b={"text": "child"})

    req(f"api/checklist/{cl_id}", "delete")

    # Access to the whole card is gone → listing/creating items under it is 410.
    req(f"api/checklist/{cl_id}/item", expected_http_code=410)
    req(
        f"api/checklist/{cl_id}/item",
        "post",
        b={"text": "orphan"},
        expected_http_code=410,
    )


# ── Label tombstones ──────────────────────────────────────────────────────────


def test_deleted_label_disappears_and_stays_gone():
    label_id = req("api/label", "post", b={"display_name": "temp"})["id"]
    req(f"api/label/{label_id}", "delete")

    ids = [l["id"] for l in req("api/label")]
    assert label_id not in ids, "tombstoned label must not be listed"

    # Stale edit → 410, no resurrection.
    req(
        f"api/label/{label_id}",
        "patch",
        b={"display_name": "zombie"},
        expected_http_code=410,
    )
    ids_after = [l["id"] for l in req("api/label")]
    assert label_id not in ids_after, "stale edit resurrected a tombstoned label"


def test_tombstoned_label_chips_are_masked_on_cards():
    label_id = req("api/label", "post", b={"display_name": "chip"})["id"]
    cl_id = req("api/checklist", "post", b={"name": "labelled"})["id"]
    req(f"api/checklist/{cl_id}/label/{label_id}", "put")

    # Sanity: chip is present before delete.
    chips = [l["id"] for l in req(f"api/checklist/{cl_id}/label")]
    assert label_id in chips

    # Deleting the label (its link row is left in place) hides the chip.
    req(f"api/label/{label_id}", "delete")
    chips_after = [l["id"] for l in req(f"api/checklist/{cl_id}/label")]
    assert label_id not in chips_after, "chip of a tombstoned label must be masked"

    req(f"api/checklist/{cl_id}", "delete")
