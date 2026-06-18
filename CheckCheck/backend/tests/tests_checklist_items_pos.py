import decimal
from utils import req, dict_must_contain

def test_checklist_item_position_endpoints():
    # Setup: create a checklist with 5 items
    checklist = req("api/checklist", "post", b={"name": "Items to sort", "text": "Sorting test"})
    checklist_id = checklist["id"]

    for i in range(1, 6):
        req(f"api/checklist/{checklist_id}/item", "post", b={"text": f"Item {i}"})

    items = req(f"api/checklist/{checklist_id}/item")["items"]
    assert len(items) == 5

    # Items arrive oldest-first (lowest index = first in list)
    expected_order = [
        {"text": "Item 1", "position": {"index": 0.4, "indentation": 0}},
        {"text": "Item 2", "position": {"index": 0.8, "indentation": 0}},
        {"text": "Item 3", "position": {"index": 1.2, "indentation": 0}},
        {"text": "Item 4", "position": {"index": 1.6, "indentation": 0}},
        {"text": "Item 5", "position": {"index": 2.0, "indentation": 0}},
    ]
    for idx, expected in enumerate(expected_order):
        dict_must_contain(items[idx], expected, exception_dict_identifier=f"item[{idx}] initial order")

    item_1, item_2, item_3, item_4, item_5 = items

    # Move item_1 to the bottom (it is at the top; bottom = highest index)
    from checkcheckserver.api.routes.routes_checklist_item_pos import move_item_to_bottom_of_checklist
    new_pos_1 = req(
        f"api/checklist/{checklist_id}/item/{item_1['id']}/move/bottom",
        "put",
    )
    assert decimal.Decimal(str(new_pos_1["index"])) == decimal.Decimal(
        str(item_5["position"]["index"])
    ) + decimal.Decimal("0.4"), (
        f"item_1 bottom index expected {item_5['position']['index'] + 0.4}, got {new_pos_1['index']}"
    )

    # Move item_5 to the top (top = lowest index, now below item_2 which is the new lowest)
    from checkcheckserver.api.routes.routes_checklist_item_pos import move_item_to_top_of_checklist
    new_pos_5 = req(
        f"api/checklist/{checklist_id}/item/{item_5['id']}/move/top",
        "put",
    )
    print("item_5 new top pos:", new_pos_5)

    # Move item_2 under item_1
    from checkcheckserver.api.routes.routes_checklist_item_pos import move_item_under_other_item
    new_pos_2 = req(
        f"api/checklist/{checklist_id}/item/{item_2['id']}/move/under/{item_1['id']}",
        "put",
    )
    print("item_2 under item_1:", new_pos_2)

    # Move item_3 above item_4
    from checkcheckserver.api.routes.routes_checklist_item_pos import move_item_above_other_item
    new_pos_3 = req(
        f"api/checklist/{checklist_id}/item/{item_3['id']}/move/above/{item_4['id']}",
        "put",
    )
    print("item_3 above item_4:", new_pos_3)

    # Verify final list is still 5 items
    items_after = req(f"api/checklist/{checklist_id}/item")["items"]
    assert len(items_after) == 5, f"Expected 5 items after moves, got {len(items_after)}"

    # Clean up
    req(f"api/checklist/{checklist_id}", "delete")
