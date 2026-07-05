"""Integration tests for GET /api/checklist/counts — the aggregate card counts
that feed the sidebar badges (Home / Shared with me / Shared by me / Archive /
per label).

Semantics under test:
  * every count is access-scoped to the caller;
  * all counts EXCEPT ``archived`` exclude archived cards;
  * ``labels`` maps each of the caller's label ids to its non-archived card
    count, with per-user label scoping (a collaborator's labels never leak).

Each test uses a freshly created, dedicated user so the counts start from zero
and stay deterministic (the session DB accumulates the admin's cards across the
whole suite).
"""

from typing import Dict

from utils import (
    req,
    authorize_for_access_token,
    create_test_user,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _user_id(token: str) -> str:
    return req("api/user/me", access_token=token)["id"]


def _create_checklist(name: str, access_token: str) -> str:
    return req(
        "api/checklist", "post", b={"name": name, "color_id": "yellow"},
        access_token=access_token,
    )["id"]


def _archive(checklist_id: str, access_token: str, state: bool = True) -> None:
    req(
        f"api/checklist/{checklist_id}/position",
        "patch",
        b={"archived": state},
        access_token=access_token,
    )


def _create_label(display_name: str, access_token: str) -> str:
    return req(
        "api/label", "post", b={"display_name": display_name},
        access_token=access_token,
    )["id"]


def _assign_label(checklist_id: str, label_id: str, access_token: str) -> None:
    req(
        f"api/checklist/{checklist_id}/label/{label_id}", "put",
        access_token=access_token,
    )


def _share(
    checklist_id: str, user_id: str, access_token: str, permission: str = "edit"
) -> Dict:
    return req(
        f"api/checklist/{checklist_id}/shares/{user_id}",
        "put",
        b={"permission": permission},
        access_token=access_token,
    )


def _counts(access_token: str) -> Dict:
    return req("api/checklist/counts", access_token=access_token)


# ── home / archived ──────────────────────────────────────────────────────────


def test_counts_start_at_zero_for_fresh_user():
    token = _make_user_token("counts-empty")
    c = _counts(token)
    assert c == {
        "home": 0,
        "shared_with_me": 0,
        "shared_by_me": 0,
        "archived": 0,
        "labels": {},
    }


def test_home_counts_non_archived_and_archive_moves_the_count():
    token = _make_user_token("counts-home")
    a = _create_checklist("c-home-a", token)
    _create_checklist("c-home-b", token)
    _create_checklist("c-home-c", token)

    assert _counts(token)["home"] == 3
    assert _counts(token)["archived"] == 0

    _archive(a, token, True)
    c = _counts(token)
    assert c["home"] == 2, "archived card must drop out of home"
    assert c["archived"] == 1

    # Un-archiving moves it back.
    _archive(a, token, False)
    c = _counts(token)
    assert c["home"] == 3
    assert c["archived"] == 0


# ── labels ───────────────────────────────────────────────────────────────────


def test_label_counts_group_and_exclude_archived():
    token = _make_user_token("counts-label")
    label = _create_label("CountLabel", token)
    a = _create_checklist("c-label-a", token)
    b = _create_checklist("c-label-b", token)
    archived = _create_checklist("c-label-arch", token)

    _assign_label(a, label, token)
    _assign_label(b, label, token)
    assert _counts(token)["labels"].get(label) == 2

    # Assigning the label to an archived card must NOT bump the count.
    _assign_label(archived, label, token)
    _archive(archived, token, True)
    assert _counts(token)["labels"].get(label) == 2

    # A label with no non-archived cards is omitted entirely (not reported 0).
    empty_label = _create_label("EmptyLabel", token)
    assert empty_label not in _counts(token)["labels"]


# ── sharing ──────────────────────────────────────────────────────────────────


def test_shared_counts_are_access_scoped():
    owner = _make_user_token("counts-owner")
    collab = _make_user_token("counts-collab")
    collab_id = _user_id(collab)

    shared = _create_checklist("c-shared", owner)
    _create_checklist("c-owner-only", owner)
    _share(shared, collab_id, owner)

    owner_counts = _counts(owner)
    assert owner_counts["shared_by_me"] == 1
    assert owner_counts["shared_with_me"] == 0
    assert owner_counts["home"] == 2  # both owned cards show on the owner's home

    collab_counts = _counts(collab)
    assert collab_counts["shared_with_me"] == 1
    assert collab_counts["shared_by_me"] == 0
    # The collaborator sees only the one card shared with them, not the other.
    assert collab_counts["home"] == 1
