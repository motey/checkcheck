from _single_test_file_runner import run_all_tests_if_test_file_called

if __name__ == "__main__":
    run_all_tests_if_test_file_called()
from typing import List, Dict
import json
import time
import requests
import random
from utils import req, dict_must_contain, list_contains_dict_that_must_contain, dictyfy
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

    colors: List[Dict] = [c["id"] for c in req(f"api/color", method="get")]
    colors.append(None)

    def get_random_color_id():
        return random.choice(colors)

    from checkcheckserver.api.routes.routes_checklist_label import (
        create_label,
        list_labels,
        LabelCreate,
    )

    for word in get_random_words(fixed_amount=8).split(" "):
        label_create = LabelCreate(color_id=get_random_color_id(), display_name=word)
        print(f"label_create: {label_create}")
        cl = req(
            f"api/label",
            method="post",
            b=dictyfy(label_create),
        )
    label_ids = [c["id"] for c in req(f"api/label", method="get")]

    def get_random_label_ids():
        no_of_labels = random.randint(0, int(len(label_ids) / 2))
        return random.sample(label_ids, no_of_labels)

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
                "color_id": get_random_color_id(),
            },
        )
        from checkcheckserver.api.routes.routes_checklist_label import (
            add_label_to_checklist,
        )

        for label_id in get_random_label_ids():
            # add label to checklist
            print("label", label_id)
            req(f"/api/checklist/{cl['id']}/label/{label_id}", method="put")
        checklists.append(cl)
        for j in range(0, random.randint(0, 70)):
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
    # dict_must_contain(new_first_item, required_keys_and_val={"text": "item 2 new"})
