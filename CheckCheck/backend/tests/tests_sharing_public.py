"""Integration tests for public URL sharing — Phase 5 of card sharing.

Covers the owner-only link-management API (``/checklist/{id}/public-links``), the
anonymous consumption surface (``/public/checklist/{token}/...``), token-keyed
live sync (``/sync?token=``) and the link lifecycle. See
``docs/archive/CARD_SHARING_PHASE5_TEST_NOTES.md`` for the captured case list.

The global default login (set by conftest) is the admin user, who acts as the
card *owner* throughout. Anonymous calls pass ``suppress_auth=True`` so no bearer
token leaks in. Collaborators get their own tokens via
``authorize_for_access_token`` and are passed to ``req`` as ``access_token=``.

The conftest boots a single session-scoped server with the default config
(``SHARING_PUBLIC_LINKS_ENABLED=True``). The config-off behaviour needs a server
booted with the flag off, so it is deferred (see the skipped test at the bottom),
exactly as the Phase 1–4 suite deferred its config-off cases.
"""

import json
import threading
import time
from typing import Callable, Dict, List, Optional

import pytest
import requests

from utils import (
    req,
    get_server_base_url,
    authorize_for_access_token,
    create_test_user,
    get_access_token,
    find_first_dict_in_list,
    list_contains_dict_that_must_contain,
    dict_must_contain,
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


def _create_item(checklist_id: str, text: str) -> str:
    return req(f"api/checklist/{checklist_id}/item", "post", b={"text": text})["id"]


def _share(checklist_id: str, user_id: str, permission: str) -> Dict:
    return req(
        f"api/checklist/{checklist_id}/shares/{user_id}",
        "put",
        b={"permission": permission},
    )


def _create_public_link(
    checklist_id: str,
    permission: str = "view",
    expires_at: Optional[str] = None,
    password: Optional[str] = None,
    access_token: str = None,
    expected_http_code: int = None,
) -> Dict:
    body: Dict = {"permission": permission}
    if expires_at is not None:
        body["expires_at"] = expires_at
    if password is not None:
        body["password"] = password
    return req(
        f"api/checklist/{checklist_id}/public-links",
        "post",
        b=body,
        access_token=access_token,
        expected_http_code=expected_http_code,
    )


def _unlock(token: str, password: str, expected_http_code: int = None) -> Dict:
    return req(
        f"api/public/checklist/{token}/unlock",
        "post",
        b={"password": password},
        suppress_auth=True,
        expected_http_code=expected_http_code,
    )


def _join(token: str, access_token: str = None, suppress_auth: bool = False, expected_http_code: int = None) -> Dict:
    return req(
        f"api/public/checklist/{token}/join",
        "post",
        access_token=access_token,
        suppress_auth=suppress_auth,
        expected_http_code=expected_http_code,
    )


# ── SSE client (supports authed bearer OR anonymous ?token=) ──────────────────


class _SSECollector:
    """Connects to ``/api/sync`` and collects the parsed notification dicts pushed
    to this subscriber on a background thread. Pass ``bearer=`` for a logged-in
    user or ``public_token=`` for an anonymous public-link subscriber."""

    def __init__(self, *, bearer: str = None, public_token: str = None, public_grant: str = None):
        assert bool(bearer) ^ bool(public_token), "pass exactly one of bearer/public_token"
        self._bearer = bearer
        self._public_token = public_token
        self._public_grant = public_grant
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
            url = f"{get_server_base_url()}/api/sync"
            headers = {}
            params = {}
            if self._bearer:
                headers["Authorization"] = f"Bearer {self._bearer}"
            else:
                params["token"] = self._public_token
                if self._public_grant:
                    params["share_grant"] = self._public_grant
            self._resp = requests.get(
                url,
                headers=headers,
                params=params,
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

    def received(self, *, cl_id: str, upd_prop: str, timeout: float = 10.0) -> Optional[Dict]:
        return self.wait_for(
            lambda e: e.get("cl_id") == cl_id and e.get("upd_prop") == upd_prop,
            timeout=timeout,
        )


# ── link management: create / list (token returned once, then redacted) ───────


def test_public_link_create_returns_token_once_and_list_redacts():
    checklist_id = _create_checklist("PublicCreate")
    created = _create_public_link(checklist_id, "view")

    dict_must_contain(
        created,
        {"permission": "view", "enabled": True, "checklist_id": checklist_id},
        required_keys=["id", "token", "created_at"],
    )
    assert created["token"], "create must return a non-empty token"

    # The token is a capability — listing must never echo it back.
    links = req(f"api/checklist/{checklist_id}/public-links")
    entry = find_first_dict_in_list(links, {"id": created["id"]})
    assert "token" not in entry, "list endpoint must redact the token"
    dict_must_contain(entry, {"permission": "view", "enabled": True})


def test_public_link_management_is_owner_only():
    """Creating/listing links is owner-only: an unrelated user gets 403 (no access
    at all), a non-owner collaborator gets 403 (privilege-escalation guard)."""
    outsider_token = _make_user_token("pub-mgmt-outsider")
    editor_token = _make_user_token("pub-mgmt-editor")
    editor_id = _user_id(editor_token)

    checklist_id = _create_checklist("PublicOwnerOnly")
    _share(checklist_id, editor_id, "edit")

    # unrelated user -> 403
    _create_public_link(checklist_id, "view", access_token=outsider_token, expected_http_code=403)
    req(
        f"api/checklist/{checklist_id}/public-links",
        access_token=outsider_token,
        expected_http_code=403,
    )
    # non-owner collaborator -> 403
    _create_public_link(checklist_id, "view", access_token=editor_token, expected_http_code=403)
    req(
        f"api/checklist/{checklist_id}/public-links",
        access_token=editor_token,
        expected_http_code=403,
    )


# ── link management: patch (toggle / level / expiry) ──────────────────────────


def test_public_link_patch_toggle_level_and_expiry():
    checklist_id = _create_checklist("PublicPatch")
    item_id = _create_item(checklist_id, "milk")
    link = _create_public_link(checklist_id, "view")
    token = link["token"]
    link_id = link["id"]

    # enabled while up
    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=200)

    # disable -> anonymous resolve now 404
    req(
        f"api/checklist/{checklist_id}/public-links/{link_id}",
        "patch",
        b={"enabled": False},
        expected_http_code=200,
    )
    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=404)

    # re-enable
    req(
        f"api/checklist/{checklist_id}/public-links/{link_id}",
        "patch",
        b={"enabled": True},
        expected_http_code=200,
    )
    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=200)

    # raise level to edit -> anonymous create now allowed
    req(
        f"api/checklist/{checklist_id}/public-links/{link_id}",
        "patch",
        b={"permission": "edit"},
        expected_http_code=200,
    )
    req(
        f"api/public/checklist/{token}/item",
        "post",
        b={"text": "bread"},
        suppress_auth=True,
        expected_http_code=200,
    )

    # set a future expiry, then a PATCH of an unrelated field must NOT clear it
    # (exclude_unset: only fields actually sent are applied).
    req(
        f"api/checklist/{checklist_id}/public-links/{link_id}",
        "patch",
        b={"expires_at": "2999-01-01T00:00:00"},
        expected_http_code=200,
    )
    req(
        f"api/checklist/{checklist_id}/public-links/{link_id}",
        "patch",
        b={"permission": "view"},
        expected_http_code=200,
    )
    links = req(f"api/checklist/{checklist_id}/public-links")
    entry = find_first_dict_in_list(links, {"id": link_id})
    assert entry["expires_at"] is not None, "PATCH of permission wrongly cleared expires_at"

    # past expiry -> anonymous resolve 404
    req(
        f"api/checklist/{checklist_id}/public-links/{link_id}",
        "patch",
        b={"expires_at": "2000-01-01T00:00:00"},
        expected_http_code=200,
    )
    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=404)

    # clear expiry explicitly (null) -> resolves again
    req(
        f"api/checklist/{checklist_id}/public-links/{link_id}",
        "patch",
        b={"expires_at": None},
        expected_http_code=200,
    )
    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=200)


