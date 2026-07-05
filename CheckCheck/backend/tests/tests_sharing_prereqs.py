"""Integration tests for the frontend backend prerequisites (P0.1 + P0.2).

P0.1 — every ``CheckListApiWithSubObj`` the API returns carries ``owner_id`` and
the caller's effective ``my_permission`` on the ``view < check < edit < owner``
ladder. This is the single field the permission-aware UI gates on, so it is
exercised across all the surfaces that return a card: create, single GET, the
grid list, update, the anonymous public read, public-link join, and (in invite
mode) accept-invite.

P0.2 — an unauthenticated ``GET /public-config`` exposes the four sharing feature
flags so the client can hide UI a disabled feature would 404 on.

The global default login (set by conftest) is the admin user, who acts as the
card *owner* throughout. Collaborators get their own tokens via
``authorize_for_access_token`` and are passed to ``req`` as ``access_token=``.
"""

from typing import Dict, Optional

from utils import (
    req,
    server_config,
    authorize_for_access_token,
    create_test_user,
    dict_must_contain,
    find_first_dict_in_list,
)


# ── helpers (mirror tests_sharing.py) ─────────────────────────────────────────


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _user_id(token: str) -> str:
    return req("api/user/me", access_token=token)["id"]


def _create_checklist(name: str) -> Dict:
    return req("api/checklist", "post", b={"name": name, "color_id": "yellow"})


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
    password: Optional[str] = None,
) -> Dict:
    body: Dict = {"permission": permission}
    if password is not None:
        body["password"] = password
    return req(f"api/checklist/{checklist_id}/public-links", "post", b=body)


# ── P0.1: owner_id + my_permission on the card read model ─────────────────────


def test_create_checklist_exposes_owner_and_owner_permission():
    owner_id = req("api/user/me")["id"]
    card = _create_checklist("Prereq-Create")
    dict_must_contain(
        card,
        {"owner_id": owner_id, "my_permission": "owner"},
        required_keys=["id", "position", "labels"],
    )


def test_get_checklist_my_permission_reflects_caller_level():
    owner_id = req("api/user/me")["id"]
    checklist_id = _create_checklist("Prereq-Get")["id"]

    # owner sees "owner"
    as_owner = req(f"api/checklist/{checklist_id}")
    dict_must_contain(as_owner, {"owner_id": owner_id, "my_permission": "owner"})

    # each collaborator level is reflected back on the single GET
    for level in ("view", "check", "edit"):
        collab_token = _make_user_token(f"prereq-get-{level}")
        collab_id = _user_id(collab_token)
        _share(checklist_id, collab_id, level)
        as_collab = req(f"api/checklist/{checklist_id}", access_token=collab_token)
        dict_must_contain(
            as_collab,
            # owner_id is always the real owner, never the caller
            {"owner_id": owner_id, "my_permission": level},
        )


def test_grid_list_carries_owner_and_my_permission():
    owner_id = req("api/user/me")["id"]
    collab_token = _make_user_token("prereq-grid-collab")
    collab_id = _user_id(collab_token)

    checklist_id = _create_checklist("Prereq-Grid")["id"]
    _share(checklist_id, collab_id, "check")

    # owner's grid: their own card reads "owner"
    owner_listing = req("api/checklist")
    owner_entry = find_first_dict_in_list(owner_listing["items"], {"id": checklist_id})
    dict_must_contain(owner_entry, {"owner_id": owner_id, "my_permission": "owner"})

    # collaborator's grid: same card, scoped to their level
    collab_listing = req("api/checklist", access_token=collab_token)
    collab_entry = find_first_dict_in_list(
        collab_listing["items"], {"id": checklist_id}
    )
    dict_must_contain(collab_entry, {"owner_id": owner_id, "my_permission": "check"})


