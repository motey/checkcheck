"""Integration tests for Phase 9 — in-app share notifications.

Covers the persistent notification feed (``/user/me/notifications``), the
share-event hooks that populate it, the one-time ``public_link_opened`` hook on
first anonymous open of a public link, and the live SSE nudge
(``upd_prop="notification"``).

The global default login (set by conftest) is the admin user, who acts as the
card *owner* throughout. The suite is robust to the invite-flow pass: when the
server runs with ``SHARING_REQUIRE_INVITE_ACCEPT`` on, sharing produces a
``card_invited`` notification instead of ``card_shared`` (both land in the same
feed and push the same SSE), so the share test branches on that flag.
"""

import json
import threading
import time
from typing import Callable, Dict, List, Optional

import pytest
import requests

from utils import (
    req,
    server_config,
    get_server_base_url,
    authorize_for_access_token,
    create_test_user,
    dict_must_contain,
    find_first_dict_in_list,
    list_contains_dict_that_must_contain,
)

INVITE_REQUIRED = server_config.SHARING_REQUIRE_INVITE_ACCEPT
# Sharing a card produces card_invited when the invite flow is on, else card_shared.
SHARE_NOTI_TYPE = "card_invited" if INVITE_REQUIRED else "card_shared"


# ── helpers (mirror the sibling sharing test modules) ─────────────────────────


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _user_id(token: str) -> str:
    return req("api/user/me", access_token=token)["id"]


def _create_checklist(name: str) -> str:
    return req("api/checklist", "post", b={"name": name, "color_id": "yellow"})["id"]


def _share(checklist_id: str, user_id: str, permission: str = "edit") -> Dict:
    return req(
        f"api/checklist/{checklist_id}/shares/{user_id}",
        "put",
        b={"permission": permission},
    )


def _create_public_link(checklist_id: str, permission: str = "view") -> Dict:
    return req(
        f"api/checklist/{checklist_id}/public-links",
        "post",
        b={"permission": permission},
    )


def _notifications(token: str, unread_only: bool = False) -> List[Dict]:
    q = {"unread_only": "true"} if unread_only else None
    return req("api/user/me/notifications", q=q, access_token=token)


def _unread_count(token: str) -> int:
    return req("api/user/me/notifications/unread-count", access_token=token)[
        "unread_count"
    ]


def _global_owner_token() -> str:
    from utils import get_access_token

    return get_access_token()


# ── SSE client (compact copy of the bearer half used in the sibling modules) ──


class _SSECollector:
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

    def received(self, *, cl_id: str, upd_prop: str, timeout: float = 10.0) -> bool:
        return (
            self.wait_for(
                lambda e: e.get("cl_id") == cl_id and e.get("upd_prop") == upd_prop,
                timeout=timeout,
            )
            is not None
        )


# ── share → notification feed ─────────────────────────────────────────────────


def test_sharing_creates_notification_and_read_flow():
    """Sharing a card notifies the target: it appears in their feed (with the
    actor + card name), the unread filter/count include it, and marking it read
    flips read_at and drops the unread count."""
    target_token = _make_user_token("noti-share-target")
    target_id = _user_id(target_token)
    checklist_id = _create_checklist("NotiShare")

    before_unread = _unread_count(target_token)
    _share(checklist_id, target_id, "edit")

    feed = _notifications(target_token)
    noti = find_first_dict_in_list(
        feed, {"type": SHARE_NOTI_TYPE, "cl_id": checklist_id}
    )
    dict_must_contain(noti, {"read_at": None}, required_keys=["id", "created_at"])
    # payload carries renderable context (actor + card name), never a secret.
    dict_must_contain(noti["payload"], {"checklist_name": "NotiShare"})
    assert noti["payload"]["actor_id"]  # the sharing owner

    # unread filter + count include it
    assert list_contains_dict_that_must_contain(
        _notifications(target_token, unread_only=True),
        {"id": noti["id"]},
        raise_if_not_fullfilled=False,
    )
    assert _unread_count(target_token) == before_unread + 1

    # mark read → read_at set, unread count back down, gone from unread filter
    marked = req(
        f"api/user/me/notifications/{noti['id']}/read",
        "post",
        access_token=target_token,
    )
    assert marked["read_at"] is not None
    assert _unread_count(target_token) == before_unread
    assert not list_contains_dict_that_must_contain(
        _notifications(target_token, unread_only=True),
        {"id": noti["id"]},
        raise_if_not_fullfilled=False,
    )


def test_mark_read_requires_ownership():
    """A user cannot mark another user's notification read — it 404s, and the
    real owner's notification stays unread."""
    target_token = _make_user_token("noti-owned-target")
    target_id = _user_id(target_token)
    outsider_token = _make_user_token("noti-owned-outsider")
    checklist_id = _create_checklist("NotiOwned")
    _share(checklist_id, target_id, "edit")

    noti = find_first_dict_in_list(
        _notifications(target_token), {"cl_id": checklist_id}
    )
    req(
        f"api/user/me/notifications/{noti['id']}/read",
        "post",
        access_token=outsider_token,
        expected_http_code=404,
    )
    # still unread for the rightful owner
    fresh = find_first_dict_in_list(
        _notifications(target_token), {"id": noti["id"]}
    )
    dict_must_contain(fresh, {"read_at": None})


def test_mark_all_read_clears_unread_count():
    """read-all marks every notification of the caller read."""
    target_token = _make_user_token("noti-readall-target")
    target_id = _user_id(target_token)
    for name in ("NotiAll1", "NotiAll2"):
        _share(_create_checklist(name), target_id, "edit")

    assert _unread_count(target_token) >= 2
    req(
        "api/user/me/notifications/read-all",
        "post",
        access_token=target_token,
        expected_http_code=204,
    )
    assert _unread_count(target_token) == 0
    assert _notifications(target_token, unread_only=True) == []


# ── public link first open → owner notification (once) ────────────────────────


def test_first_public_link_open_notifies_owner_once():
    """The first anonymous open of a public link creates exactly one
    public_link_opened notification for the owner; subsequent opens add none."""
    checklist_id = _create_checklist("NotiPublicOpen")
    link = _create_public_link(checklist_id, "view")
    token = link["token"]

    def _owner_open_notis() -> List[Dict]:
        return [
            n
            for n in _notifications(_global_owner_token())
            if n["type"] == "public_link_opened" and n["cl_id"] == checklist_id
        ]

    assert _owner_open_notis() == []

    # open the link anonymously a few times
    for _ in range(3):
        req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=200)

    notis = _owner_open_notis()
    assert len(notis) == 1, f"expected exactly one open notification, got {len(notis)}"
    dict_must_contain(
        notis[0]["payload"], {"checklist_name": "NotiPublicOpen"}
    )


# ── live SSE nudge ────────────────────────────────────────────────────────────


def test_recipient_gets_notification_sse_push():
    """A connected recipient gets a live upd_prop='notification' nudge the moment a
    card is shared with them."""
    target_token = _make_user_token("noti-sse-target")
    target_id = _user_id(target_token)
    checklist_id = _create_checklist("NotiSSE")

    with _SSECollector(target_token) as target_sse:
        _share(checklist_id, target_id, "edit")
        assert target_sse.received(cl_id=checklist_id, upd_prop="notification")