def test_public_link_accepts_timezone_aware_expiry():
    """Regression: a client (e.g. JS ``Date.toISOString()``) sends a tz-aware
    expiry like ``"...Z"``. The expiry column is naive-UTC, so the value must be
    normalised to naive UTC on the way in — otherwise Postgres rejects the insert
    (tz-aware into a tz-naive column → 500) and the resolver's naive/aware
    comparison would raise. A future tz-aware expiry must create cleanly and
    resolve; a past one must 404."""
    checklist_id = _create_checklist("PublicTzExpiry")

    future = _create_public_link(checklist_id, "view", expires_at="2999-01-01T00:00:00Z")
    assert future["expires_at"] is not None
    req(f"api/public/checklist/{future['token']}", suppress_auth=True, expected_http_code=200)

    past = _create_public_link(checklist_id, "view", expires_at="2000-01-01T00:00:00+00:00")
    req(f"api/public/checklist/{past['token']}", suppress_auth=True, expected_http_code=404)

    # the same normalisation must apply on PATCH
    req(
        f"api/checklist/{checklist_id}/public-links/{future['id']}",
        "patch",
        b={"expires_at": "2000-06-01T12:00:00Z"},
        expected_http_code=200,
    )
    req(f"api/public/checklist/{future['token']}", suppress_auth=True, expected_http_code=404)


