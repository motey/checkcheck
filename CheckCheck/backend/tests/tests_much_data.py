from typing import List, Dict
import os
import random
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from utils import (
    req,
    dict_must_contain,
    get_access_token,
    server_config,
)

# How much data to generate. Override via env to stress-test harder, e.g.
#   CHECKCHECK_MUCH_DATA_LISTS=500 CHECKCHECK_MUCH_DATA_ITEMS=50 python -m pytest ...
NO_CHECKLISTS = int(os.environ.get("CHECKCHECK_MUCH_DATA_LISTS", "40"))
MAX_NO_CHECKLIST_ITEMS = int(os.environ.get("CHECKCHECK_MUCH_DATA_ITEMS", "30"))

# A small local word pool. The old test downloaded MIT's 10k-word list over the
# internet on every run, which dominated the runtime (and is flaky/offline-
# hostile). The generated content is throwaway, so any words will do.
_WORDS: List[str] = (
    "apple banana cherry delta echo forest garden harbor island jungle kettle "
    "lemon meadow nectar ocean pepper quartz river summit timber umbra valley "
    "willow xenon yonder zephyr amber bronze cobalt copper crimson emerald "
    "golden indigo ivory jade lavender magenta maroon olive orchid scarlet "
    "silver teal violet anchor beacon bridge canyon cottage desert dynamo "
    "engine falcon glacier hammer harvest ladder lantern lighthouse marble "
    "mountain orchard pebble pillar prairie quarry ranch ridge sapling thicket "
    "tunnel village wagon meadowlark cardinal sparrow falconry otter badger "
    "beaver bobcat caribou cheetah dolphin elephant ferret gazelle hedgehog "
    "iguana jaguar koala lynx mongoose narwhal ocelot panther raccoon salmon"
).split()


def get_random_words(
    rng: random.Random, max_amount: int = 100, fixed_amount: int = None
) -> str:
    if fixed_amount is None:
        fixed_amount = rng.randint(1, max_amount)
    return " ".join(rng.choice(_WORDS) for _ in range(fixed_amount))


def _is_postgres() -> bool:
    return "postgres" in str(server_config.SQL_DATABASE_URL).lower()


# Each worker thread gets its own keep-alive HTTP session so connections are
# reused (the bare ``requests`` module opens a fresh TCP connection per call)
# and ``requests.Session`` is not shared across threads (it isn't thread-safe).
_thread_local = threading.local()


def _session() -> requests.Session:
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = requests.Session()
        token = get_access_token()
        if token:
            sess.headers["Authorization"] = f"Bearer {token}"
        _thread_local.session = sess
    return sess


def test_much_data():
    seed = None  # set to a fixed value for reproducible runs
    rng = random.Random(seed)

    colors: List[Dict] = [c["id"] for c in req("api/color", method="get")]
    colors.append(None)

    # ── Labels ────────────────────────────────────────────────────────────────
    for word in get_random_words(rng, fixed_amount=8).split(" "):
        req(
            "api/label",
            method="post",
            b={"color_id": rng.choice(colors), "display_name": word},
        )
    label_ids = [c["id"] for c in req("api/label", method="get")]

    # ── Pre-compute every list + its items up front (cheap, no I/O) so the
    #    HTTP creation phase is pure request dispatch we can parallelise. ───────
    def make_spec(i: int) -> Dict:
        no_labels = rng.randint(0, len(label_ids) // 2)
        return {
            "create": {
                "name": (
                    f"{i}" + get_random_words(rng, max_amount=10)
                    if rng.choice([True, False])
                    else None
                ),
                "text": (
                    f"{i}" + get_random_words(rng)
                    if rng.choice([True, False])
                    else None
                ),
                "color_id": rng.choice(colors),
                "checked_items_collapsed": rng.choice([True, False]),
                "checked_items_seperated": rng.choice([True, False]),
            },
            "labels": rng.sample(label_ids, no_labels),
            "items": [
                {
                    "text": f"{j}" + get_random_words(rng, max_amount=30),
                    "state": {"checked": rng.choice([True, False])},
                }
                for j in range(rng.randint(0, MAX_NO_CHECKLIST_ITEMS))
            ],
        }

    specs = [make_spec(i) for i in range(NO_CHECKLISTS)]

    def create_list(spec: Dict) -> Dict:
        sess = _session()
        cl = req("api/checklist", method="post", b=spec["create"], session=sess)
        for label_id in spec["labels"]:
            req(
                f"/api/checklist/{cl['id']}/label/{label_id}",
                method="put",
                session=sess,
            )
        # Items within one list must be created sequentially: each item's
        # position index is derived from the current last item server-side.
        for item in spec["items"]:
            req(f"api/checklist/{cl['id']}/item", method="post", b=item, session=sess)
        return cl

    # Fan out across lists. Postgres handles concurrent writes well, so default
    # to 8 workers there. SQLite (the default local-dev DB) serialises writes and
    # raises "database is locked" under concurrency, so default to serial there.
    # Override with CHECKCHECK_MUCH_DATA_WORKERS to tune either way.
    default_workers = "8" if _is_postgres() else "1"
    workers = int(os.environ.get("CHECKCHECK_MUCH_DATA_WORKERS", default_workers))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(create_list, specs))
    else:
        for spec in specs:
            create_list(spec)

    # ── Single-list item CRUD smoke checks (unchanged behaviour) ───────────────
    checklists: List[Dict] = req("api/checklist")["items"]
    first_checklist_id = checklists[0]["id"]

    res = req(
        f"api/checklist/{first_checklist_id}/item", method="post", b={"text": "Milk"}
    )
    dict_must_contain(
        res,
        required_keys_and_val={"text": "Milk"},
        exception_dict_identifier="list checklist positions",
    )

    for i in range(1, 10):
        req(
            f"api/checklist/{first_checklist_id}/item",
            method="post",
            b={"text": f"Item {i}"},
        )

    checklistitems = req(f"api/checklist/{first_checklist_id}/item")["items"]

    new_text = checklistitems[2]["text"] + " updated"
    res = req(
        f'api/checklist/{first_checklist_id}/item/{checklistitems[2]["id"]}',
        method="patch",
        b={"text": new_text},
    )
    dict_must_contain(res, required_keys_and_val={"text": new_text})

    before_count = len(checklistitems)
    req(
        f'api/checklist/{first_checklist_id}/item/{checklistitems[2]["id"]}',
        method="delete",
    )
    checklistitems = req(f"api/checklist/{first_checklist_id}/item")["items"]
    assert before_count - 1 == len(checklistitems)

    req(
        f"api/checklist/{first_checklist_id}/item",
        method="post",
        b={"text": "item 2 new"},
    )
    checklistitems = req(f"api/checklist/{first_checklist_id}/item")["items"]
    assert checklistitems  # list still readable after the churn
