"""WI-4 — delta feed: ``GET /api/changes?since=<cursor>``.

One endpoint that tells a device everything visible to it that changed since its
cursor. The cursor is a global, server-set, strictly-monotonic ``server_seq``
stamped on every syncable write; a client stores the ``next_cursor`` it gets back
and passes it as ``since`` next time.

These drive the real REST routes so the whole spine is exercised together:
server_seq stamping (model + mapper events), the per-entity change/tombstone
queries, access-gain (share delivers the whole tree) and access-loss
(``removed_checklist_ids``), and the ``full_resync`` fallback. Two simulated
devices converging through the endpoint is the core property under test.
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


def _changes(
    since: int = 0,
    known: Optional[List[str]] = None,
    token: Optional[str] = None,
) -> Dict:
    q: Dict = {"since": since}
    if known is not None:
        # req() joins a list value with commas — exactly the format the endpoint's
        # `known` param parses.
        q["known"] = known
    return req("api/changes", q=q, access_token=token)


def _cursor(token: Optional[str] = None) -> int:
    """Current server high-water mark, as the caller would see it."""
    return _changes(since=0, token=token)["next_cursor"]


def _cl_ids(delta: Dict) -> List[str]:
    return [c["id"] for c in delta["checklists"]]


def _item_ids(delta: Dict) -> List[str]:
    return [i["id"] for i in delta["items"]]


# ── ordinary changes ──────────────────────────────────────────────────────────


def test_new_checklist_and_item_appear_then_cursor_converges():
    start = _cursor()

    cl_id = req("api/checklist", "post", b={"name": "delta card"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "hello"})["id"]

    delta = _changes(since=start)
    assert cl_id in _cl_ids(delta), "new checklist must appear in the delta"
    assert item_id in _item_ids(delta), "new item must appear in the delta"

    # Advancing to the returned cursor and re-pulling yields none of our rows —
    # the device has converged.
    converged = _changes(since=delta["next_cursor"])
    assert cl_id not in _cl_ids(converged)
    assert item_id not in _item_ids(converged)

    req(f"api/checklist/{cl_id}", "delete")


def test_item_state_change_surfaces_the_item():
    cl_id = req("api/checklist", "post", b={"name": "state host"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "check me"})["id"]

    start = _cursor()
    req(f"api/checklist/{cl_id}/item/{item_id}/state", "patch", b={"checked": True})

    delta = _changes(since=start)
    item = find_first_dict_in_list(delta["items"], {"id": item_id})
    assert item["state"]["checked"] is True, "checked-state change must surface"

    req(f"api/checklist/{cl_id}", "delete")


def test_item_position_change_surfaces_the_item():
    cl_id = req("api/checklist", "post", b={"name": "pos host"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "movable"})["id"]

    start = _cursor()
    req(f"api/checklist/{cl_id}/item/{item_id}/position", "patch", b={"index": 7.25})

    delta = _changes(since=start)
    item = find_first_dict_in_list(delta["items"], {"id": item_id})
    assert item["position"]["index"] == 7.25

    req(f"api/checklist/{cl_id}", "delete")


def test_checklist_rename_surfaces_card_not_stale():
    cl_id = req("api/checklist", "post", b={"name": "before"})["id"]

    start = _cursor()
    req(f"api/checklist/{cl_id}", "patch", b={"name": "after"})

    delta = _changes(since=start)
    card = find_first_dict_in_list(delta["checklists"], {"id": cl_id})
    assert card["name"] == "after"
    assert card["my_permission"] == "owner", "owner must be reported for own card"

    req(f"api/checklist/{cl_id}", "delete")


# ── tombstones ────────────────────────────────────────────────────────────────


def test_item_and_checklist_tombstones_are_reported():
    cl_id = req("api/checklist", "post", b={"name": "doomed"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "x"})["id"]

    start = _cursor()

    # Delete the item first and confirm its tombstone reaches the delta while the
    # card is still accessible.
    req(f"api/checklist/{cl_id}/item/{item_id}", "delete")
    delta = _changes(since=start)
    assert item_id in delta["item_tombstones"], "deleted item must be tombstoned"

    # Now delete the card: its tombstone is reported (and it drops out of the
    # access set, so the item tombstone is subsumed by the card tombstone).
    req(f"api/checklist/{cl_id}", "delete")
    delta2 = _changes(since=start)
    assert cl_id in delta2["checklist_tombstones"], "deleted card must be tombstoned"
    assert cl_id not in _cl_ids(delta2), "tombstoned card must not be a live change"


def test_label_change_and_tombstone_are_reported():
    start = _cursor()
    label_id = req("api/label", "post", b={"display_name": "delta-label"})["id"]

    delta = _changes(since=start)
    assert label_id in [l["id"] for l in delta["labels"]], "new label must appear"

    mid = delta["next_cursor"]
    req(f"api/label/{label_id}", "delete")
    delta2 = _changes(since=mid)
    assert label_id in delta2["label_tombstones"], "deleted label must be tombstoned"
    assert label_id not in [l["id"] for l in delta2["labels"]]


# ── access gained / lost ──────────────────────────────────────────────────────


def test_gaining_access_delivers_the_whole_tree():
    other = _make_user_token("wi4-gain")
    other_id = _user_id(other)

    # Admin owns a card with an item, both created BEFORE the share — so their
    # server_seq predates the collaborator grant.
    cl_id = req("api/checklist", "post", b={"name": "shared tree"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "predates grant"})[
        "id"
    ]

    base = _cursor(token=other)  # other's cursor before any access
    assert cl_id not in _cl_ids(_changes(since=base, token=other))

    # Share to `other` (instant-accepted with the invite flag off).
    req(f"api/checklist/{cl_id}/shares/{other_id}", "put", b={"permission": "edit"})

    delta = _changes(since=base, token=other)
    assert cl_id in _cl_ids(delta), "gained card must be delivered"
    # The item predates the grant (lower seq) but must still arrive in full.
    assert item_id in _item_ids(delta), "children of a gained card must be delivered"
    card = find_first_dict_in_list(delta["checklists"], {"id": cl_id})
    assert card["my_permission"] == "edit"

    req(f"api/checklist/{cl_id}", "delete")


def test_losing_access_is_reported_in_removed_ids():
    other = _make_user_token("wi4-lose")
    other_id = _user_id(other)

    cl_id = req("api/checklist", "post", b={"name": "revoke me"})["id"]
    req(f"api/checklist/{cl_id}/shares/{other_id}", "put", b={"permission": "edit"})

    # `other` has the card cached now; capture their cursor.
    after_share = _cursor(token=other)
    assert cl_id in _cl_ids(_changes(since=0, token=other))

    # Admin revokes the share.
    req(f"api/checklist/{cl_id}/shares/{other_id}", "delete")

    # `other` reconnects and tells the server which cards it still caches.
    delta = _changes(since=after_share, known=[cl_id], token=other)
    assert cl_id in delta["removed_checklist_ids"], "revoked card must be reported"

    req(f"api/checklist/{cl_id}", "delete")


# ── full resync ───────────────────────────────────────────────────────────────


def test_cursor_ahead_of_server_triggers_full_resync():
    cl_id = req("api/checklist", "post", b={"name": "resync card"})["id"]
    high_water = _cursor()

    delta = _changes(since=high_water + 10_000_000)
    assert delta["full_resync"] is True, "a cursor past the server must full-resync"
    # A full resync is computed as if since=0, so it returns the caller's whole
    # accessible state.
    assert cl_id in _cl_ids(delta)
    assert delta["next_cursor"] >= high_water

    req(f"api/checklist/{cl_id}", "delete")


def test_bootstrap_from_zero_returns_current_state():
    cl_id = req("api/checklist", "post", b={"name": "bootstrap card"})["id"]
    delta = _changes(since=0)
    assert delta["full_resync"] is False
    assert cl_id in _cl_ids(delta), "since=0 bootstrap must include current cards"
    req(f"api/checklist/{cl_id}", "delete")


# ── two-device convergence ────────────────────────────────────────────────────


def test_two_devices_converge_through_the_feed():
    """A card edited across several ops on 'device A' is applied by 'device B'
    walking its cursor forward; a final pull is empty (converged)."""
    cl_id = req("api/checklist", "post", b={"name": "conv"})["id"]

    cursor = _cursor()  # device B starts here

    # Device A makes a burst of edits.
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "one"})["id"]
    req(f"api/checklist/{cl_id}", "patch", b={"name": "conv-renamed"})
    req(f"api/checklist/{cl_id}/item/{item_id}", "patch", b={"text": "one-edited"})
    req(f"api/checklist/{cl_id}/item/{item_id}/state", "patch", b={"checked": True})

    # Device B pulls once and sees the converged end-state (LWW: latest values).
    delta = _changes(since=cursor)
    card = find_first_dict_in_list(delta["checklists"], {"id": cl_id})
    assert card["name"] == "conv-renamed"
    item = find_first_dict_in_list(delta["items"], {"id": item_id})
    assert item["text"] == "one-edited"
    assert item["state"]["checked"] is True

    # Re-pulling at the new cursor is empty for our rows — B has converged with A.
    converged = _changes(since=delta["next_cursor"])
    assert cl_id not in _cl_ids(converged)
    assert item_id not in _item_ids(converged)

    req(f"api/checklist/{cl_id}", "delete")