def test_public_link_patch_is_owner_only():
    editor_token = _make_user_token("pub-patch-editor")
    editor_id = _user_id(editor_token)
    checklist_id = _create_checklist("PublicPatchGuard")
    _share(checklist_id, editor_id, "edit")
    link = _create_public_link(checklist_id, "view")

    req(
        f"api/checklist/{checklist_id}/public-links/{link['id']}",
        "patch",
        b={"enabled": False},
        access_token=editor_token,
        expected_http_code=403,
    )


def test_public_link_patch_and_delete_cross_checklist_404():
    """A link id from another checklist must not be reachable via this card's path
    (no cross-card leak); 404 for both PATCH and DELETE."""
    checklist_a = _create_checklist("PublicCrossA")
    checklist_b = _create_checklist("PublicCrossB")
    link_b = _create_public_link(checklist_b, "view")

    req(
        f"api/checklist/{checklist_a}/public-links/{link_b['id']}",
        "patch",
        b={"enabled": False},
        expected_http_code=404,
    )
    req(
        f"api/checklist/{checklist_a}/public-links/{link_b['id']}",
        "delete",
        expected_http_code=404,
    )
    # link B is untouched
    req(f"api/public/checklist/{link_b['token']}", suppress_auth=True, expected_http_code=200)


# ── link management: delete ───────────────────────────────────────────────────


def test_public_link_delete_revokes_access():
    checklist_id = _create_checklist("PublicDelete")
    link = _create_public_link(checklist_id, "view")
    token = link["token"]

    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=200)
    req(
        f"api/checklist/{checklist_id}/public-links/{link['id']}",
        "delete",
        expected_http_code=204,
    )
    # token no longer resolves
    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=404)


def test_public_link_delete_is_owner_only():
    editor_token = _make_user_token("pub-delete-editor")
    editor_id = _user_id(editor_token)
    checklist_id = _create_checklist("PublicDeleteGuard")
    _share(checklist_id, editor_id, "edit")
    link = _create_public_link(checklist_id, "view")

    req(
        f"api/checklist/{checklist_id}/public-links/{link['id']}",
        "delete",
        access_token=editor_token,
        expected_http_code=403,
    )
    # still resolves (not deleted)
    req(f"api/public/checklist/{link['token']}", suppress_auth=True, expected_http_code=200)


# ── anonymous consumption: read ───────────────────────────────────────────────


def test_public_anonymous_get_card_and_items():
    checklist_id = _create_checklist("PublicRead")
    _create_item(checklist_id, "milk")
    _create_item(checklist_id, "bread")
    token = _create_public_link(checklist_id, "view")["token"]

    card = req(f"api/public/checklist/{token}", suppress_auth=True)
    dict_must_contain(card, {"id": checklist_id, "name": "PublicRead"})

    items = req(f"api/public/checklist/{token}/item", suppress_auth=True)
    assert items["total_count"] == 2
    texts = sorted(i["text"] for i in items["items"])
    assert texts == ["bread", "milk"]


def test_public_view_renders_owner_labels_not_collaborators():
    """An anonymous visitor renders with the owner's per-user settings. Labels are
    a per-user layer, so the public view must show the owner's labels — never a
    collaborator's private ones."""
    collab_token = _make_user_token("pub-label-collab")
    collab_id = _user_id(collab_token)
    checklist_id = _create_checklist("PublicLabels")
    _share(checklist_id, collab_id, "view")

    owner_label = req("api/label", "post", b={"display_name": "owner-only-label"})
    req(f"api/checklist/{checklist_id}/label/{owner_label['id']}", "put")

    collab_label = req(
        "api/label",
        "post",
        b={"display_name": "collab-only-label"},
        access_token=collab_token,
    )
    req(
        f"api/checklist/{checklist_id}/label/{collab_label['id']}",
        "put",
        access_token=collab_token,
    )

    token = _create_public_link(checklist_id, "view")["token"]
    card = req(f"api/public/checklist/{token}", suppress_auth=True)
    assert [l["display_name"] for l in card["labels"]] == ["owner-only-label"]


def test_public_unknown_disabled_expired_token_all_404():
    # unknown token
    req("api/public/checklist/this-token-does-not-exist", suppress_auth=True, expected_http_code=404)

    checklist_id = _create_checklist("PublicResolveGuards")
    # disabled link
    disabled = _create_public_link(checklist_id, "view")
    req(
        f"api/checklist/{checklist_id}/public-links/{disabled['id']}",
        "patch",
        b={"enabled": False},
    )
    req(f"api/public/checklist/{disabled['token']}", suppress_auth=True, expected_http_code=404)

    # already-expired link (created with a past expiry)
    expired = _create_public_link(checklist_id, "view", expires_at="2000-01-01T00:00:00")
    req(f"api/public/checklist/{expired['token']}", suppress_auth=True, expected_http_code=404)


# ── anonymous consumption: permission-level enforcement ───────────────────────


