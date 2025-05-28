from _single_test_file_runner import run_all_tests_if_test_file_called

if __name__ == "__main__":
    run_all_tests_if_test_file_called()
from typing import List, Dict
import json
import time
import requests
import random
from utils import req, dict_must_contain, list_contains_dict_that_must_contain
from statics import (
    ADMIN_USER_EMAIL,
    ADMIN_USER_NAME,
)


def test_much_data():
    # count current list amount

    seed = None  # for reproducable runs enable seed
    # seed = "123456"
    if seed:
        random.seed(seed)
    words_resp = requests.get("https://www.mit.edu/~ecprice/wordlist.10000")
    words: List[str] = words_resp.text.split("\n")

    def get_random_words(max_amount: int = 100, fixed_amount: int = None):
        return_words = []
        if fixed_amount is None:
            fixed_amount = random.randint(1, max_amount)
        for i in range(0, fixed_amount):
            return_words.append(random.choice(words))
        return " ".join(return_words)

    from checkcheckserver.api.routes.routes_color_scheme import (
        list_colors,
        ChecklistColorScheme,
    )

    colors = [c["id"] for c in req(f"api/color", method="get")]

    def get_random_color():
        return random.choice(colors)

    checklists = []
    for i in range(0, 100):
        from checkcheckserver.api.routes.routes_checklist import create_checklist

        cl = req(
            f"api/checklist",
            method="post",
            b={
                "name": (
                    f"{i}" + get_random_words(max_amount=10)
                    if random.choice([True, False])
                    else None
                ),
                "text": (
                    f"{i}" + get_random_words()
                    if random.choice([True, False])
                    else None
                ),
                "color_id": get_random_color(),
            },
        )
        checklists.append(cl)
        for j in range(0, random.randint(0, 200)):
            # from checkcheckserver.api.routes.routes_checklist_item import create_checklist_item
            print(i, j)
            req(
                f"api/checklist/{cl['id']}/item",
                method="post",
                b={
                    "text": f"{j}" + get_random_words(max_amount=30),
                    "state": {"checked": random.choice([True, False])},
                },
            )

    checklists: List[Dict] = req("api/checklist")["items"]
    first_checklist = checklists[0]
    first_checklist_id = first_checklist["id"]
    # hint: import only for quick code access to endpoint
    from checkcheckserver.api.routes.routes_checklist_item import create_checklist_item

    res = req(
        f"api/checklist/{first_checklist_id}/item", method="post", b={"text": "Milk"}
    )
    print("res", res)
    dict_must_contain(
        res,
        required_keys_and_val={"text": "Milk"},
        exception_dict_identifier="list checklist positions",
    )

    for i in range(1, 10):

        res = req(
            f"api/checklist/{first_checklist_id}/item",
            method="post",
            b={"text": f"Item {i}"},
        )
    from checkcheckserver.api.routes.routes_checklist_item import list_checklist_items

    checklistitems = req(
        f"api/checklist/{first_checklist_id}/item",
        method="get",
    )["items"]

    from checkcheckserver.api.routes.routes_checklist_item import update_checklist_item

    new_text = checklistitems[2]["text"] + " updated"

    res = req(
        f'api/checklist/{first_checklist_id}/item/{checklistitems[2]["id"]}',
        method="patch",
        b={"text": new_text},
    )
    dict_must_contain(res, required_keys_and_val={"text": new_text})

    from checkcheckserver.api.routes.routes_checklist_item import delete_checklist_item

    before_count = len(checklistitems)
    res = req(
        f'api/checklist/{first_checklist_id}/item/{checklistitems[2]["id"]}',
        method="delete",
    )
    checklistitems = req(
        f"api/checklist/{first_checklist_id}/item",
        method="get",
    )["items"]
    assert before_count - 1 == len(checklistitems)
    print("checklistitems", checklistitems)
    res = req(
        f"api/checklist/{first_checklist_id}/item",
        method="post",
        b={"text": "item 2 new"},
    )
    checklistitems = req(
        f"api/checklist/{first_checklist_id}/item",
        method="get",
    )["items"]
    print("checklistitems", checklistitems)
    new_first_item = checklistitems[0]
    print("new_last_item", new_first_item)
    dict_must_contain(new_first_item, required_keys_and_val={"text": "item 2 new"})
