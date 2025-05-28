from typing import List, Dict
import json
import time
from utils import req, dict_must_contain, list_contains_dict_that_must_contain
from statics import (
    ADMIN_USER_EMAIL,
    ADMIN_USER_NAME,
)


def test_checklist_posistion_endpoints():
    # count current list amount
    current_list_count = req(
        "checklist",
        method="get",
    )["total_count"]
    # create 3 new lists
    list_count = current_list_count + 3
    test_item_id = None
    for i in range(current_list_count, list_count):
        res = req(
            "checklist",
            method="post",
            b={
                "name": f"CList{i}",
                "text": f"list{i} text",
            },
        )
        test_item_id = res["id"]
        # nested positions objects should not have its parents checklist id

        if "checklist_id" in list(res["position"].keys()):
            raise ValueError(
                f"Nexted positon obj should not have checklist_id attr. res:{res}"
            )

    # hint: import only for quick code access to endpoint
    from checkcheckserver.api.routes.routes_checklist_position import (
        list_checklist_positions,
    )

    res = req("position", method="get")
    print("res", res)
    dict_must_contain(
        res,
        required_keys_and_val={"total_count": list_count},
        exception_dict_identifier="list checklist positions",
    )
    assert len(res["items"]) == list_count
    dict_must_contain(
        res["items"][0],
        required_keys=["checked_items_collapsed", "index", "pinned"],
        exception_dict_identifier="checklist positions count",
    )

    from checkcheckserver.api.routes.routes_checklist_position import (
        get_checklist_position,
    )

    res = req(f"/checklist/{test_item_id}/position", method="get")
    print("res", res)
    dict_must_contain(res, required_keys_and_val={"checklist_id": test_item_id})
    pos_obj_to_update = res

    from checkcheckserver.api.routes.routes_checklist_position import (
        update_checklist_position,
    )

    res = req(f"/checklist/{test_item_id}/position", method="patch", b={"index": 0.2})
    dict_must_contain(res, required_keys_and_val={"index": 0.2})
    all_pos_obj = req("position", method="get")
    print("res", res)
    assert all_pos_obj["items"][1]["checklist_id"] == pos_obj_to_update["checklist_id"]

    return

    res = req(
        "checklist",
        method="post",
        b={
            "name": "Dont Forget",
            "text": "Items you should not forget",
            "color_id": "YellowLight",
        },
    )
    first_checklist = res
    first_checklist_id = res["id"]
    print(res)
    dict_must_contain(
        res,
        required_keys=["id", "color", "position"],
        exception_dict_identifier="create first checklist",
    )
    dict_must_contain(
        res["position"],
        required_keys=["archived", "pinned", "checked_items_collapsed", "index"],
        exception_dict_identifier="create first checklist",
    )
    # create second list
    res = req(
        "checklist",
        method="post",
        b={
            "name": "Shopping",
            "text": "Items you have to buy",
            "color_id": "YellowLight",
        },
    )
    print("res", res)
    dict_must_contain(
        res["position"],
        required_keys_and_val={"index": 0.4},
        exception_dict_identifier="create second shopping checklist",
    )
    dict_must_contain(
        res["color"],
        required_keys_and_val={"id": "YellowLight"},
        exception_dict_identifier="create second shopping checklist",
    )
    second_check_list_id = res["id"]
    from checkcheckserver.api.routes.routes_checklist import list_checklists

    res = req(
        "checklist",
        method="get",
    )
    dict_must_contain(
        res,
        required_keys_and_val={"total_count": 2},
        required_keys=["items"],
        exception_dict_identifier="create second shopping checklist",
    )
    assert len(res["items"]) == 2
    assert res["items"][0] == first_checklist

    from checkcheckserver.api.routes.routes_checklist import update_checklist

    res = req(
        f"checklist/{second_check_list_id}",
        method="patch",
        b={"text": "Things you have to buy"},
    )
    dict_must_contain(
        res,
        required_keys_and_val={
            "name": "Shopping",
            "text": "Things you have to buy",
            "color_id": "YellowLight",
        },
        required_keys=["position"],
        exception_dict_identifier="Validate Checklist was updated",
    )

    from checkcheckserver.api.routes.routes_checklist import delete_checklist

    res = req(
        f"checklist/{second_check_list_id}",
        method="delete",
    )
    res = req(
        "checklist",
        method="get",
    )
    dict_must_contain(
        res,
        required_keys_and_val={"total_count": 1},
        required_keys=["items"],
        exception_dict_identifier="Validate Checklist was deleted",
    )
