from typing import List, Dict
import json
import time
from utils import req, dict_must_contain, list_contains_dict_that_must_contain
from statics import (
    ADMIN_USER_EMAIL,
    ADMIN_USER_NAME,
)


def test_checklist_item_endpoints():
    # count current list amount
    from checkcheckserver.api.routes.routes_checklist_item import create_checklist_item

    checklists: List[Dict] = req("checklist")["items"]
    first_checklist = checklists[0]
    first_checklist_id = first_checklist["id"]
    # hint: import only for quick code access to endpoint
    from checkcheckserver.api.routes.routes_checklist_item import create_checklist_item

    res = req(
        f"/checklist/{first_checklist_id}/item", method="post", b={"text": "Milk"}
    )
    print("res", res)
    dict_must_contain(
        res,
        required_keys_and_val={"text": "Milk"},
        exception_dict_identifier="list checklist positions",
    )

    for i in range(1, 10):

        res = req(
            f"/checklist/{first_checklist_id}/item",
            method="post",
            b={"text": f"Item {i}"},
        )
    from checkcheckserver.api.routes.routes_checklist_item import list_checklist_items

    checklistitems = req(
        f"/checklist/{first_checklist_id}/item",
        method="get",
    )["items"]

    from checkcheckserver.api.routes.routes_checklist_item import update_checklist_item

    new_text = checklistitems[2]["text"] + " updated"

    res = req(
        f'/checklist/{first_checklist_id}/item/{checklistitems[2]["id"]}',
        method="patch",
        b={"text": new_text},
    )
    dict_must_contain(res, required_keys_and_val={"text": new_text})

    from checkcheckserver.api.routes.routes_checklist_item import delete_checklist_item

    before_count = len(checklistitems)
    res = req(
        f'/checklist/{first_checklist_id}/item/{checklistitems[2]["id"]}',
        method="delete",
    )
    checklistitems = req(
        f"/checklist/{first_checklist_id}/item",
        method="get",
    )["items"]
    assert before_count - 1 == len(checklistitems)
    print("checklistitems", checklistitems)
    res = req(
        f"/checklist/{first_checklist_id}/item", method="post", b={"text": "item 2 new"}
    )
    checklistitems = req(
        f"/checklist/{first_checklist_id}/item",
        method="get",
    )["items"]
    print("checklistitems", checklistitems)
    new_first_item = checklistitems[0]
    print("new_first_item", new_first_item)
    dict_must_contain(new_first_item, required_keys_and_val={"text": "item 2 new"})

    from checkcheckserver.api.routes.routes_checklist_item_index import (
        get_checklist_item_position,
    )

    new_item_index = req(
        f"/checklist/{first_checklist_id}/item/{new_first_item['id']}/index",
        method="get",
    )
    print("new_item_pos", new_item_index)
    from checkcheckserver.api.routes.routes_checklist_item_index import (
        set_checklist_item_position,
    )

    new_item_index = req(
        f"/checklist/{first_checklist_id}/item/{new_first_item['id']}/index",
        method="patch",
        b={"index": "0.1"},
    )
    checklistitems = req(
        f"/checklist/{first_checklist_id}/item",
        method="get",
    )["items"]
    assert checklistitems[-1]["text"] == new_first_item["text"]