def test_public_permission_levels_enforced():
    checklist_id = _create_checklist("PublicPerms")
    item_id = _create_item(checklist_id, "milk")

    view_token = _create_public_link(checklist_id, "view")["token"]
    check_token = _create_public_link(checklist_id, "check")["token"]
    edit_token = _create_public_link(checklist_id, "edit")["token"]

    # view: GET ok; cannot check, edit text, create or delete
    req(f"api/public/checklist/{view_token}", suppress_auth=True, expected_http_code=200)
    req(
        f"api/public/checklist/{view_token}/item/{item_id}/state",
        "patch",
        b={"checked": True},
        suppress_auth=True,
        expected_http_code=403,
    )
    req(
        f"api/public/checklist/{view_token}/item/{item_id}",
        "patch",
        b={"text": "nope"},
        suppress_auth=True,
        expected_http_code=403,
    )
    req(
        f"api/public/checklist/{view_token}/item",
        "post",
        b={"text": "nope"},
        suppress_auth=True,
        expected_http_code=403,
    )
    req(
        f"api/public/checklist/{view_token}/item/{item_id}",
        "delete",
        suppress_auth=True,
        expected_http_code=403,
    )

    # check: state toggle ok; still cannot edit text / create
    req(
        f"api/public/checklist/{check_token}/item/{item_id}/state",
        "patch",
        b={"checked": True},
        suppress_auth=True,
        expected_http_code=200,
    )
    req(
        f"api/public/checklist/{check_token}/item/{item_id}",
        "patch",
        b={"text": "nope"},
        suppress_auth=True,
        expected_http_code=403,
    )
    req(
        f"api/public/checklist/{check_token}/item",
        "post",
        b={"text": "nope"},
        suppress_auth=True,
        expected_http_code=403,
    )

    # edit: state + text + create + delete all ok
    req(
        f"api/public/checklist/{edit_token}/item/{item_id}",
        "patch",
        b={"text": "oat milk"},
        suppress_auth=True,
        expected_http_code=200,
    )
    new_item = req(
        f"api/public/checklist/{edit_token}/item",
        "post",
        b={"text": "bread"},
        suppress_auth=True,
        expected_http_code=200,
    )
    req(
        f"api/public/checklist/{edit_token}/item/{new_item['id']}",
        "delete",
        suppress_auth=True,
        expected_http_code=200,
    )


def test_public_cross_checklist_item_idor():
    """An item id that lives in checklist B must not be reachable through a token
    that grants checklist A (404, no foreign-item leak)."""
    checklist_a = _create_checklist("PublicIDOR-A")
    checklist_b = _create_checklist("PublicIDOR-B")
    item_in_b = _create_item(checklist_b, "secret-b")
    edit_token_a = _create_public_link(checklist_a, "edit")["token"]

    req(
        f"api/public/checklist/{edit_token_a}/item/{item_in_b}/state",
        "patch",
        b={"checked": True},
        suppress_auth=True,
        expected_http_code=404,
    )
    req(
        f"api/public/checklist/{edit_token_a}/item/{item_in_b}",
        "patch",
        b={"text": "pwn"},
        suppress_auth=True,
        expected_http_code=404,
    )
    req(
        f"api/public/checklist/{edit_token_a}/item/{item_in_b}",
        "delete",
        suppress_auth=True,
        expected_http_code=404,
    )


# ── token-keyed SSE ───────────────────────────────────────────────────────────


def test_public_sse_anonymous_viewer_receives_authed_edit():
    """An anonymous viewer connected via ``/sync?token=`` gets live updates when an
    authed editor changes the card. Also asserts the routing fields
    (target_tokens / target_user_ids) are stripped from the client payload."""
    checklist_id = _create_checklist("SSE-public-in")
    item_id = _create_item(checklist_id, "milk")
    token = _create_public_link(checklist_id, "view")["token"]

    with _SSECollector(public_token=token) as anon_sse:
        req(f"api/checklist/{checklist_id}/item/{item_id}/state", "patch", b={"checked": True})
        ev = anon_sse.received(cl_id=checklist_id, upd_prop="item_state")
        assert ev is not None, "anonymous viewer did not receive the authed editor's change"
        assert "target_tokens" not in ev, "target_tokens must be stripped from the SSE payload"
        assert "target_user_ids" not in ev, "target_user_ids must be stripped from the SSE payload"


def test_public_sse_owner_receives_anonymous_edit():
    """When an anonymous visitor with an edit link changes the card, the owner's
    authed SSE client receives the matching update."""
    owner_token = get_access_token()
    checklist_id = _create_checklist("SSE-public-out")
    item_id = _create_item(checklist_id, "milk")
    token = _create_public_link(checklist_id, "edit")["token"]

    with _SSECollector(bearer=owner_token) as owner_sse:
        req(
            f"api/public/checklist/{token}/item/{item_id}/state",
            "patch",
            b={"checked": True},
            suppress_auth=True,
        )
        assert owner_sse.received(
            cl_id=checklist_id, upd_prop="item_state"
        ), "owner was not notified of the anonymous visitor's change"


