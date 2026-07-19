"""Integration tests for org / group (OIDC group) sharing — now *living* shares.

An owner shares a card with everyone in an OIDC group in one call. The group→level
intent is persisted as a first-class ``CheckListGroupShare`` (source of truth), and
access is *materialized* onto the group's current members as ordinary collaborator
rows — so the whole flow still reuses the per-user share machinery (the invite gate
and the in-app notifications apply automatically). Because it is living, membership
is reconciled: lowering a group's level downgrades its group-derived members, new
members gain access on their next OIDC login, leavers lose it, and revoking the
group removes members it granted while leaving explicit individual shares intact.
An explicit individual share always wins (the reconciler never touches a
``via_group IS NULL`` row).

Most tests run in the default suite (instant-add, invite flag OFF). The single
invite-on case runs in the dedicated invite-flow pass that boots the server with
``CHECKCHECK_TEST_SHARING_REQUIRE_INVITE_ACCEPT=1`` (wired into both
``run_backend_tests_with_*.sh`` for this module).

Backend parity (Postgres prod vs SQLite dev) is covered structurally: the group →
members resolver is dialect-specific, and these tests run against whichever backend
the run script selected, so the two scripts together exercise both code paths.
"""

import os
import time

import pytest
import requests

from utils import (
    req,
    server_config,
    create_test_user,
    authorize_for_access_token,
    oidc_login_get_token,
    get_access_token,
    dict_must_contain,
    find_first_dict_in_list,
    list_contains_dict_that_must_contain,
)
from statics import OIDC_TEST_PROVIDER_SLUG
from tests_sharing_sync import _SSECollector

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


def _group_shares(checklist_id: str, **kw) -> list[dict]:
    return req(f"api/checklist/{checklist_id}/shares/group", **kw)


def _revoke_group(checklist_id: str, group: str, **kw):
    return req(
        f"api/checklist/{checklist_id}/shares/group/{group}", "delete", **kw
    )


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
def test_group_share_relevels_group_derived_members_but_not_explicit():
    """Living semantics: re-sharing the same group at the same level is a no-op;
    re-sharing at a *different* level re-levels the group-derived members (up OR
    down, since the group share is authoritative for them) — but a member holding
    an explicit individual share at a higher level is never touched."""
    group = "team-idem"
    _, m1_id = _make_user("group-idem-m1")
    _, m2_id = _make_user("group-idem-m2")
    _, exp_id = _make_user("group-idem-explicit")
    _set_groups(m1_id, [group])
    _set_groups(m2_id, [group])
    _set_groups(exp_id, [group])
    checklist_id = _create_checklist("GroupIdem")
    # An explicit individual share at 'edit' — must survive any group re-level.
    _share_user(checklist_id, exp_id, "edit")

    first = _share_group(checklist_id, group, "edit")
    # m1 + m2 newly granted; exp_id skipped (explicit share wins).
    dict_must_contain(first, {"added": 2, "skipped": 1, "total_members": 3})

    # Same level again — group-derived already at 'edit', explicit still skipped.
    again = _share_group(checklist_id, group, "edit")
    dict_must_contain(again, {"added": 0, "skipped": 3})

    # Lower level — the group is authoritative for its own members: they drop to
    # 'view'. The explicit 'edit' share is untouched.
    lower = _share_group(checklist_id, group, "view")
    dict_must_contain(lower, {"added": 2, "skipped": 1})
    for uid in (m1_id, m2_id):
        dict_must_contain(
            find_first_dict_in_list(_shares(checklist_id), {"user_id": uid}),
            {"permission": "view"},
        )
    # Explicit share preserved at 'edit'.
    dict_must_contain(
        find_first_dict_in_list(_shares(checklist_id), {"user_id": exp_id}),
        {"permission": "edit"},
    )
    # Provenance is exposed so the people-list UI can hide group-derived rows:
    # group members carry via_group; the explicit share does not.
    shares = _shares(checklist_id)
    assert find_first_dict_in_list(shares, {"user_id": m1_id})["via_group"] == group
    assert find_first_dict_in_list(shares, {"user_id": exp_id})["via_group"] is None


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
    unrelated user has no access at all (403)."""
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
        expected_http_code=403,
    )


# ── GET / DELETE group shares (the first-class group list) ────────────────────


@requires_invite_off
def test_group_shares_are_listed_and_reflect_level():
    """The card records which groups it is shared with (GET .../shares/group),
    each at its own level — the data behind the ShareModal's group list."""
    checklist_id = _create_checklist("GroupList")
    assert _group_shares(checklist_id) == []

    _share_group(checklist_id, "team-list-a", "view")
    _share_group(checklist_id, "team-list-b", "edit")

    listed = _group_shares(checklist_id)
    assert {g["group"] for g in listed} == {"team-list-a", "team-list-b"}
    dict_must_contain(
        find_first_dict_in_list(listed, {"group": "team-list-a"}),
        {"permission": "view"},
    )
    dict_must_contain(
        find_first_dict_in_list(listed, {"group": "team-list-b"}),
        {"permission": "edit"},
    )

    # Non-owner cannot see the group list.
    stranger_token, _ = _make_user("group-list-stranger")
    _group_shares(checklist_id, access_token=stranger_token, expected_http_code=403)


