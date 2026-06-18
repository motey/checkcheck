from utils import req, dict_must_contain, list_contains_dict_that_must_contain

def test_checklist_position_endpoints():
    current_list_count = req("api/checklist")["total_count"]

    # Create 3 new checklists
    target_count = current_list_count + 3
    test_item_id = None
    for i in range(current_list_count, target_count):
        res = req("api/checklist", "post", b={"name": f"CList{i}", "text": f"list{i} text"})
        test_item_id = res["id"]
        assert "checklist_id" not in res["position"], (
            "Nested position object must not expose checklist_id"
        )

    # List all positions
    from checkcheckserver.api.routes.routes_checklist_position import list_checklist_positions
    res = req("api/position")
    dict_must_contain(res, {"total_count": target_count}, exception_dict_identifier="list positions")
    assert len(res["items"]) == target_count
    dict_must_contain(res["items"][0], required_keys=["checked_items_collapsed", "index", "pinned"])

    # Get position for a specific checklist
    res = req(f"api/checklist/{test_item_id}/position")
    dict_must_contain(res, {"checklist_id": test_item_id})

    # Update position
    res = req(f"api/checklist/{test_item_id}/position", "patch", b={"index": 0.2})
    dict_must_contain(res, {"index": 0.2})
