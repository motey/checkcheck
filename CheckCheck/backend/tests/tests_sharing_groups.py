"""Integration tests for Phase 10 — org / group (OIDC group) sharing.

An owner can share a card with everyone in an OIDC group in one call. Membership
is *snapshotted* at share time (the group is expanded to its current members and
an ordinary collaborator row is created per member), so the whole flow reuses the
per-user share machinery: the invite gate (Phase 8) and the in-app notifications
(Phase 9) apply automatically.

Most tests run in the default suite (instant-add, invite flag OFF). The single
invite-on case runs in the dedicated invite-flow pass that boots the server with
``CHECKCHECK_TEST_SHARING_REQUIRE_INVITE_ACCEPT=1`` (wired into both
``run_backend_tests_with_*.sh`` for this module).

Backend parity (Postgres prod vs SQLite dev) is covered structurally: the group →
members resolver is dialect-specific, and these tests run against whichever backend
the run script selected, so the two scripts together exercise both code paths.
"""

import os

import pytest
import requests

from utils import (
    req,
    server_config,
    create_test_user,
    authorize_for_access_token,
    oidc_login_get_token,
    dict_must_contain,
    find_first_dict_in_list,
    list_contains_dict_that_must_contain,
)
from statics import OIDC_TEST_PROVIDER_SLUG

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


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_user(user_name: str) -> tuple[str, str]:
    """Create a local user, return (bearer_token, user_id)."""
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    token = authorize_for_access_token(user_name, pw)
    user_id = req("api/user/me", access_token=token)["id"]
    return token, user_id


def _set_groups(user_id: str, groups: list[str]) -> None:
    """Set a user's persisted OIDC groups (admin PATCH). Mirrors what an OIDC
    login would persist, without driving the provider for every member."""
    req(f"api/user/{user_id}", "patch", b={"oidc_groups": groups})


def _create_checklist(name: str) -> str:
    return req("api/checklist", "post", b={"name": name, "color_id": "yellow"})["id"]


def _share_user(checklist_id: str, user_id: str, permission: str) -> dict:
    return req(
        f"api/checklist/{checklist_id}/shares/{user_id}",
        "put",
        b={"permission": permission},
    )


def _share_group(checklist_id: str, group: str, permission: str, **kw) -> dict:
    return req(
        f"api/checklist/{checklist_id}/shares/group/{group}",
        "put",
        b={"permission": permission},
        **kw,
    )


def _shares(checklist_id: str) -> list[dict]:
    return req(f"api/checklist/{checklist_id}/shares")


# ── GET /user/me/groups ───────────────────────────────────────────────────────


def test_my_groups_reflects_persisted_oidc_groups():
    """The group picker endpoint returns the caller's own OIDC groups; a local
    user with none gets an empty list."""
    token, user_id = _make_user("group-mygroups")
    assert req("api/user/me/groups", access_token=token) == []

    _set_groups(user_id, ["team-mygroups-a", "team-mygroups-b"])
    groups = req("api/user/me/groups", access_token=token)
    assert set(groups) == {"team-mygroups-a", "team-mygroups-b"}


# ── PUT /checklist/{id}/shares/group/{group} (default suite, flag off) ─────────


@requires_invite_off
def test_group_share_adds_members_skips_owner_and_higher_level():
    """Sharing with a group adds every (non-owner) member at the chosen level;
    a member already at an equal/higher level is skipped (never downgraded), and
    a user in a *different* group is untouched. Members gain real access."""
    group = "team-alpha"
    other_group = "team-beta"

    alice_token, alice_id = _make_user("group-alpha-alice")
    bob_token, bob_id = _make_user("group-alpha-bob")
    carol_token, carol_id = _make_user("group-beta-carol")
    dave_token, dave_id = _make_user("group-alpha-dave")
    _set_groups(alice_id, [group])
    _set_groups(bob_id, [group])
    _set_groups(carol_id, [other_group])
    _set_groups(dave_id, [group])

    checklist_id = _create_checklist("GroupAlpha")
    # Dave is pre-shared at the higher 'edit' level — the group share must not
    # downgrade him to 'view'.
    _share_user(checklist_id, dave_id, "edit")

    result = _share_group(checklist_id, group, "view")
    dict_must_contain(
        result,
        {
            "group": group,
            "permission": "view",
            "total_members": 3,  # alice, bob, dave — carol is in another group
            "added": 2,  # alice + bob
            "skipped": 1,  # dave already at 'edit'
        },
    )

    shares = _shares(checklist_id)
    dict_must_contain(
        find_first_dict_in_list(shares, {"user_id": alice_id}),
        {"permission": "view", "status": "accepted"},
    )
    dict_must_contain(
        find_first_dict_in_list(shares, {"user_id": bob_id}),
        {"permission": "view", "status": "accepted"},
    )
    # Dave is not downgraded.
    dict_must_contain(
        find_first_dict_in_list(shares, {"user_id": dave_id}),
        {"permission": "edit"},
    )
    # Carol (other group) was never added.
    assert not list_contains_dict_that_must_contain(
        shares, {"user_id": carol_id}, raise_if_not_fullfilled=False
    )

    # A newly added member has real access (flag off → instant add).
    req(f"api/checklist/{checklist_id}", access_token=alice_token, expected_http_code=200)
    # And received an in-app 'card_shared' notification for this card.
    notes = req("api/user/me/notifications", access_token=alice_token)
    assert list_contains_dict_that_must_contain(
        notes, {"cl_id": checklist_id, "type": "card_shared"}, raise_if_not_fullfilled=False
    )


