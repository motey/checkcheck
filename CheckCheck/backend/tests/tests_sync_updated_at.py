"""WI-1 — the server-set ``updated_at`` version signal.

Every syncable row must carry an ``updated_at`` that the *server* bumps on every
write (never the client). The 2.0 delta feed (WI-4) reads it for last-writer-wins
ordering, so a mutation that fails to bump it would make a change invisible to
offline clients. These tests drive each syncable entity through its real CRUD
route and assert the timestamp is exposed and strictly advances on update.
"""

import datetime
import time

from utils import req, dict_must_contain


def _ts(value: str) -> datetime.datetime:
    """Parse an ``updated_at`` field from an API response."""
    assert value is not None, "updated_at missing from response"
    return datetime.datetime.fromisoformat(value)


# A short pause before each update. Timestamps have microsecond resolution and
# HTTP round-trips already take milliseconds, but this removes any doubt that a
# same-instant collision could make a real bump look like a no-op.
def _tick() -> None:
    time.sleep(0.01)


def test_checklist_updated_at_present_and_bumps():
    cl = req("api/checklist", "post", b={"name": "u_at list"})
    cl_id = cl["id"]
    dict_must_contain(cl, required_keys=["updated_at"])
    before = _ts(cl["updated_at"])

    _tick()
    patched = req(f"api/checklist/{cl_id}", "patch", b={"name": "renamed"})
    after = _ts(patched["updated_at"])
    assert after > before, f"checklist updated_at did not bump: {before} -> {after}"

    req(f"api/checklist/{cl_id}", "delete")


def test_item_updated_at_present_and_bumps():
    cl_id = req("api/checklist", "post", b={"name": "item u_at"})["id"]
    item = req(f"api/checklist/{cl_id}/item", "post", b={"text": "orig"})
    item_id = item["id"]
    # The item and both of its child rows (position, state) each expose their own
    # version — they are independently syncable rows.
    dict_must_contain(item, required_keys=["updated_at"])
    dict_must_contain(item["position"], required_keys=["updated_at"])
    dict_must_contain(item["state"], required_keys=["updated_at"])
    before = _ts(item["updated_at"])

    _tick()
    patched = req(f"api/checklist/{cl_id}/item/{item_id}", "patch", b={"text": "edited"})
    after = _ts(patched["updated_at"])
    assert after > before, f"item updated_at did not bump: {before} -> {after}"

    req(f"api/checklist/{cl_id}", "delete")


def test_item_state_updated_at_bumps_on_toggle():
    cl_id = req("api/checklist", "post", b={"name": "state u_at"})["id"]
    item = req(f"api/checklist/{cl_id}/item", "post", b={"text": "toggle me"})
    item_id = item["id"]
    before = _ts(item["state"]["updated_at"])

    _tick()
    toggled = req(
        f"api/checklist/{cl_id}/item/{item_id}/state", "patch", b={"checked": True}
    )
    after = _ts(toggled["updated_at"])
    assert after > before, f"state updated_at did not bump: {before} -> {after}"

    req(f"api/checklist/{cl_id}", "delete")


def test_item_position_updated_at_bumps_on_move():
    cl_id = req("api/checklist", "post", b={"name": "pos u_at"})["id"]
    for i in range(3):
        req(f"api/checklist/{cl_id}/item", "post", b={"text": f"item {i}"})
    items = req(f"api/checklist/{cl_id}/item")["items"]
    first = items[0]
    before = _ts(first["position"]["updated_at"])

    _tick()
    moved = req(f"api/checklist/{cl_id}/item/{first['id']}/move/bottom", "put")
    after = _ts(moved["updated_at"])
    assert after > before, f"position updated_at did not bump: {before} -> {after}"

    req(f"api/checklist/{cl_id}", "delete")


def test_label_updated_at_present_and_bumps():
    label = req("api/label", "post", b={"display_name": "orig label"})
    label_id = label["id"]
    dict_must_contain(label, required_keys=["updated_at"])
    before = _ts(label["updated_at"])

    _tick()
    patched = req(f"api/label/{label_id}", "patch", b={"display_name": "new label"})
    after = _ts(patched["updated_at"])
    assert after > before, f"label updated_at did not bump: {before} -> {after}"

    req(f"api/label/{label_id}", "delete")
