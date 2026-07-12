from typing import List, Dict
from utils import req, dict_must_contain, list_contains_dict_that_must_contain

def test_checklist_item_crud():
    # Create a fresh checklist to work with
    checklist = req("api/checklist", "post", b={"name": "Item Test List", "text": "For item CRUD tests"})
    checklist_id = checklist["id"]

    # Create first item
    res = req(f"api/checklist/{checklist_id}/item", "post", b={"text": "Milk"})
    dict_must_contain(res, {"text": "Milk"})

    # Create 9 more items
    for i in range(1, 10):
        req(f"api/checklist/{checklist_id}/item", "post", b={"text": f"Item {i}"})

    # List items
    items = req(f"api/checklist/{checklist_id}/item")["items"]
    assert len(items) == 10, f"Expected 10 items, got {len(items)}"

    # Update an item
    target = items[2]
    new_text = target["text"] + " updated"
    res = req(f"api/checklist/{checklist_id}/item/{target['id']}", "patch", b={"text": new_text})
    dict_must_contain(res, {"text": new_text})

    # Delete an item
    before_count = len(items)
    req(f"api/checklist/{checklist_id}/item/{target['id']}", "delete")
    items_after = req(f"api/checklist/{checklist_id}/item")["items"]
    assert len(items_after) == before_count - 1

    # New item appears last (highest index = bottom of list)
    req(f"api/checklist/{checklist_id}/item", "post", b={"text": "Brand new item"})
    items_final = req(f"api/checklist/{checklist_id}/item")["items"]
    dict_must_contain(items_final[-1], {"text": "Brand new item"})

    # Clean up
    req(f"api/checklist/{checklist_id}", "delete")


def test_list_items_checked_filter():
    """`GET /checklist/{id}/item?checked=` must actually filter by state. The
    filter join was previously built but discarded (the query was never
    reassigned), silently returning every item."""
    checklist_id = req("api/checklist", "post", b={"name": "Checked filter"})["id"]
    a = req(f"api/checklist/{checklist_id}/item", "post", b={"text": "done"})["id"]
    b = req(f"api/checklist/{checklist_id}/item", "post", b={"text": "todo"})["id"]
    req(f"api/checklist/{checklist_id}/item/{a}/state", "patch", b={"checked": True})

    checked = req(f"api/checklist/{checklist_id}/item", q={"checked": True})["items"]
    assert [i["id"] for i in checked] == [a]
    unchecked = req(f"api/checklist/{checklist_id}/item", q={"checked": False})["items"]
    assert [i["id"] for i in unchecked] == [b]

    req(f"api/checklist/{checklist_id}", "delete")
