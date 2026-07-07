"""Postgres-only concurrency test for the ``server_seq`` commit-order guarantee.

The whole 2.0 delta feed rests on one invariant (see
``model/_base_model.py::TimestampedModel.server_seq`` and
``routes_changes.py``): **committed ``server_seq`` values are monotonic in commit
order**, so a client that has consumed the cursor up to ``N`` can never miss a row
that commits later with a smaller seq. The allocator holds the single ``sync_seq``
counter-row lock until its transaction commits, so seq ``K`` always commits before
seq ``K+1`` is even *allocated*; and ``/api/changes`` reads the high-water mark
(``next_cursor``) *before* it queries rows. Together that means: whenever a pull
reports ``next_cursor = M``, every row with seq ``<= M`` is already visible.

``tests_convergence.py`` is interleaved-*sequential* by design (its docstring says
so — the harness fires one token at a time), so nothing there exercises true write
parallelism. SQLite serialises writes anyway, so the guarantee is trivially met
there. It is only *at risk* under real parallelism, which needs Postgres — hence
this module skips unless the suite runs with ``--db=postgres``
(``./run_backend_tests_with_postgres.sh``).

The test fires bursts of N simultaneous item creates while a reader thread walks
the cursor forward (never resetting ``since`` below what it has reached). If the
allocator ever let a higher seq commit before a lower one — so the reader advanced
its cursor past a seq whose row had not yet committed — that row would be stranded
forever, and its id would be missing from what the walk delivered. The final
assertion catches exactly that.

Note the deadlock-retry expectation documented on ``_allocate_server_seq``: under
this kind of load Postgres may abort a transaction with a deadlock error (a 5xx
the outbox would replay). The idempotent write endpoints make that self-healing;
here we retry a create once on a transient 5xx so the burst still lands every row.
"""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Set

import pytest
import requests

from utils import req, get_access_token, get_server_base_url


def _changes(since: int, session: requests.Session, timeout: float = 15.0) -> Dict:
    """Direct GET /api/changes with an explicit timeout, so a request that hangs
    under load surfaces as an error rather than stalling the reader forever."""
    r = session.get(
        f"{get_server_base_url()}/api/changes",
        params={"since": since},
        headers={"Authorization": f"Bearer {get_access_token()}"},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def _cursor() -> int:
    with requests.Session() as s:
        return _changes(0, s)["next_cursor"]


def _create_item(cl_id: str, item_id: str) -> None:
    """POST one item, tolerating a transient 5xx (deadlock abort) with one retry —
    the write is idempotent (client-supplied id), so a replay lands the same row."""
    for attempt in range(2):
        try:
            req(
                f"api/checklist/{cl_id}/item",
                "post",
                b={"id": item_id, "text": "x"},
            )
            return
        except requests.HTTPError as err:
            status = err.response.status_code if err.response is not None else None
            if status is not None and status >= 500 and attempt == 0:
                continue  # deadlock abort → replay (idempotent)
            raise


def test_parallel_writes_are_never_skipped_by_the_cursor(request):
    if request.config.getoption("--db") != "postgres":
        pytest.skip(
            "the server_seq commit-order guarantee is only at risk under true "
            "write parallelism; SQLite serialises writes, so this needs the Docker "
            "Postgres harness (./run_backend_tests_with_postgres.sh)."
        )

    cl_id = req("api/checklist", "post", b={"name": "seq-concurrency"})["id"]
    start_cursor = _cursor()

    created_ids: Set[str] = set()
    created_lock = threading.Lock()
    delivered: Set[str] = set()
    # Set to the post-write high-water once the writers finish; the reader drains
    # up to it, then stops. We can't wait for the cursor to *stop* growing: every
    # authenticated request re-stamps its UserAuth.last_used_at (a TimestampedModel
    # UPDATE → a fresh server_seq), so the reader's own polling keeps the counter
    # climbing forever. That churn is harmless to sync correctness (UserAuth is not
    # in the delta feed), but it means "drain to a fixed target" is the only sound
    # stop condition. Every created item's seq is below that target, so reaching it
    # proves the forward walk delivered them all.
    drain_target: List[int] = []
    reader_error: List[BaseException] = []
    advances = 0  # how many times the reader's cursor actually moved forward

    def reader() -> None:
        nonlocal advances
        cursor = start_cursor
        with requests.Session() as session:
            try:
                while True:
                    delta = _changes(cursor, session)
                    for it in delta["items"]:
                        delivered.add(it["id"])
                    nc = delta["next_cursor"]
                    if nc > cursor:
                        cursor = nc
                        advances += 1
                    if drain_target and cursor >= drain_target[0]:
                        break
                    time.sleep(0.001)  # keep it tight so it observes partial state
            except BaseException as e:  # surface it in the main thread
                reader_error.append(e)

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    ROUNDS = 3
    WRITERS = 24  # a full barrier of simultaneous committers per round

    for _ in range(ROUNDS):
        barrier = threading.Barrier(WRITERS)

        def write(_i: int) -> None:
            item_id = str(uuid.uuid4())
            barrier.wait()  # release all writers at once → maximal commit overlap
            _create_item(cl_id, item_id)
            with created_lock:
                created_ids.add(item_id)

        with ThreadPoolExecutor(max_workers=WRITERS) as pool:
            list(pool.map(write, range(WRITERS)))

        # Let the reader catch its cursor up between bursts, so a skip *within* the
        # next burst strands a row the walk has already moved past.
        time.sleep(0.05)

    # Every create returned 2xx (committed), so the current high-water sits at or
    # above every item's seq. Point the reader at it and let the forward walk drain.
    drain_target.append(_cursor())
    reader_thread.join(timeout=60)
    if reader_error:
        raise reader_error[0]
    assert not reader_thread.is_alive(), (
        f"cursor reader did not finish draining (advances={advances}, "
        f"delivered={len(delivered)}, created={len(created_ids)})"
    )

    assert len(created_ids) == ROUNDS * WRITERS, "every queued create must land"

    missing = created_ids - delivered
    assert not missing, (
        f"{len(missing)} row(s) committed with a server_seq the cursor had already "
        f"advanced past — the commit-order guarantee was violated (rows lost to the "
        f"delta feed): {sorted(missing)}"
    )
    # Sanity: the walk actually advanced incrementally rather than collapsing into
    # one bootstrap pull at the end (which would prove nothing about interleaving).
    assert advances >= 2, (
        f"cursor only advanced {advances}x — the reader did not walk concurrently "
        "with the writers, so the test proved nothing"
    )

    req(f"api/checklist/{cl_id}", "delete")
