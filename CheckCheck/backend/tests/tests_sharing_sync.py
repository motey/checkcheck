"""Live-sync (SSE) fan-out tests for the share *delete* flows.

Regression coverage for a class of bugs where the recipient set of a sync
notification was resolved from live DB state *after* the rows that identify the
recipients had already been deleted:

  * owner deletes a shared checklist  -> nobody was notified at all;
  * collaborator leaves               -> the wrong people were told "deleted";
  * owner revokes a share             -> the removed user was never told.

These connect a real Server-Sent-Events client to ``/api/sync`` and assert which
user actually receives which ``upd_prop`` for the affected checklist. The drain
loop (SQLite) polls once a second and the Postgres path is near-instant, so the
waits below are generously sized.
"""

import json
import threading
import time
from typing import Callable, Dict, List, Optional

import requests

from utils import (
    req,
    get_server_base_url,
    authorize_for_access_token,
    create_test_user,
    get_access_token,
)


# ── SSE client ────────────────────────────────────────────────────────────────


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
        # Wait until the response headers are back (the server has begun the
        # stream and registered this client), then give the generator a beat to
        # finish appending itself to the in-process client list.
        self._ready.wait(timeout=10)
        time.sleep(1.0)
        return self

    def __exit__(self, *_exc) -> None:
        # Just signal stop. Do NOT close the response from this (main) thread:
        # the daemon thread is blocked in iter_lines holding the read, so close()
        # here would block until the read timeout. The short read timeout below
        # lets the daemon thread unwind on its own; it's a daemon so it never
        # holds up the process.
        self._stop.set()

    def _run(self) -> None:
        try:
            self._resp = requests.get(
                self._url,
                headers={"Authorization": f"Bearer {self._token}"},
                stream=True,
                # Short read timeout: between events the read unblocks quickly so
                # the _stop flag is honoured and the thread doesn't linger.
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
            # A read timeout / closed connection just ends collection.
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

    def received(
        self, *, cl_id: str, upd_prop: str, timeout: float = 10.0
    ) -> bool:
        return (
            self.wait_for(
                lambda e: e.get("cl_id") == cl_id and e.get("upd_prop") == upd_prop,
                timeout=timeout,
            )
            is not None
        )


# ── helpers (owner is the global default login set by conftest) ───────────────


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _user_id(token: str) -> str:
    return req("api/user/me", access_token=token)["id"]


def _create_checklist(name: str) -> str:
    return req("api/checklist", "post", b={"name": name, "color_id": "yellow"})["id"]


def _share(checklist_id: str, user_id: str, permission: str) -> Dict:
    return req(
        f"api/checklist/{checklist_id}/shares/{user_id}",
        "put",
        b={"permission": permission},
    )


# ── tests ─────────────────────────────────────────────────────────────────────


def test_owner_delete_of_shared_checklist_notifies_collaborator():
    """Bug #1: the owner deletes a shared checklist. The collaborator must get a
    live ``checklist_deleted`` so the card disappears from their grid. Before the
    fix, target resolution ran after the checklist+collaborators were deleted, so
    the target set was empty and nobody was notified."""
    collab_token = _make_user_token("sse-del-collab")
    collab_id = _user_id(collab_token)
    checklist_id = _create_checklist("SSE-owner-delete")
    _share(checklist_id, collab_id, "edit")

    with _SSECollector(collab_token) as collab_sse:
        req(f"api/checklist/{checklist_id}", "delete", expected_http_code=204)
        assert collab_sse.received(
            cl_id=checklist_id, upd_prop="checklist_deleted"
        ), "collaborator was not notified that the shared checklist was deleted"


def test_revoke_share_notifies_removed_user():
    """Bug #3: when the owner revokes a share, the removed user must get a live
    ``checklist_deleted``. Before the fix their collaborator row was deleted
    before target resolution, so they were excluded from their own removal."""
    collab_token = _make_user_token("sse-revoke-collab")
    collab_id = _user_id(collab_token)
    checklist_id = _create_checklist("SSE-revoke")
    _share(checklist_id, collab_id, "edit")

    with _SSECollector(collab_token) as collab_sse:
        req(
            f"api/checklist/{checklist_id}/shares/{collab_id}",
            "delete",
            expected_http_code=204,
        )
        assert collab_sse.received(
            cl_id=checklist_id, upd_prop="checklist_deleted"
        ), "removed user was not notified that their access was revoked"


def test_collaborator_leave_targets_the_right_users():
    """Bug #2: when a collaborator leaves, the leaver should get
    ``checklist_deleted`` (drop the card) while the owner should get
    ``share_removed`` — NOT ``checklist_deleted`` for a card that still exists."""
    owner_token = get_access_token()  # global default login == card owner
    collab_token = _make_user_token("sse-leave-collab")
    collab_id = _user_id(collab_token)
    checklist_id = _create_checklist("SSE-leave")
    _share(checklist_id, collab_id, "edit")

    with _SSECollector(owner_token) as owner_sse, _SSECollector(
        collab_token
    ) as collab_sse:
        # collaborator leaves via the self-delete ("leave list") path
        req(
            f"api/checklist/{checklist_id}",
            "delete",
            access_token=collab_token,
            expected_http_code=204,
        )

        # leaver drops the card
        assert collab_sse.received(
            cl_id=checklist_id, upd_prop="checklist_deleted"
        ), "the leaver was not told to drop the card"

        # owner is told the share set changed...
        assert owner_sse.received(
            cl_id=checklist_id, upd_prop="share_removed"
        ), "owner was not notified that a collaborator left"

        # ...but must NOT be told the (still-existing) checklist was deleted
        assert (
            owner_sse.wait_for(
                lambda e: e.get("cl_id") == checklist_id
                and e.get("upd_prop") == "checklist_deleted",
                timeout=2.0,
            )
            is None
        ), "owner wrongly received 'checklist_deleted' for a live checklist"
