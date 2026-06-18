from utils import req, dict_must_contain

def test_item_state_check_and_uncheck():
    # Setup: fresh checklist with two items
    checklist = req("api/checklist", "post", b={"name": "State Test List"})
    cl_id = checklist["id"]
    item_a = req(f"api/checklist/{cl_id}/item", "post", b={"text": "Item A"})
    item_b = req(f"api/checklist/{cl_id}/item", "post", b={"text": "Item B"})
    a_id = item_a["id"]
    b_id = item_b["id"]

    # Newly created items are unchecked
    state = req(f"api/checklist/{cl_id}/item/{a_id}/state")
    dict_must_contain(state, {"checked": False})

    # Check item A
    res = req(f"api/checklist/{cl_id}/item/{a_id}/state", "patch", b={"checked": True})
    dict_must_contain(res, {"checked": True})

    # GET confirms the new state
    state = req(f"api/checklist/{cl_id}/item/{a_id}/state")
    dict_must_contain(state, {"checked": True})

    # Item B is still unchecked
    state_b = req(f"api/checklist/{cl_id}/item/{b_id}/state")
    dict_must_contain(state_b, {"checked": False})

    # Uncheck item A
    res = req(f"api/checklist/{cl_id}/item/{a_id}/state", "patch", b={"checked": False})
    dict_must_contain(res, {"checked": False})

    state = req(f"api/checklist/{cl_id}/item/{a_id}/state")
    dict_must_contain(state, {"checked": False})

    # Clean up
    req(f"api/checklist/{cl_id}", "delete")

def test_item_state_reflected_in_item_list():
    """Checking an item is visible when listing items."""
    checklist = req("api/checklist", "post", b={"name": "State Reflection List"})
    cl_id = checklist["id"]
    item = req(f"api/checklist/{cl_id}/item", "post", b={"text": "Buy milk"})
    item_id = item["id"]

    req(f"api/checklist/{cl_id}/item/{item_id}/state", "patch", b={"checked": True})

    items = req(f"api/checklist/{cl_id}/item")["items"]
    match = next((i for i in items if i["id"] == item_id), None)
    assert match is not None
    dict_must_contain(match["state"], {"checked": True})

    req(f"api/checklist/{cl_id}", "delete")
