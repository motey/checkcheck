"""WI-5 — sync-protocol convergence suite.

Where ``tests_changes.py`` unit-tests the delta feed in isolation, this module
exercises the *protocol as a whole* — the contract documented in
``docs/SYNC_PROTOCOL.md`` — across the multi-client scenarios the local-first
frontend (WI-6…11) depends on:

* two clients editing the same shared card converge to a Last-Writer-Wins
  end-state (the harness is sequential, so "concurrent" = interleaved sequential
  writes from two tokens — there is no true parallelism to exploit);
* an offline burst replayed against the idempotent write endpoints, then pulled
  by a second device, converges without duplication;
* a share granted mid-flight delivers the whole tree on the recipient's next
  pull;
* the SSE ``changes_available`` poke rides alongside the frozen legacy per-entity
  events and carries the current ``server_seq``.

All of this runs against the real server subprocess over HTTP (see conftest).
"""

import json
import threading
import time
from typing import Callable, Dict, List, Optional

import requests

from utils import (
    req,
    get_server_base_url,
    get_access_token,
    authorize_for_access_token,
    create_test_user,
    find_first_dict_in_list,
)


# ── helpers (admin is the global default login set by conftest) ───────────────


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _user_id(token: str) -> str:
    return req("api/user/me", access_token=token)["id"]


def _changes(
    since: int = 0,
    known: Optional[List[str]] = None,
    token: Optional[str] = None,
) -> Dict:
    q: Dict = {"since": since}
    if known is not None:
        q["known"] = known
    return req("api/changes", q=q, access_token=token)


def _cursor(token: Optional[str] = None) -> int:
    return _changes(since=0, token=token)["next_cursor"]


def _cl_ids(delta: Dict) -> List[str]:
    return [c["id"] for c in delta["checklists"]]


def _item_ids(delta: Dict) -> List[str]:
    return [i["id"] for i in delta["items"]]


def _share(checklist_id: str, user_id: str, permission: str) -> Dict:
    return req(
        f"api/checklist/{checklist_id}/shares/{user_id}",
        "put",
        b={"permission": permission},
    )


# ── SSE client (compact copy of the bearer half used in the sibling modules) ──


class _SSECollector:
    """Connects to ``/api/sync`` with a bearer token and collects the parsed
    notification dicts pushed to that user, on a background thread."""

    def __init__(self, token: str):
        self._token = token
        self._url = f"{get_server_base_url()}/api/sync"
        self.events: List[Dict] = []
        self._resp: Optional[requests.Response] = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "_SSECollector":
        self._thread.start()
        self._ready.wait(timeout=10)
        time.sleep(1.0)
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            self._resp = requests.get(
                self._url,
                headers={"Authorization": f"Bearer {self._token}"},
                stream=True,
                timeout=(5, 3),
            )
            self._ready.set()
            for raw in self._resp.iter_lines(decode_unicode=True):
                if self._stop.is_set():
                    break
                if raw and raw.startswith("data:"):
                    try:
                        self.events.append(json.loads(raw[len("data:") :].strip()))
                    except json.JSONDecodeError:
                        pass
        except Exception:
            self._ready.set()

    def wait_for(
        self, predicate: Callable[[Dict], bool], timeout: float = 10.0
    ) -> Optional[Dict]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for ev in list(self.events):
                if predicate(ev):
                    return ev
            time.sleep(0.2)
        return None


# ── scenario 1: two clients, same shared card, LWW convergence ────────────────


def test_two_clients_converge_last_writer_wins_on_shared_card():
    """Admin and a collaborator both edit the same item's text. The harness is
    sequential, so the writes interleave in a defined order; the convergent
    end-state both devices pull is the LAST write (server-arrival LWW)."""
    collab = _make_user_token("conv-lww")
    collab_id = _user_id(collab)

    cl_id = req("api/checklist", "post", b={"name": "shared lww"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "v0"})["id"]
    _share(cl_id, collab_id, "edit")

    # Both devices have the card; take a common cursor to pull from.
    cursor = _cursor()  # admin's cursor (== global high-water)

    # Interleaved edits to the SAME field from the two tokens.
    req(f"api/checklist/{cl_id}/item/{item_id}", "patch",
        b={"text": "admin-1"})
    req(f"api/checklist/{cl_id}/item/{item_id}", "patch",
        b={"text": "collab-1"}, access_token=collab)
    req(f"api/checklist/{cl_id}/item/{item_id}", "patch",
        b={"text": "admin-2"})
    # Last arrival wins:
    req(f"api/checklist/{cl_id}/item/{item_id}", "patch",
        b={"text": "collab-FINAL"}, access_token=collab)

    # Both devices pull and see the identical converged value.
    for tok in (None, collab):
        delta = _changes(since=cursor, token=tok)
        item = find_first_dict_in_list(delta["items"], {"id": item_id})
        assert item is not None, "both clients must receive the changed item"
        assert item["text"] == "collab-FINAL", "LWW = last write to arrive"

    req(f"api/checklist/{cl_id}", "delete")


def test_two_clients_editing_different_fields_both_survive():
    """Different fields of the same row edited by two clients: both survive
    (each write re-stamps the row, LWW is per-field)."""
    collab = _make_user_token("conv-fields")
    collab_id = _user_id(collab)

    cl_id = req("api/checklist", "post", b={"name": "before"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "t0"})["id"]
    _share(cl_id, collab_id, "edit")

    cursor = _cursor()

    # Collaborator changes the item text; admin renames the card.
    req(f"api/checklist/{cl_id}/item/{item_id}", "patch",
        b={"text": "edited-by-collab"}, access_token=collab)
    req(f"api/checklist/{cl_id}", "patch", b={"name": "renamed-by-admin"})

    delta = _changes(since=cursor, token=collab)
    card = find_first_dict_in_list(delta["checklists"], {"id": cl_id})
    item = find_first_dict_in_list(delta["items"], {"id": item_id})
    assert card["name"] == "renamed-by-admin", "admin's card edit survives"
    assert item["text"] == "edited-by-collab", "collab's item edit survives"

    req(f"api/checklist/{cl_id}", "delete")