def test_public_sse_token_only_receives_its_own_checklist():
    """An anonymous token grants exactly one card; its SSE stream must never carry
    updates for an unrelated card."""
    card_a = _create_checklist("SSE-scope-A")
    item_a = _create_item(card_a, "a-milk")
    card_b = _create_checklist("SSE-scope-B")
    item_b = _create_item(card_b, "b-milk")
    token_a = _create_public_link(card_a, "view")["token"]

    with _SSECollector(public_token=token_a) as anon_sse:
        # change the *other* card first
        req(f"api/checklist/{card_b}/item/{item_b}/state", "patch", b={"checked": True})
        # then change the token's own card
        req(f"api/checklist/{card_a}/item/{item_a}/state", "patch", b={"checked": True})

        # the own-card update must arrive...
        assert anon_sse.received(
            cl_id=card_a, upd_prop="item_state"
        ), "anonymous viewer missed its own card's update"
        # ...and the unrelated card must never have been delivered
        assert (
            anon_sse.wait_for(lambda e: e.get("cl_id") == card_b, timeout=2.0) is None
        ), "anonymous viewer wrongly received an unrelated card's update"


def test_public_sse_rejects_disabled_expired_unknown_token():
    """A disabled / expired / unknown token must be rejected on /sync?token= (401),
    so no anonymous stream is opened for it."""
    req("api/sync", q={"token": "totally-unknown-token"}, suppress_auth=True, expected_http_code=401)

    checklist_id = _create_checklist("SSE-reject")
    disabled = _create_public_link(checklist_id, "view")
    req(
        f"api/checklist/{checklist_id}/public-links/{disabled['id']}",
        "patch",
        b={"enabled": False},
    )
    req("api/sync", q={"token": disabled["token"]}, suppress_auth=True, expected_http_code=401)

    expired = _create_public_link(checklist_id, "view", expires_at="2000-01-01T00:00:00")
    req("api/sync", q={"token": expired["token"]}, suppress_auth=True, expected_http_code=401)


# ── lifecycle / cascade ───────────────────────────────────────────────────────


def test_public_link_resolves_404_after_checklist_deleted():
    """Deleting the checklist (owner) makes its public link stop resolving."""
    checklist_id = _create_checklist("PublicCascade")
    token = _create_public_link(checklist_id, "view")["token"]
    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=200)

    req(f"api/checklist/{checklist_id}", "delete", expected_http_code=204)
    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=404)


# ── Phase 6: join via public link (POST /public/checklist/{token}/join) ───────


def test_join_adds_logged_in_user_as_collaborator():
    """Happy path: a logged-in user joins via a link → gets the card, the card
    appears in their grid, and a collaborator row at the link's level exists."""
    joiner_token = _make_user_token("join-happy")
    joiner_id = _user_id(joiner_token)
    checklist_id = _create_checklist("JoinHappy")
    token = _create_public_link(checklist_id, "check")["token"]

    # not in the joiner's grid before joining
    listing = req("api/checklist", access_token=joiner_token)
    assert not list_contains_dict_that_must_contain(
        listing["items"], {"id": checklist_id}, raise_if_not_fullfilled=False
    )

    card = _join(token, access_token=joiner_token, expected_http_code=200)
    dict_must_contain(card, {"id": checklist_id, "name": "JoinHappy"})

    # now in their grid
    listing = req("api/checklist", access_token=joiner_token)
    assert list_contains_dict_that_must_contain(listing["items"], {"id": checklist_id})

    # owner sees a real collaborator at the link's level
    shares = req(f"api/checklist/{checklist_id}/shares")
    entry = find_first_dict_in_list(shares, {"user_id": joiner_id})
    dict_must_contain(entry, {"permission": "check"})


def test_join_level_matches_link_and_is_enforced():
    """Joining a ``view`` link grants exactly ``view``: the joiner can read the
    card via the normal authed routes but cannot toggle state (403), enforced by
    the ordinary collaborator guards (not the token path)."""
    joiner_token = _make_user_token("join-view-level")
    checklist_id = _create_checklist("JoinViewLevel")
    item_id = _create_item(checklist_id, "milk")
    token = _create_public_link(checklist_id, "view")["token"]

    _join(token, access_token=joiner_token, expected_http_code=200)

    # can read via the authed route...
    req(f"api/checklist/{checklist_id}", access_token=joiner_token, expected_http_code=200)
    # ...but a view collaborator cannot check or edit
    req(
        f"api/checklist/{checklist_id}/item/{item_id}/state",
        "patch",
        b={"checked": True},
        access_token=joiner_token,
        expected_http_code=403,
    )
    req(
        f"api/checklist/{checklist_id}/item/{item_id}",
        "patch",
        b={"text": "nope"},
        access_token=joiner_token,
        expected_http_code=403,
    )


