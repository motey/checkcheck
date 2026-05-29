from _single_test_file_runner import run_all_tests_if_test_file_called

if __name__ == "__main__":
    run_all_tests_if_test_file_called()

import decimal
from utils import req, dict_must_contain


# ── PATCH /user/me ────────────────────────────────────────────────────────────


def test_user_self_update():
    me = req("api/user/me")
    original_display_name = me.get("display_name")

    updated = req("api/user/me", "patch", b={"display_name": "Updated Display Name"})
    dict_must_contain(updated, {"display_name": "Updated Display Name"})

    # Confirm persisted
    me2 = req("api/user/me")
    assert me2["display_name"] == "Updated Display Name"

    # Restore original
    req("api/user/me", "patch", b={"display_name": original_display_name})


# ── GET /checklist/{checklist_id} ─────────────────────────────────────────────


def test_get_single_checklist():
    created = req(
        "api/checklist",
        "post",
        b={"name": "Single Fetch List", "text": "Some description"},
    )
    cl_id = created["id"]

    fetched = req(f"api/checklist/{cl_id}")
    dict_must_contain(fetched, {"id": cl_id, "name": "Single Fetch List"})
    dict_must_contain(fetched, required_keys=["position", "color", "labels"])

    req(f"api/checklist/{cl_id}", "delete")


def test_get_nonexistent_checklist_returns_404():
    import uuid
    fake_id = str(uuid.uuid4())
    req(f"api/checklist/{fake_id}", expected_http_code=404)


# ── PATCH /checklist/{id}/item/{id}/position ─────────────────────────────────


def test_item_position_patch():
    checklist = req("api/checklist", "post", b={"name": "Pos Patch List"})
    cl_id = checklist["id"]

    item = req(f"api/checklist/{cl_id}/item", "post", b={"text": "Reposition me"})
    item_id = item["id"]

    # Directly set an arbitrary index
    new_index = 99.5
    result = req(
        f"api/checklist/{cl_id}/item/{item_id}/position",
        "patch",
        b={"index": new_index},
    )
    assert decimal.Decimal(str(result["index"])) == decimal.Decimal(str(new_index)), (
        f"Expected index {new_index}, got {result['index']}"
    )

    # GET the position to confirm it persisted
    pos = req(f"api/checklist/{cl_id}/item/{item_id}/position")
    assert decimal.Decimal(str(pos["index"])) == decimal.Decimal(str(new_index))

    # Patch indentation
    result2 = req(
        f"api/checklist/{cl_id}/item/{item_id}/position",
        "patch",
        b={"indentation": 2},
    )
    assert result2["indentation"] == 2

    req(f"api/checklist/{cl_id}", "delete")