@requires_invite_off
def test_revoke_group_removes_group_only_members_keeps_explicit_and_other_group():
    """Revoking a group share removes members who had access only via that group,
    but leaves an explicit individual share intact and keeps a member who is still
    covered by another group share (dropped to that group's level)."""
    group = "team-rev"
    other = "team-rev-other"
    alice_token, alice_id = _make_user("group-rev-alice")   # group-only
    bob_token, bob_id = _make_user("group-rev-bob")     # group + explicit edit
    carol_token, carol_id = _make_user("group-rev-carol")  # in both groups
    _set_groups(alice_id, [group])
    _set_groups(bob_id, [group])
    _set_groups(carol_id, [group, other])

    checklist_id = _create_checklist("GroupRevoke")
    _share_user(checklist_id, bob_id, "edit")  # explicit, must survive
    _share_group(checklist_id, group, "view")
    _share_group(checklist_id, other, "check")

    # Everyone can see the card before the revoke.
    for tok in (alice_token, bob_token, carol_token):
        req(f"api/checklist/{checklist_id}", access_token=tok, expected_http_code=200)

    _revoke_group(checklist_id, group, expected_http_code=204)

    # Alice had access only via `group` → removed.
    req(f"api/checklist/{checklist_id}", access_token=alice_token, expected_http_code=403)
    # Bob keeps his explicit 'edit' share.
    req(f"api/checklist/{checklist_id}", access_token=bob_token, expected_http_code=200)
    dict_must_contain(
        find_first_dict_in_list(_shares(checklist_id), {"user_id": bob_id}),
        {"permission": "edit"},
    )
    # Carol stays (still in `other`), dropped to that group's 'check' level.
    req(f"api/checklist/{checklist_id}", access_token=carol_token, expected_http_code=200)
    dict_must_contain(
        find_first_dict_in_list(_shares(checklist_id), {"user_id": carol_id}),
        {"permission": "check"},
    )

    # The group list now shows only the remaining group.
    assert {g["group"] for g in _group_shares(checklist_id)} == {other}


@requires_invite_off
def test_bulk_group_revoke_emits_single_share_removed_broadcast():
    """A bulk group revoke removes N members from one card. The owner (and any
    remaining share-set member) must get exactly ONE broadcast ``share_removed``
    for the card — not one per removed member — while each removed member still
    gets their own targeted ``checklist_deleted`` so the card leaves their board."""
    group = "team-bulk-revoke"
    owner_token = get_access_token()  # default login owns cards created via req()
    member_tokens = []
    for i in range(3):
        tok, uid = _make_user(f"group-bulk-rev-{i}")
        _set_groups(uid, [group])
        member_tokens.append(tok)

    checklist_id = _create_checklist("GroupBulkRevoke")
    dict_must_contain(
        _share_group(checklist_id, group, "view"),
        {"added": 3, "total_members": 3},
    )

    # Watch the owner's broadcast stream and one removed member's targeted stream.
    with _SSECollector(owner_token) as owner_sse, _SSECollector(
        member_tokens[0]
    ) as member_sse:
        _revoke_group(checklist_id, group, expected_http_code=204)

        # Per-user pinned poke still reaches each removed member (targeted).
        assert member_sse.received(
            cl_id=checklist_id, upd_prop="checklist_deleted"
        ), "removed member was not told to drop the card"

        # The owner is told the share set changed — and only once for the batch.
        assert owner_sse.received(
            cl_id=checklist_id, upd_prop="share_removed"
        ), "owner was not notified the group share was revoked"
        # Give any extra (pre-fix, per-member) broadcasts time to arrive.
        time.sleep(2.0)
        share_removed = [
            e
            for e in list(owner_sse.events)
            if e.get("cl_id") == checklist_id
            and e.get("upd_prop") == "share_removed"
        ]
        assert len(share_removed) == 1, (
            "expected a single batched 'share_removed' broadcast for a bulk "
            f"group revoke of 3 members, got {len(share_removed)}"
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
    req(f"api/checklist/{checklist_id}", access_token=invitee_token, expected_http_code=403)
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


# ── reconcile on OIDC login (the "living" part) ───────────────────────────────


@requires_invite_off
def test_reconcile_on_login_grants_new_member_and_revokes_leaver():
    """Living membership across logins: a card shared with a group is gained by a
    user who logs in already in that group — even though the share predates their
    account — and lost when they next log in no longer in that group. This is the
    reconcile-on-login path (the only moment a user's OIDC group set is re-read)."""
    _require_oidc()
    group = "team-login-live"

    # A local owner shares a card with the group *before* the member exists.
    owner_token, _ = _make_user("group-login-owner")
    checklist_id = req(
        "api/checklist",
        "post",
        b={"name": "GroupLoginLive", "color_id": "yellow"},
        access_token=owner_token,
    )["id"]
    _share_group(checklist_id, group, "view", access_token=owner_token)

    # The member's first OIDC login (already in the group) materializes access.
    sub = "group-login-live-member"
    member_token = _oidc_login_with_groups(sub, [group])
    req(
        f"api/checklist/{checklist_id}",
        access_token=member_token,
        expected_http_code=200,
    )

    # Logging in again, no longer in the group, removes the group-derived access.
    member_token_after = _oidc_login_with_groups(sub, [])
    req(
        f"api/checklist/{checklist_id}",
        access_token=member_token_after,
        expected_http_code=403,
    )
