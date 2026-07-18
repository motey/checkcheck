from typing import List, Dict
from utils import req, dict_must_contain, list_contains_dict_that_must_contain

def test_checklist_crud():
    # Create first list
    res = req(
        "api/checklist",
        "post",
        b={"name": "Dont Forget", "text": "Items you should not forget", "color_id": "yellow"},
    )
    first_checklist_id = res["id"]
    dict_must_contain(
        res,
        required_keys=["id", "color", "position"],
        exception_dict_identifier="create first checklist",
    )
    dict_must_contain(
        res["position"],
        required_keys=["archived", "pinned", "checked_items_collapsed", "index"],
    )
    first_checklist = res

    # Create second list
    res = req(
        "api/checklist",
        "post",
        b={"name": "Shopping", "text": "Items you have to buy", "color_id": "yellow"},
    )
    second_checklist_id = res["id"]
    dict_must_contain(res["color"], {"id": "yellow"})

    # List both
    res = req("api/checklist")
    dict_must_contain(res, {"total_count": 2}, required_keys=["items"])
    assert len(res["items"]) == 2

    # Update second list
    res = req(
        f"api/checklist/{second_checklist_id}",
        "patch",
        b={"text": "Things you have to buy"},
    )
    dict_must_contain(
        res,
        {"name": "Shopping", "text": "Things you have to buy", "color_id": "yellow"},
        required_keys=["position"],
    )

    # Delete second list
    req(f"api/checklist/{second_checklist_id}", "delete")
    res = req("api/checklist")
    dict_must_contain(res, {"total_count": 1}, required_keys=["items"])


def test_checklist_suggest_existing_items_flag():
    # New cards default to suggesting existing items (Keep-style dedup helper).
    res = req("api/checklist", "post", b={"name": "Groceries"})
    checklist_id = res["id"]
    dict_must_contain(res, {"suggest_existing_items": True})

    # The per-card toggle round-trips through PATCH.
    res = req(
        f"api/checklist/{checklist_id}",
        "patch",
        b={"suggest_existing_items": False},
    )
    dict_must_contain(res, {"suggest_existing_items": False})

    # And is reflected on a subsequent read.
    res = req(f"api/checklist/{checklist_id}")
    dict_must_contain(res, {"suggest_existing_items": False})
