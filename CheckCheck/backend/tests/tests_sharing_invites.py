"""Integration tests for the Phase 8 invite / accept flow.

The invite flow is gated by the server-side ``SHARING_REQUIRE_INVITE_ACCEPT``
config flag, which changes sharing for the whole process and so cannot be toggled
per-request. This module therefore contains two groups, each guarded by a skip:

* **flag OFF** (the default test server): sharing still instant-adds — these run
  in the normal suite and guard against a regression.
* **flag ON**: sharing creates a *pending* invite the target must accept — these
  run only in the dedicated invite-flow pass that boots the server with
  ``CHECKCHECK_TEST_SHARING_REQUIRE_INVITE_ACCEPT=1`` (wired into both
  ``run_backend_tests_with_*.sh``). The test process reads the same env via
  ``utils.server_config`` so it knows which server it is talking to.

The global default login (set by conftest) is the admin user, who acts as the
card *owner* throughout.
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

requires_invite_on = pytest.mark.skipif(
    not INVITE_REQUIRED,
    reason=(
        "needs the server booted with SHARING_REQUIRE_INVITE_ACCEPT=1 — run the "
        "invite-flow pass (CHECKCHECK_TEST_SHARING_REQUIRE_INVITE_ACCEPT=1)."
    ),
)
requires_invite_off = pytest.mark.skipif(
    INVITE_REQUIRED,
    reason="instant-add path — runs in the default suite (flag off).",
)


# ── helpers (mirror tests_sharing.py) ─────────────────────────────────────────


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _user_id(token: str) -> str:
    return req("api/user/me", access_token=token)["id"]


def _create_checklist(name: str) -> str:
    return req("api/checklist", "post", b={"name": name, "color_id": "yellow"})["id"]


def _create_item(checklist_id: str, text: str) -> str:
    return req(f"api/checklist/{checklist_id}/item", "post", b={"text": text})["id"]


def _share(checklist_id: str, user_id: str, permission: str = "edit") -> Dict:
    return req(
        f"api/checklist/{checklist_id}/shares/{user_id}",
        "put",
        b={"permission": permission},
    )


def _card_in_grid(token: str, checklist_id: str) -> bool:
    listing = req("api/checklist", access_token=token)
    return list_contains_dict_that_must_contain(
        listing["items"], {"id": checklist_id}, raise_if_not_fullfilled=False
    )


# ── SSE client (compact copy of the one in tests_sharing_sync.py) ─────────────


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

    def received(self, *, cl_id: str, upd_prop: str, timeout: float = 10.0) -> bool:
        return (
            self.wait_for(
                lambda e: e.get("cl_id") == cl_id and e.get("upd_prop") == upd_prop,
                timeout=timeout,
            )
            is not None
        )


# ── flag OFF: no regression (default suite) ───────────────────────────────────


@requires_invite_off
def test_share_instant_adds_when_invite_not_required():
    """With SHARING_REQUIRE_INVITE_ACCEPT off, sharing grants access immediately:
    the collaborator can read the card, it shows in their grid, the owner's share
    list shows them 'accepted', and they have no pending invite."""
    collab_token = _make_user_token("invite-off-collab")
    collab_id = _user_id(collab_token)
    checklist_id = _create_checklist("InviteOff")

    res = _share(checklist_id, collab_id, "edit")
    dict_must_contain(res, {"status": "accepted", "permission": "edit"})

    # immediate access + visible in grid
    req(f"api/checklist/{checklist_id}", access_token=collab_token, expected_http_code=200)
    assert _card_in_grid(collab_token, checklist_id)

    # no pending invite for them
    invites = req("api/user/me/invites", access_token=collab_token)
    assert not list_contains_dict_that_must_contain(
        invites, {"checklist_id": checklist_id}, raise_if_not_fullfilled=False
    )

    # owner's share list shows accepted
    shares = req(f"api/checklist/{checklist_id}/shares")
    entry = find_first_dict_in_list(shares, {"user_id": collab_id})
    dict_must_contain(entry, {"status": "accepted"})


# ── flag ON: invite / accept flow ─────────────────────────────────────────────


@requires_invite_on
def test_share_creates_pending_invite_without_access():
    """With the flag on, sharing creates a pending invite: the target has NO
    access and the card is NOT in their grid, but the invite appears in their
    inbox and the owner sees the share as 'pending'."""
    invitee_token = _make_user_token("invite-on-pending")
    invitee_id = _user_id(invitee_token)
    checklist_id = _create_checklist("InvitePending")

    res = _share(checklist_id, invitee_id, "edit")
    dict_must_contain(res, {"status": "pending", "permission": "edit"})

    # no access yet, not in grid
    req(f"api/checklist/{checklist_id}", access_token=invitee_token, expected_http_code=401)
    assert not _card_in_grid(invitee_token, checklist_id)

    # invite shows in the invitee's inbox with the inviter (= owner) info
    invites = req("api/user/me/invites", access_token=invitee_token)
    invite = find_first_dict_in_list(invites, {"checklist_id": checklist_id})
    dict_must_contain(
        invite,
        {"permission": "edit", "checklist_name": "InvitePending"},
        required_keys=["inviter_id", "inviter_user_name", "created_at"],
    )

    # owner's share list shows it as pending
    shares = req(f"api/checklist/{checklist_id}/shares")
    entry = find_first_dict_in_list(shares, {"user_id": invitee_id})
    dict_must_contain(entry, {"status": "pending"})


@requires_invite_on
def test_accept_invite_grants_access_and_notifies_owner():
    """Accepting a pending invite grants access, puts the card in the invitee's
    grid, flips the owner's view to 'accepted', and emits a live share_added the
    owner receives."""
    invitee_token = _make_user_token("invite-on-accept")
    invitee_id = _user_id(invitee_token)
    checklist_id = _create_checklist("InviteAccept")
    _share(checklist_id, invitee_id, "check")

    # before accept: inbox has it, no access
    assert list_contains_dict_that_must_contain(
        req("api/user/me/invites", access_token=invitee_token),
        {"checklist_id": checklist_id},
        raise_if_not_fullfilled=False,
    )
    req(f"api/checklist/{checklist_id}", access_token=invitee_token, expected_http_code=401)

    # owner watches the sync stream while the invitee accepts
    with _SSECollector(_global_owner_token()) as owner_sse:
        card = req(
            f"api/checklist/{checklist_id}/invites/accept",
            "post",
            access_token=invitee_token,
        )
        dict_must_contain(card, {"id": checklist_id})
        assert owner_sse.received(cl_id=checklist_id, upd_prop="share_added")

    # after accept: access, in grid, inbox empty
    req(f"api/checklist/{checklist_id}", access_token=invitee_token, expected_http_code=200)
    assert _card_in_grid(invitee_token, checklist_id)
    assert not list_contains_dict_that_must_contain(
        req("api/user/me/invites", access_token=invitee_token),
        {"checklist_id": checklist_id},
        raise_if_not_fullfilled=False,
    )

    # the 'check' level the invite carried is enforced (can toggle, cannot edit text)
    item_id = _create_item(checklist_id, "milk")
    req(
        f"api/checklist/{checklist_id}/item/{item_id}/state",
        "patch",
        b={"checked": True},
        access_token=invitee_token,
        expected_http_code=200,
    )
    req(
        f"api/checklist/{checklist_id}/item/{item_id}",
        "patch",
        b={"text": "nope"},
        access_token=invitee_token,
        expected_http_code=403,
    )

    # owner's share list now shows accepted
    entry = find_first_dict_in_list(
        req(f"api/checklist/{checklist_id}/shares"), {"user_id": invitee_id}
    )
    dict_must_contain(entry, {"status": "accepted"})


@requires_invite_on
def test_decline_invite_keeps_no_access_and_allows_reinvite():
    """Declining a pending invite leaves the invitee with no access and clears it
    from their inbox; the owner can re-invite, which re-arms a fresh pending."""
    invitee_token = _make_user_token("invite-on-decline")
    invitee_id = _user_id(invitee_token)
    checklist_id = _create_checklist("InviteDecline")
    _share(checklist_id, invitee_id, "edit")

    req(
        f"api/checklist/{checklist_id}/invites/decline",
        "post",
        access_token=invitee_token,
        expected_http_code=204,
    )

    # still no access, not in grid, inbox no longer lists it
    req(f"api/checklist/{checklist_id}", access_token=invitee_token, expected_http_code=401)
    assert not _card_in_grid(invitee_token, checklist_id)
    assert not list_contains_dict_that_must_contain(
        req("api/user/me/invites", access_token=invitee_token),
        {"checklist_id": checklist_id},
        raise_if_not_fullfilled=False,
    )

    # owner sees the declined row
    entry = find_first_dict_in_list(
        req(f"api/checklist/{checklist_id}/shares"), {"user_id": invitee_id}
    )
    dict_must_contain(entry, {"status": "declined"})

    # re-invite re-arms a pending invite (rather than erroring)
    res = _share(checklist_id, invitee_id, "edit")
    dict_must_contain(res, {"status": "pending"})
    assert list_contains_dict_that_must_contain(
        req("api/user/me/invites", access_token=invitee_token),
        {"checklist_id": checklist_id},
        raise_if_not_fullfilled=False,
    )


@requires_invite_on
def test_accept_and_decline_require_a_pending_invite():
    """accept/decline 404 when the caller has no pending invite for the card (an
    uninvited user must not be able to probe a card's existence)."""
    outsider_token = _make_user_token("invite-on-outsider")
    checklist_id = _create_checklist("InviteNoProbe")

    req(
        f"api/checklist/{checklist_id}/invites/accept",
        "post",
        access_token=outsider_token,
        expected_http_code=404,
    )
    req(
        f"api/checklist/{checklist_id}/invites/decline",
        "post",
        access_token=outsider_token,
        expected_http_code=404,
    )


@requires_invite_on
def test_pending_invitee_has_no_access_via_item_routes():
    """A pending invitee is treated like an unrelated user across the item surface,
    not just on GET /checklist: they cannot list a card's items, the card is absent
    from the /item bootstrap listing, and they cannot read an item by id (IDOR).

    This also pins the invariant the grid/bootstrap access query relies on — a
    pending invitee never has a CheckListPosition — since that join is what keeps
    them out of list_access_ids."""
    invitee_token = _make_user_token("invite-on-itemaccess")
    invitee_id = _user_id(invitee_token)
    checklist_id = _create_checklist("InviteItemAccess")
    item_id = _create_item(checklist_id, "secret")
    _share(checklist_id, invitee_id, "edit")  # pending — no access yet

    # cannot list the card's items
    req(
        f"api/checklist/{checklist_id}/item",
        access_token=invitee_token,
        expected_http_code=401,
    )
    # the /item bootstrap listing must not include the pending card
    preview = req("api/item", access_token=invitee_token)
    assert checklist_id not in preview, preview
    # cannot read a specific item by id (cross-card IDOR via an uninvited card)
    req(
        f"api/checklist/{checklist_id}/item/{item_id}",
        access_token=invitee_token,
        expected_http_code=401,
    )


@requires_invite_on
def test_pending_invitee_is_not_fanned_ordinary_edits():
    """A pending invitee is not a live viewer, so ordinary edit notifications are
    NOT delivered to them — until they accept, after which they are."""
    invitee_token = _make_user_token("invite-on-sse")
    invitee_id = _user_id(invitee_token)
    checklist_id = _create_checklist("InviteSSE")
    _share(checklist_id, invitee_id, "edit")

    with _SSECollector(invitee_token) as invitee_sse:
        # owner makes an ordinary edit while the invitee is still pending
        _create_item(checklist_id, "while-pending")
        assert not invitee_sse.received(
            cl_id=checklist_id, upd_prop="item_created", timeout=3.0
        ), "a pending invitee must not receive ordinary edit notifications"

    # accept, then a fresh edit IS delivered
    req(
        f"api/checklist/{checklist_id}/invites/accept",
        "post",
        access_token=invitee_token,
    )
    with _SSECollector(invitee_token) as invitee_sse:
        _create_item(checklist_id, "after-accept")
        assert invitee_sse.received(
            cl_id=checklist_id, upd_prop="item_created"
        ), "an accepted collaborator must receive edit notifications"


def _global_owner_token() -> str:
    """The conftest stores the admin/owner token as the global default login."""
    from utils import get_access_token

    return get_access_token()
