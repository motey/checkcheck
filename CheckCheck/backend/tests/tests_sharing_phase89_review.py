"""Additional regression tests found while reviewing Phase 8 (invite/accept) and
Phase 9 (in-app notifications).

These complement ``tests_sharing_invites.py`` / ``tests_sharing_notifications.py``
by pinning a handful of *critical paths* the original suites did not cover:

* a share notification reaches **only** the recipient — never the sharer's own
  feed (delivery is pinned to the target ``user_id``);
* re-sharing an **already-accepted** collaborator at a new level does **not**
  produce a second notification (the ``already_accepted`` branch in
  ``upsert_share`` — guards against notification spam on every level change);
* the one-time ``public_link_opened`` owner notification must **not** fire for a
  link that does not resolve (disabled / expired) — ``link_is_resolvable`` gates
  ``mark_first_opened``, so a disabled/expired open is neither an oracle nor a
  source of notification spam.

All cases here run in the default (flag-off) suite. The invite-flow (flag-on)
robustness case lives in ``tests_sharing_invites.py`` so it runs in the dedicated
second pass.
"""

import datetime
from typing import Dict, List

import pytest

from utils import (
    req,
    server_config,
    authorize_for_access_token,
    create_test_user,
    dict_must_contain,
    find_first_dict_in_list,
    get_access_token,
    list_contains_dict_that_must_contain,
)

INVITE_REQUIRED = server_config.SHARING_REQUIRE_INVITE_ACCEPT
# Sharing a card produces card_invited when the invite flow is on, else card_shared.
SHARE_NOTI_TYPE = "card_invited" if INVITE_REQUIRED else "card_shared"

requires_invite_off = pytest.mark.skipif(
    INVITE_REQUIRED,
    reason="instant-add path — runs in the default suite (flag off).",
)


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


def _notifications(token: str, unread_only: bool = False) -> List[Dict]:
    q = {"unread_only": "true"} if unread_only else None
    return req("api/user/me/notifications", q=q, access_token=token)


def _owner_token() -> str:
    """The conftest stores the admin/owner token as the global default login."""
    return get_access_token()


def _notis_for_card(token: str, checklist_id: str, type_: str = None) -> List[Dict]:
    return [
        n
        for n in _notifications(token)
        if n["cl_id"] == checklist_id and (type_ is None or n["type"] == type_)
    ]


def _create_public_link(checklist_id: str, **body) -> Dict:
    return req(
        f"api/checklist/{checklist_id}/public-links",
        "post",
        b={"permission": "view", **body},
    )


# ── notification delivery is pinned to the recipient ──────────────────────────


def test_share_notification_only_reaches_recipient_not_sharer():
    """Sharing a card notifies the *target* only. The sharing owner must not see a
    card_shared/card_invited entry for that card in their own feed — delivery is
    pinned to the target user id."""
    target_token = _make_user_token("p89-recipient-only")
    target_id = _user_id(target_token)
    checklist_id = _create_checklist("P89RecipientOnly")

    _share(checklist_id, target_id, "edit")

    # recipient gets exactly one share notification for this card
    target_share_notis = _notis_for_card(target_token, checklist_id, SHARE_NOTI_TYPE)
    assert len(target_share_notis) == 1, target_share_notis

    # the owner (the sharer) gets no share notification about their own action
    owner_share_notis = [
        n
        for n in _notis_for_card(_owner_token(), checklist_id)
        if n["type"] in ("card_shared", "card_invited")
    ]
    assert owner_share_notis == [], owner_share_notis


@requires_invite_off
def test_reshare_of_accepted_collaborator_does_not_duplicate_notification():
    """Re-sharing an already-accepted collaborator at a new level updates the
    permission but must NOT emit a second notification (only a genuinely new grant
    notifies — the ``already_accepted`` branch in upsert_share). Guards against a
    notification on every level change."""
    target_token = _make_user_token("p89-reshare")
    target_id = _user_id(target_token)
    checklist_id = _create_checklist("P89Reshare")

    first = _share(checklist_id, target_id, "edit")
    dict_must_contain(first, {"status": "accepted", "permission": "edit"})
    assert len(_notis_for_card(target_token, checklist_id, "card_shared")) == 1

    # change the level on the already-accepted collaborator
    second = _share(checklist_id, target_id, "view")
    dict_must_contain(second, {"status": "accepted", "permission": "view"})

    # still exactly one notification — the level change did not re-notify
    assert (
        len(_notis_for_card(target_token, checklist_id, "card_shared")) == 1
    ), "a no-op level change on an accepted collaborator must not re-notify"


# ── public_link_opened must not fire for an unresolvable link ─────────────────


def test_disabled_public_link_open_does_not_notify_owner():
    """Opening a *disabled* link returns 404 and must NOT create a
    public_link_opened notification — link_is_resolvable gates mark_first_opened,
    so a disabled link is neither an existence oracle nor notification spam."""
    checklist_id = _create_checklist("P89Disabled")
    link = _create_public_link(checklist_id, permission="view")
    token = link["token"]

    # disable it before anyone opens it
    req(
        f"api/checklist/{checklist_id}/public-links/{link['id']}",
        "patch",
        b={"enabled": False},
    )

    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=404)

    assert (
        _notis_for_card(_owner_token(), checklist_id, "public_link_opened") == []
    ), "a disabled-link open must not notify the owner"


def test_expired_public_link_open_does_not_notify_owner():
    """Opening an *expired* link returns 404 and must NOT create a
    public_link_opened notification (same gate as disabled)."""
    checklist_id = _create_checklist("P89Expired")
    past = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    ).isoformat()
    link = _create_public_link(checklist_id, permission="view", expires_at=past)
    token = link["token"]

    req(f"api/public/checklist/{token}", suppress_auth=True, expected_http_code=404)

    assert (
        _notis_for_card(_owner_token(), checklist_id, "public_link_opened") == []
    ), "an expired-link open must not notify the owner"
