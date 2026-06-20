"""Authorization tests for item endpoints addressed as /checklist/{id}/item/{item_id}/...

Route permission guards authorize the *checklist* named in the path, but the
item / item-state / item-position operations address the item by its own id. If a
route does not also verify the item belongs to that checklist, a user with access
to checklist A can read or mutate an item that lives in checklist B (which they
have no access to) by calling /checklist/{A}/item/{item_in_B}/... — a
cross-checklist IDOR.

The global default login (set by conftest) is the admin user; it owns the
"victim" checklist B here. The attacker is a separate user that owns only
checklist A and has no share on B.
"""

from utils import (
    req,
    authorize_for_access_token,
    create_test_user,
)


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _setup_attacker_a_and_victim_b():
    """attacker owns checklist A; admin (victim) owns checklist B with one item.
    The attacker has no access whatsoever to B. Returns
    (attacker_token, checklist_a, checklist_b, item_b)."""
    attacker_token = _make_user_token("xcheck-attacker")
    checklist_a = req(
        "api/checklist",
        "post",
        b={"name": "A", "color_id": "yellow"},
        access_token=attacker_token,
    )["id"]

    # admin / default token owns B
    checklist_b = req("api/checklist", "post", b={"name": "B", "color_id": "yellow"})["id"]
    item_b = req(f"api/checklist/{checklist_b}/item", "post", b={"text": "secret"})["id"]

    # sanity: attacker genuinely has no access to B
    req(f"api/checklist/{checklist_b}", access_token=attacker_token, expected_http_code=401)
    return attacker_token, checklist_a, checklist_b, item_b


def test_cannot_read_foreign_item_via_path_mismatch():
    attacker_token, checklist_a, _, item_b = _setup_attacker_a_and_victim_b()
    # Reading B's item through the attacker's own checklist A must not work.
    req(
        f"api/checklist/{checklist_a}/item/{item_b}",
        access_token=attacker_token,
        expected_http_code=404,
    )


def test_cannot_read_foreign_item_state_via_path_mismatch():
    attacker_token, checklist_a, _, item_b = _setup_attacker_a_and_victim_b()
    req(
        f"api/checklist/{checklist_a}/item/{item_b}/state",
        access_token=attacker_token,
        expected_http_code=404,
    )


def test_cannot_toggle_foreign_item_state_via_path_mismatch():
    attacker_token, checklist_a, checklist_b, item_b = _setup_attacker_a_and_victim_b()
    req(
        f"api/checklist/{checklist_a}/item/{item_b}/state",
        "patch",
        b={"checked": True},
        access_token=attacker_token,
        expected_http_code=404,
    )
    # The victim's item must remain unchecked (the write must not have landed).
    state = req(f"api/checklist/{checklist_b}/item/{item_b}/state")
    assert state["checked"] is False, "foreign item state was mutated across checklists"


def test_cannot_read_foreign_item_position_via_path_mismatch():
    attacker_token, checklist_a, _, item_b = _setup_attacker_a_and_victim_b()
    req(
        f"api/checklist/{checklist_a}/item/{item_b}/position",
        access_token=attacker_token,
        expected_http_code=404,
    )


def test_cannot_update_foreign_item_position_via_path_mismatch():
    attacker_token, checklist_a, _, item_b = _setup_attacker_a_and_victim_b()
    req(
        f"api/checklist/{checklist_a}/item/{item_b}/position",
        "patch",
        b={"index": 99.0},
        access_token=attacker_token,
        expected_http_code=404,
    )