def test_join_requires_login():
    """The join route is the one authenticated endpoint on the public surface:
    logged out → 401 (you need an account to own a deck slot)."""
    checklist_id = _create_checklist("JoinNoAuth")
    token = _create_public_link(checklist_id, "view")["token"]
    _join(token, suppress_auth=True, expected_http_code=401)


def test_join_unknown_disabled_expired_token_404():
    joiner_token = _make_user_token("join-badtoken")

    _join("no-such-token", access_token=joiner_token, expected_http_code=404)

    checklist_id = _create_checklist("JoinResolveGuards")
    disabled = _create_public_link(checklist_id, "view")
    req(
        f"api/checklist/{checklist_id}/public-links/{disabled['id']}",
        "patch",
        b={"enabled": False},
    )
    _join(disabled["token"], access_token=joiner_token, expected_http_code=404)

    expired = _create_public_link(checklist_id, "view", expires_at="2000-01-01T00:00:00")
    _join(expired["token"], access_token=joiner_token, expected_http_code=404)


def test_join_is_idempotent_and_never_downgrades():
    """Joining must not downgrade an existing access level, must be a no-op for the
    owner, and joining twice must not create duplicate collaborator rows."""
    # owner joining their own card -> no-op, still owner, not listed as collaborator
    owner_id = req("api/user/me")["id"]
    checklist_id = _create_checklist("JoinIdempotent")
    view_token = _create_public_link(checklist_id, "view")["token"]
    _join(view_token, expected_http_code=200)  # owner is the global default login
    shares = req(f"api/checklist/{checklist_id}/shares")
    assert not list_contains_dict_that_must_contain(
        shares, {"user_id": owner_id}, raise_if_not_fullfilled=False
    ), "owner must not be added as a collaborator on their own card"
    # owner still has owner powers
    req(f"api/checklist/{checklist_id}/shares", expected_http_code=200)

    # existing 'edit' collaborator joining a 'view' link must stay 'edit'
    joiner_token = _make_user_token("join-nodowngrade")
    joiner_id = _user_id(joiner_token)
    _share(checklist_id, joiner_id, "edit")
    _join(view_token, access_token=joiner_token, expected_http_code=200)
    shares = req(f"api/checklist/{checklist_id}/shares")
    entry = find_first_dict_in_list(shares, {"user_id": joiner_id})
    dict_must_contain(entry, {"permission": "edit"})
    # still able to edit text (proves not downgraded to view)
    item_id = _create_item(checklist_id, "milk")
    req(
        f"api/checklist/{checklist_id}/item/{item_id}",
        "patch",
        b={"text": "oat milk"},
        access_token=joiner_token,
        expected_http_code=200,
    )

    # joining twice does not create a duplicate row
    fresh_token = _make_user_token("join-twice")
    fresh_id = _user_id(fresh_token)
    _join(view_token, access_token=fresh_token, expected_http_code=200)
    _join(view_token, access_token=fresh_token, expected_http_code=200)
    shares = req(f"api/checklist/{checklist_id}/shares")
    matches = [s for s in shares if s["user_id"] == fresh_id]
    assert len(matches) == 1, f"expected exactly one collaborator row, got {len(matches)}"


def test_join_emits_share_added_to_owner():
    """The owner's authed SSE client receives ``share_added`` when a user joins,
    mirroring the collaborator-add notification."""
    owner_token = get_access_token()
    joiner_token = _make_user_token("join-sse-share-added")
    checklist_id = _create_checklist("JoinShareAdded")
    token = _create_public_link(checklist_id, "edit")["token"]

    with _SSECollector(bearer=owner_token) as owner_sse:
        _join(token, access_token=joiner_token, expected_http_code=200)
        assert owner_sse.received(
            cl_id=checklist_id, upd_prop="share_added"
        ), "owner was not notified that a user joined via the public link"


def test_join_switches_user_to_id_keyed_collaborator_sync():
    """After joining, the user syncs as an ordinary collaborator by ``user.id``:
    an owner edit reaches the joiner's authed stream, and the joiner's edit reaches
    the owner — bidirectional, no token involved."""
    owner_token = get_access_token()
    joiner_token = _make_user_token("join-sse-bidir")
    checklist_id = _create_checklist("JoinBidirSync")
    item_id = _create_item(checklist_id, "milk")
    token = _create_public_link(checklist_id, "edit")["token"]
    _join(token, access_token=joiner_token, expected_http_code=200)

    with _SSECollector(bearer=owner_token) as owner_sse, _SSECollector(
        bearer=joiner_token
    ) as joiner_sse:
        # owner edit -> reaches the joiner
        req(f"api/checklist/{checklist_id}/item/{item_id}/state", "patch", b={"checked": True})
        assert joiner_sse.received(
            cl_id=checklist_id, upd_prop="item_state"
        ), "joiner did not receive the owner's edit on their authed stream"

        # joiner edit -> reaches the owner
        req(
            f"api/checklist/{checklist_id}/item/{item_id}",
            "patch",
            b={"text": "oat milk"},
            access_token=joiner_token,
        )
        assert owner_sse.received(
            cl_id=checklist_id, upd_prop="item_text"
        ), "owner did not receive the joiner's edit"


