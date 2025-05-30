from typing import List, Dict
import json
import time
from utils import req, dict_must_contain, list_contains_dict_that_must_contain
from statics import (
    ADMIN_USER_EMAIL,
    ADMIN_USER_NAME,
)


def test_checklist_endpoints():
    # hint: import only for quick code access to endpoint
    from checkcheckserver.api.routes.routes_checklist import create_checklist

    res = req(
        "checklist",
        method="post",
        b={
            "name": "Dont Forget",
            "text": "Items you should not forget",
            "color_id": "yellow",
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
            "color_id": "yellow",
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
        required_keys_and_val={"id": "yellow"},
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
    assert res["items"][-1] == first_checklist

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
            "color_id": "yellow",
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