def test_update_checklist_returns_my_permission_for_editor():
    owner_id = req("api/user/me")["id"]
    editor_token = _make_user_token("prereq-update-editor")
    editor_id = _user_id(editor_token)

    checklist_id = _create_checklist("Prereq-Update")["id"]
    _share(checklist_id, editor_id, "edit")

    # owner update -> "owner"
    as_owner = req(f"api/checklist/{checklist_id}", "patch", b={"text": "by owner"})
    dict_must_contain(as_owner, {"owner_id": owner_id, "my_permission": "owner"})

    # editor update -> "edit"
    as_editor = req(
        f"api/checklist/{checklist_id}",
        "patch",
        b={"text": "by editor"},
        access_token=editor_token,
    )
    dict_must_contain(as_editor, {"owner_id": owner_id, "my_permission": "edit"})


def test_public_read_my_permission_is_link_level():
    owner_id = req("api/user/me")["id"]
    checklist_id = _create_checklist("Prereq-Public")["id"]
    _create_item(checklist_id, "milk")

    for level in ("view", "check", "edit"):
        link = _create_public_link(checklist_id, permission=level)
        card = req(
            f"api/public/checklist/{link['token']}",
            suppress_auth=True,
        )
        # An anonymous viewer still sees the real owner_id, and my_permission is
        # capped at the link's level — never "owner".
        dict_must_contain(card, {"owner_id": owner_id, "my_permission": level})


def test_public_join_my_permission_for_joining_user():
    owner_id = req("api/user/me")["id"]
    joiner_token = _make_user_token("prereq-join")

    checklist_id = _create_checklist("Prereq-Join")["id"]
    link = _create_public_link(checklist_id, permission="check")

    card = req(
        f"api/public/checklist/{link['token']}/join",
        "post",
        access_token=joiner_token,
    )
    # The joiner is now a real collaborator at the link's level.
    dict_must_contain(card, {"owner_id": owner_id, "my_permission": "check"})


def test_owner_join_own_public_link_reports_owner():
    owner_id = req("api/user/me")["id"]
    checklist_id = _create_checklist("Prereq-Join-Own")["id"]
    link = _create_public_link(checklist_id, permission="view")

    # The owner opening their own link's join must still read as "owner".
    card = req(f"api/public/checklist/{link['token']}/join", "post")
    dict_must_contain(card, {"owner_id": owner_id, "my_permission": "owner"})


# (Accept-invite's my_permission is asserted in tests_sharing_invites.py, which is
# the module the invite-flow test pass boots with SHARING_REQUIRE_INVITE_ACCEPT=1.)


# ── P0.2: unauthenticated public-config ───────────────────────────────────────


def test_public_config_unauthenticated_returns_flags():
    res = req("api/public-config", suppress_auth=True)
    dict_must_contain(
        res,
        required_keys=[
            "sharing_enabled",
            "sharing_public_links_enabled",
            "sharing_user_search_enabled",
            "sharing_require_invite_accept",
            "api_token_default_expiry_days",
            "api_token_allow_never_expire",
        ],
    )
    # the flags must be booleans
    for key in (
        "sharing_enabled",
        "sharing_public_links_enabled",
        "sharing_user_search_enabled",
        "sharing_require_invite_accept",
        "api_token_allow_never_expire",
    ):
        assert isinstance(res[key], bool), f"{key} must be a bool, got {type(res[key])}"
    # the default-expiry hint is an int (whole days) or null (server default is never)
    assert res["api_token_default_expiry_days"] is None or isinstance(
        res["api_token_default_expiry_days"], int
    ), "api_token_default_expiry_days must be an int or null"


def test_public_config_reflects_server_flags():
    """The endpoint mirrors the live server config — including the invite flag,
    which differs between the default and the invite-flow test passes."""
    res = req("api/public-config", suppress_auth=True)
    expected_default_days = (
        None
        if server_config.API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES is None
        else max(1, round(server_config.API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES / (60 * 24)))
    )
    dict_must_contain(
        res,
        {
            "sharing_enabled": server_config.SHARING_ENABLED,
            "sharing_public_links_enabled": server_config.SHARING_PUBLIC_LINKS_ENABLED,
            "sharing_user_search_enabled": server_config.SHARING_USER_SEARCH_ENABLED,
            "sharing_require_invite_accept": server_config.SHARING_REQUIRE_INVITE_ACCEPT,
            "api_token_allow_never_expire": server_config.API_TOKEN_ALLOW_NEVER_EXPIRE,
            "api_token_default_expiry_days": expected_default_days,
        },
    )