# ── Phase 7: password-protected public links ──────────────────────────────────


def test_protected_link_resolve_requires_grant():
    """A protected link 404s until unlocked: no grant → 404, wrong passphrase →
    404 (no existence leak), correct passphrase → grant → resolves 200; a garbage
    grant → 404."""
    checklist_id = _create_checklist("PwResolve")
    _create_item(checklist_id, "milk")
    created = _create_public_link(checklist_id, "view", password="hunter2")
    token = created["token"]
    assert created["password_protected"] is True

    # no grant -> 404
    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=404)

    # wrong passphrase at /unlock -> same 404 as a missing link (no oracle)
    _unlock(token, "wrong-pass", expected_http_code=404)

    # correct passphrase -> grant; resolving with it works
    unlocked = _unlock(token, "hunter2", expected_http_code=200)
    grant = unlocked["grant"]
    assert grant and unlocked["expires_in"] > 0
    card = req(
        f"api/public/checklist/{token}",
        q={"share_grant": grant},
        suppress_auth=True,
        expected_http_code=200,
    )
    dict_must_contain(card, {"id": checklist_id, "name": "PwResolve"})

    # items are reachable with the grant too
    items = req(
        f"api/public/checklist/{token}/item",
        q={"share_grant": grant},
        suppress_auth=True,
        expected_http_code=200,
    )
    assert items["total_count"] == 1

    # a garbage grant is rejected like no grant
    req(
        f"api/public/checklist/{token}",
        q={"share_grant": "not-a-real-grant"},
        suppress_auth=True,
        expected_http_code=404,
    )


def test_grant_is_bound_to_its_own_link():
    """A grant minted for one protected link must not unlock a different one."""
    card_a = _create_checklist("PwBindA")
    card_b = _create_checklist("PwBindB")
    token_a = _create_public_link(card_a, "view", password="secretA")["token"]
    token_b = _create_public_link(card_b, "view", password="secretB")["token"]

    grant_a = _unlock(token_a, "secretA", expected_http_code=200)["grant"]

    # grant for A does not resolve B
    req(
        f"api/public/checklist/{token_b}",
        q={"share_grant": grant_a},
        suppress_auth=True,
        expected_http_code=404,
    )
    # but it does resolve A
    req(
        f"api/public/checklist/{token_a}",
        q={"share_grant": grant_a},
        suppress_auth=True,
        expected_http_code=200,
    )


def test_unlock_on_unprotected_link_is_400():
    """Unlocking a link that has no passphrase is a 400 (nothing to unlock); only a
    caller already holding a working token sees it, so no new info leaks."""
    checklist_id = _create_checklist("PwUnprotected")
    token = _create_public_link(checklist_id, "view")["token"]
    _unlock(token, "anything", expected_http_code=400)
    # and an unknown token unlocks to 404, not 400
    _unlock("no-such-token", "anything", expected_http_code=404)


def test_patch_add_then_clear_password():
    """An owner can add a passphrase to an existing link and later clear it with an
    explicit null; an unrelated PATCH must not disturb the passphrase."""
    checklist_id = _create_checklist("PwPatch")
    link = _create_public_link(checklist_id, "view")
    token, link_id = link["token"], link["id"]

    # unprotected: resolves with no grant
    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=200)

    # add a passphrase -> now gated
    patched = req(
        f"api/checklist/{checklist_id}/public-links/{link_id}",
        "patch",
        b={"password": "letmein"},
        expected_http_code=200,
    )
    assert patched["password_protected"] is True
    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=404)

    grant = _unlock(token, "letmein", expected_http_code=200)["grant"]
    req(
        f"api/public/checklist/{token}",
        q={"share_grant": grant},
        suppress_auth=True,
        expected_http_code=200,
    )

    # an unrelated PATCH (permission) must NOT clear the passphrase
    req(
        f"api/checklist/{checklist_id}/public-links/{link_id}",
        "patch",
        b={"permission": "check"},
        expected_http_code=200,
    )
    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=404)

    # rotating the passphrase invalidates the old grant
    req(
        f"api/checklist/{checklist_id}/public-links/{link_id}",
        "patch",
        b={"password": "newsecret"},
        expected_http_code=200,
    )
    req(
        f"api/public/checklist/{token}",
        q={"share_grant": grant},
        suppress_auth=True,
        expected_http_code=404,
    )
    new_grant = _unlock(token, "newsecret", expected_http_code=200)["grant"]
    req(
        f"api/public/checklist/{token}",
        q={"share_grant": new_grant},
        suppress_auth=True,
        expected_http_code=200,
    )

    # clear protection with an explicit null -> resolves again with no grant
    cleared = req(
        f"api/checklist/{checklist_id}/public-links/{link_id}",
        "patch",
        b={"password": None},
        expected_http_code=200,
    )
    assert cleared["password_protected"] is False
    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=200)


