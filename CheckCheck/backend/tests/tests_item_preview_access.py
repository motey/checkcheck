"""Access behaviour of the bootstrap preview endpoint ``GET /api/item``.

The endpoint used to 403 the *entire* batch if any single requested
``checklist_id`` was outside the caller's access set. That id set goes stale as
a normal event in a shared app (a collaborator deletes a card, an owner revokes
a share), and the board asks for previews in batches, so one stale id blanked
the previews of every other card in the same request and surfaced a raw
"Error 403" toast (see docs/plans/E2E_STABILITY.md, bug B2).

It now returns what the caller *can* see and silently omits the rest. These
tests pin both halves of that contract: the accessible ids come back, and the
inaccessible ones are absent from the response entirely (no ids, no item text,
no counts), so nothing about a foreign card leaks.
"""

import requests

from utils import (
    req,
    authorize_for_access_token,
    create_test_user,
    get_server_base_url,
    get_access_token,
)


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _preview(checklist_ids, access_token: str | None = None) -> dict:
    """GET /api/item with REPEATED checklist_ids params.

    ``utils.req`` comma-joins list query values, which FastAPI would reject for a
    repeated-parameter list, so this issues the request directly.
    """
    token = access_token or get_access_token()
    query = "&".join(f"checklist_ids={cl_id}" for cl_id in checklist_ids)
    response = requests.get(
        f"{get_server_base_url()}/api/item?{query}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, (response.status_code, response.text)
    return response.json()


def _setup_owned_and_foreign():
    """Returns (caller_token, owned_id, foreign_id).

    The caller owns ``owned_id`` (with one item); the admin default user owns
    ``foreign_id`` (with one item) and has not shared it.
    """
    caller_token = _make_user_token("preview-batch-caller")
    owned = req(
        "api/checklist",
        "post",
        b={"name": "MineWithPreview", "color_id": "yellow"},
        access_token=caller_token,
    )["id"]
    req(
        f"api/checklist/{owned}/item",
        "post",
        b={"text": "my item"},
        access_token=caller_token,
    )

    foreign = req("api/checklist", "post", b={"name": "NotYours", "color_id": "yellow"})["id"]
    req(f"api/checklist/{foreign}/item", "post", b={"text": "foreign secret"})

    return caller_token, owned, foreign


def test_mixed_batch_returns_accessible_ids_and_omits_the_rest():
    caller_token, owned, foreign = _setup_owned_and_foreign()

    preview = _preview([owned, foreign], access_token=caller_token)

    assert owned in preview, preview
    assert foreign not in preview, "an inaccessible checklist leaked into the preview batch"
    assert [i["text"] for i in preview[owned]["items"]] == ["my item"]
    assert preview[owned]["item_count"] == 1


def test_batch_of_only_inaccessible_ids_returns_an_empty_body_not_the_whole_board():
    caller_token, owned, foreign = _setup_owned_and_foreign()

    # Regression guard for the obvious way to get this wrong: filtering the id
    # list down to empty and then falling back to "no ids given → return every
    # accessible checklist", which would hand back the caller's whole board for a
    # request that asked only about someone else's card.
    preview = _preview([foreign], access_token=caller_token)

    assert preview == {}, preview
    assert owned not in preview


def test_deleted_checklist_id_does_not_kill_its_batch():
    """The real-world trigger: a card in the batch is deleted mid-flight."""
    caller_token, owned, _ = _setup_owned_and_foreign()
    doomed = req(
        "api/checklist",
        "post",
        b={"name": "AboutToGo", "color_id": "yellow"},
        access_token=caller_token,
    )["id"]
    req(f"api/checklist/{doomed}", "delete", access_token=caller_token)

    preview = _preview([owned, doomed], access_token=caller_token)

    assert owned in preview, "a deleted card in the batch must not blank its siblings"
    assert doomed not in preview


def test_single_checklist_route_still_403s_for_a_foreign_card():
    """The IDOR-relevant surface keeps its hard 403 — only the batch is lenient."""
    caller_token, _, foreign = _setup_owned_and_foreign()

    req(
        f"api/checklist/{foreign}/item",
        access_token=caller_token,
        expected_http_code=403,
    )