@requires_invite_off
def test_group_share_is_idempotent_and_does_not_downgrade_on_repeat():
    """Re-sharing the same group at a lower level skips everyone already at the
    higher level (no downgrade); re-sharing at the same level is a no-op add=0."""
    group = "team-idem"
    _, m1_id = _make_user("group-idem-m1")
    _, m2_id = _make_user("group-idem-m2")
    _set_groups(m1_id, [group])
    _set_groups(m2_id, [group])
    checklist_id = _create_checklist("GroupIdem")

    first = _share_group(checklist_id, group, "edit")
    dict_must_contain(first, {"added": 2, "skipped": 0, "total_members": 2})

    # Same level again — already at 'edit', nothing to do.
    again = _share_group(checklist_id, group, "edit")
    dict_must_contain(again, {"added": 0, "skipped": 2})

    # Lower level — must not downgrade anyone.
    lower = _share_group(checklist_id, group, "view")
    dict_must_contain(lower, {"added": 0, "skipped": 2})
    for uid in (m1_id, m2_id):
        dict_must_contain(
            find_first_dict_in_list(_shares(checklist_id), {"user_id": uid}),
            {"permission": "edit"},
        )


def test_group_share_unknown_group_resolves_empty():
    """An unknown group (or one only local users would be in — they have no
    groups) resolves to no members: a clean empty result, not an error."""
    checklist_id = _create_checklist("GroupEmpty")
    result = _share_group(checklist_id, "no-such-group-xyz", "view")
    dict_must_contain(
        result, {"total_members": 0, "added": 0, "skipped": 0}
    )


@requires_invite_off
def test_group_share_requires_owner():
    """A non-owner collaborator cannot group-share (owner-only → 403); an
    unrelated user has no access at all (401)."""
    collab_token, collab_id = _make_user("group-collab")
    stranger_token, _ = _make_user("group-stranger")
    checklist_id = _create_checklist("GroupOwnerOnly")
    _share_user(checklist_id, collab_id, "edit")  # has access, but not owner

    _share_group(
        checklist_id, "team-alpha", "view",
        access_token=collab_token,
        expected_http_code=403,
    )
    _share_group(
        checklist_id, "team-alpha", "view",
        access_token=stranger_token,
        expected_http_code=401,
    )


# ── invite gate (invite-flow pass, flag on) ───────────────────────────────────


@requires_invite_on
def test_group_share_goes_out_as_invites_when_flag_on():
    """With SHARING_REQUIRE_INVITE_ACCEPT on, a group share creates *pending*
    invites: members have no access yet but the invite is in their inbox and the
    owner's share list shows them pending."""
    group = "team-invite"
    invitee_token, invitee_id = _make_user("group-invite-a")
    _set_groups(invitee_id, [group])
    checklist_id = _create_checklist("GroupInvite")

    result = _share_group(checklist_id, group, "edit")
    dict_must_contain(result, {"added": 1, "total_members": 1})

    # No access yet …
    req(f"api/checklist/{checklist_id}", access_token=invitee_token, expected_http_code=401)
    # … but a pending invite is in the inbox …
    assert list_contains_dict_that_must_contain(
        req("api/user/me/invites", access_token=invitee_token),
        {"checklist_id": checklist_id},
        raise_if_not_fullfilled=False,
    )
    # … and the owner sees it as pending.
    dict_must_contain(
        find_first_dict_in_list(_shares(checklist_id), {"user_id": invitee_id}),
        {"status": "pending", "permission": "edit"},
    )


# ── RESTRICT_USER_SEARCH_TO_OWN_GROUPS (OIDC caller) ──────────────────────────


def _require_oidc() -> str:
    if not os.environ.get("OIDC_MOCK_SERVER_URL"):
        pytest.skip("OIDC mock server not running — skipping OIDC group-restrict test")
    return os.environ["OIDC_MOCK_SERVER_URL"]


def _oidc_login_with_groups(sub: str, groups: list[str]) -> str:
    """Register an OIDC mock user with the given groups, log them in, and return
    their access token. The login persists ``oidc_groups`` on the local user."""
    mock_url = _require_oidc()
    requests.put(
        f"{mock_url}/users/{sub}",
        json={
            "sub": sub,
            "userinfo": {
                "name": sub,
                "email": f"{sub}@test.com",
                "given_name": sub,
                "groups": groups,
            },
        },
    ).raise_for_status()
    return oidc_login_get_token(OIDC_TEST_PROVIDER_SLUG, sub)


@requires_invite_off
def test_restrict_caller_can_share_own_group_but_not_a_foreign_one():
    """With the OIDC provider configured RESTRICT_USER_SEARCH_TO_OWN_GROUPS, an
    OIDC-authed owner may share with a group they belong to, but is forbidden
    (403) from targeting a group they are not a member of."""
    _require_oidc()
    own_group = "oidc-owner-team"
    owner_token = _oidc_login_with_groups("group-restrict-owner", [own_group])

    # A member of the owner's own group (set up as a plain local user).
    _, member_id = _make_user("group-restrict-member")
    _set_groups(member_id, [own_group])

    checklist_id = req(
        "api/checklist",
        "post",
        b={"name": "GroupRestrict", "color_id": "yellow"},
        access_token=owner_token,
    )["id"]

    # Sharing the owner's own group is allowed and adds the member.
    ok = _share_group(
        checklist_id, own_group, "view", access_token=owner_token
    )
    dict_must_contain(ok, {"added": 1, "total_members": 1})

    # Sharing a group the owner does not belong to is forbidden — before any
    # member is touched.
    _share_group(
        checklist_id,
        "some-foreign-group",
        "view",
        access_token=owner_token,
        expected_http_code=403,
    )
