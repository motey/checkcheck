from typing import List, Dict
import json
import time
from utils import req, dict_must_contain, list_contains_dict_that_must_contain
from statics import (
    ADMIN_USER_EMAIL,
    ADMIN_USER_NAME,
)
import decimal


def test_checklist_item_pos_endpoints():
    from checkcheckserver.api.routes.routes_checklist import create_checklist

    res = req(
        "api/checklist",
        method="post",
        b={
            "name": "Items to sort",
            "text": "Items we want to sort",
        },
    )
    checklist_id = res["id"]
    #
    from checkcheckserver.api.routes.routes_checklist_item import create_checklist_item

    for i in range(1, 6):
        res = req(
            f"api/checklist/{checklist_id}/item",
            method="post",
            b={"text": f"Item {i}"},
        )
    #
    checklistitems = req(
        f"api/checklist/{checklist_id}/item",
        method="get",
    )["items"]
    expected_order = [
        {
            "text": "Item 5",
            "position": {"index": 2.0, "indentation": 0},
        },
        {
            "text": "Item 4",
            "position": {"index": 1.6, "indentation": 0},
        },
        {
            "text": "Item 3",
            "position": {"index": 1.2, "indentation": 0},
        },
        {
            "text": "Item 2",
            "position": {"index": 0.8, "indentation": 0},
        },
        {
            "text": "Item 1",
            "position": {"index": 0.4, "indentation": 0},
        },
    ]
    for index, item in enumerate(expected_order):
        dict_must_contain(
            checklistitems[index],
            required_keys_and_val=item,
            exception_dict_identifier="list checklist positions",
        )
    item_5 = checklistitems[0]
    item_4 = checklistitems[1]
    item_3 = checklistitems[2]
    item_2 = checklistitems[3]
    item_1 = checklistitems[4]

    from checkcheckserver.api.routes.routes_checklist_item_pos import (
        move_item_to_bottom_of_checklist,
    )

    item_5_new_index = req(
        f"/checklist/{checklist_id}/item/{item_5['id']}/move/bottom",
        method="patch",
    )
    assert decimal.Decimal(str(item_5_new_pos["index"])) == decimal.Decimal(
        str(item_1["position"]["index"])
    ) - decimal.Decimal(
        "0.4"
    ), f'item_5_new_pos["index"] == item_1["position"]["index"] - 0.4: {item_5_new_pos["index"]}=={item_1["position"]["index"]-0.4} -> {item_5_new_pos["index"] == item_1["position"]["index"] - 0.4}'
    print("item_5_new_pos", item_5_new_index)
    #
    from checkcheckserver.api.routes.routes_checklist_item_index import (
        move_item_to_top_of_checklist,
    )

    item_1_new_index = req(
        f"/checklist/{checklist_id}/item/{item_1['id']}/move/top",
        method="patch",
    )
    print("item_1_new_pos", item_1_new_index)

    from checkcheckserver.api.routes.routes_checklist_item_index import (
        move_item_under_other_item,
    )

    item_2_new_index = req(
        f"/checklist/{checklist_id}/item/{item_2['id']}/move/under/{item_1['id']}",
        method="patch",
    )
    #
    from checkcheckserver.api.routes.routes_checklist_item_index import (
        move_item_above_other_item,
    )

    item_2_new_index = req(
        f"/checklist/{checklist_id}/item/{item_3['id']}/move/above/{item_4['id']}",
        method="patch",
    )
    #
    checklistitems_after = req(
        f"/checklist/{checklist_id}/item",
        method="get",
    )["items"]
    expected_order = [
        {
            "text": "Item 1",
            "position": {"index": 2.0, "indentation": 0},
        },
        {
            "text": "Item 2",
            "position": {"index": 1.8, "indentation": 0},
        },
        {
            "text": "Item 3",
            "position": {"index": 1.8, "indentation": 0},
        },
        {
            "text": "Item 4",
            "position": {"index": 1.6, "indentation": 0},
        },
        {
            "text": "Item 5",
            "position": {"index": 0.0, "indentation": 0},
        },
    ]
    print("checklistitems_after", checklistitems_after)
    for index, item in enumerate(expected_order):
        dict_must_contain(
            checklistitems_after[index],
            required_keys_and_val=item,
            exception_dict_identifier="list checklist positions",
        )