# ── scenario 2: offline outbox replay → delta pull ────────────────────────────


def test_offline_outbox_replay_then_pull_converges_without_duplication():
    """Simulate an outbox draining after reconnect: the same client-id'd create
    is replayed (network double-send), then several edits land. A second device
    pulls once and converges — the replayed create yields no duplicate row."""
    import uuid as _uuid

    device_b_cursor = _cursor()  # device B's cursor before A goes "offline"

    cl_id = req("api/checklist", "post", b={"name": "outbox card"})["id"]

    # Client-generated item id — the outbox's replay-safety hinges on this.
    item_id = str(_uuid.uuid4())
    first = req(f"api/checklist/{cl_id}/item", "post",
                b={"id": item_id, "text": "queued"})
    assert first["id"] == item_id

    # Reconnect double-send: the SAME create replays. Must return the existing
    # row (idempotent), not duplicate or error.
    replay = req(f"api/checklist/{cl_id}/item", "post",
                 b={"id": item_id, "text": "queued"})
    assert replay["id"] == item_id, "create replay must return the same row"

    # Subsequent queued edits drain.
    req(f"api/checklist/{cl_id}/item/{item_id}", "patch", b={"text": "flushed"})

    # Device B pulls once and converges: exactly one item, latest text.
    delta = _changes(since=device_b_cursor)
    matching = [i for i in delta["items"] if i["id"] == item_id]
    assert len(matching) == 1, "replayed create must not duplicate the item"
    assert matching[0]["text"] == "flushed"
    assert cl_id in _cl_ids(delta)

    # And B has fully converged — a re-pull at the new cursor is empty for our rows.
    converged = _changes(since=delta["next_cursor"])
    assert item_id not in _item_ids(converged)
    assert cl_id not in _cl_ids(converged)

    req(f"api/checklist/{cl_id}", "delete")


# ── scenario 3: share added mid-flight ────────────────────────────────────────


def test_share_added_midflight_delivers_whole_tree_on_next_pull():
    """Device B (the recipient) holds a cursor from before a card existed. The
    card is created, edited, and only THEN shared to B. B's next pull from its
    old cursor delivers the whole tree (card + all items), since the rows
    predate the collaborator grant."""
    recipient = _make_user_token("conv-midflight")
    recipient_id = _user_id(recipient)

    base = _cursor(token=recipient)  # recipient's cursor, before anything exists

    # Admin builds a card with two items and edits one — all before sharing.
    cl_id = req("api/checklist", "post", b={"name": "midflight"})["id"]
    a = req(f"api/checklist/{cl_id}/item", "post", b={"text": "a"})["id"]
    b = req(f"api/checklist/{cl_id}/item", "post", b={"text": "b"})["id"]
    req(f"api/checklist/{cl_id}/item/{a}", "patch", b={"text": "a-edited"})

    # Recipient sees nothing yet.
    assert cl_id not in _cl_ids(_changes(since=base, token=recipient))

    # Share mid-flight.
    _share(cl_id, recipient_id, "edit")

    # Next pull from the SAME old cursor delivers the full tree despite the
    # children predating the grant.
    delta = _changes(since=base, token=recipient)
    assert cl_id in _cl_ids(delta), "gained card delivered"
    got_items = set(_item_ids(delta))
    assert {a, b} <= got_items, "all children of the gained card delivered"
    item_a = find_first_dict_in_list(delta["items"], {"id": a})
    assert item_a["text"] == "a-edited", "latest child state delivered"
    card = find_first_dict_in_list(delta["checklists"], {"id": cl_id})
    assert card["my_permission"] == "edit"

    req(f"api/checklist/{cl_id}", "delete")


# ── scenario 4: the changes_available poke ────────────────────────────────────


def test_changes_available_poke_rides_alongside_legacy_event():
    """A board edit produces BOTH the frozen legacy per-entity event (item_text)
    AND the WI-5 changes_available poke, which carries the current server_seq.
    The poke's seq lets a flagged client decide whether to pull."""
    cl_id = req("api/checklist", "post", b={"name": "poke host"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "x"})["id"]

    before = _cursor()

    with _SSECollector(get_access_token()) as sse:
        req(f"api/checklist/{cl_id}/item/{item_id}", "patch", b={"text": "poked"})

        # Legacy per-entity event still fires (unchanged contract).
        legacy = sse.wait_for(
            lambda e: e.get("cl_id") == cl_id
            and e.get("upd_prop") == "item_text"
        )
        assert legacy is not None, "legacy per-entity event must still fire"

        # The additional changes_available poke fires and carries the current
        # server_seq. (The SQLite drain also delivers this card's earlier
        # create-time pokes to a freshly-connected collector, so match on the one
        # whose seq advanced past the pre-edit cursor — the edit we just made.)
        poke = sse.wait_for(
            lambda e: e.get("cl_id") == cl_id
            and e.get("upd_prop") == "changes_available"
            and (e.get("server_seq") or 0) > before
        )
        assert poke is not None, (
            "a changes_available poke carrying an advanced server_seq must fire "
            "alongside the edit, so a flagged client knows to pull"
        )

    req(f"api/checklist/{cl_id}", "delete")