def test_password_never_appears_in_create_list_or_read():
    """The plaintext and the hash must never be echoed; only the derived
    ``password_protected`` flag is exposed."""
    checklist_id = _create_checklist("PwRedact")
    created = _create_public_link(checklist_id, "view", password="topsecret")

    assert "password" not in created
    assert "password_hash" not in created
    assert "topsecret" not in json.dumps(created)
    assert created["password_protected"] is True

    links = req(f"api/checklist/{checklist_id}/public-links")
    entry = find_first_dict_in_list(links, {"id": created["id"]})
    assert "password" not in entry
    assert "password_hash" not in entry
    assert "topsecret" not in json.dumps(entry)
    assert entry["password_protected"] is True


def test_protected_link_write_levels_enforced_with_grant():
    """With a valid grant the link's permission level is still enforced: a protected
    ``check`` link can toggle state but cannot edit text."""
    checklist_id = _create_checklist("PwLevels")
    item_id = _create_item(checklist_id, "milk")
    token = _create_public_link(checklist_id, "check", password="pw12345")["token"]
    grant = _unlock(token, "pw12345", expected_http_code=200)["grant"]

    req(
        f"api/public/checklist/{token}/item/{item_id}/state",
        "patch",
        b={"checked": True},
        q={"share_grant": grant},
        suppress_auth=True,
        expected_http_code=200,
    )
    req(
        f"api/public/checklist/{token}/item/{item_id}",
        "patch",
        b={"text": "nope"},
        q={"share_grant": grant},
        suppress_auth=True,
        expected_http_code=403,
    )


def test_join_protected_link_requires_grant():
    """A logged-in user cannot bypass the passphrase via join: no grant → 404, valid
    grant → joins as a collaborator."""
    joiner_token = _make_user_token("join-protected")
    joiner_id = _user_id(joiner_token)
    checklist_id = _create_checklist("PwJoin")
    token = _create_public_link(checklist_id, "edit", password="joinpass")["token"]

    # logged in but no grant -> 404 (passphrase not bypassable)
    req(
        f"api/public/checklist/{token}/join",
        "post",
        access_token=joiner_token,
        expected_http_code=404,
    )

    grant = _unlock(token, "joinpass", expected_http_code=200)["grant"]
    card = req(
        f"api/public/checklist/{token}/join",
        "post",
        q={"share_grant": grant},
        access_token=joiner_token,
        expected_http_code=200,
    )
    dict_must_contain(card, {"id": checklist_id})
    shares = req(f"api/checklist/{checklist_id}/shares")
    entry = find_first_dict_in_list(shares, {"user_id": joiner_id})
    dict_must_contain(entry, {"permission": "edit"})


def test_protected_link_sse_requires_grant():
    """The token-keyed SSE stream is the same capability as the read surface: a
    protected link cannot subscribe without a grant (401), but can with one — and
    then receives an authed editor's live change."""
    checklist_id = _create_checklist("PwSSE")
    item_id = _create_item(checklist_id, "milk")
    token = _create_public_link(checklist_id, "view", password="ssepass")["token"]

    # no grant -> /sync?token= rejected
    req("api/sync", q={"token": token}, suppress_auth=True, expected_http_code=401)
    # wrong/garbage grant -> rejected too
    req(
        "api/sync",
        q={"token": token, "share_grant": "nope"},
        suppress_auth=True,
        expected_http_code=401,
    )

    grant = _unlock(token, "ssepass", expected_http_code=200)["grant"]
    with _SSECollector(public_token=token, public_grant=grant) as anon_sse:
        req(f"api/checklist/{checklist_id}/item/{item_id}/state", "patch", b={"checked": True})
        assert anon_sse.received(
            cl_id=checklist_id, upd_prop="item_state"
        ), "unlocked anonymous viewer did not receive the authed editor's change"


# ── config-off (deferred: needs a server booted with the flag off) ────────────


@pytest.mark.skip(
    reason="SHARING_PUBLIC_LINKS_ENABLED=false needs a server booted with that env; "
    "the conftest boots a single session-scoped server with public links ON. "
    "Deferred to a dedicated config-off module/CI invocation, mirroring how the "
    "Phase 1-4 suite deferred its config-off cases. When wired, assert every "
    "/public/... endpoint, every /checklist/{id}/public-links endpoint, the "
    "/public/checklist/{token}/join route, and /sync?token= all return 404 / reject."
)
def test_public_endpoints_disabled_when_flag_off():
    pass
