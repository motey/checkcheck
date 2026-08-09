"""Live-sync (SSE) fan-out for label-level changes.

Renaming or deleting a label changes the chips on every card the user attached it
to, but leaves those cards' own rows untouched — so nothing at the card level
announced it and the label routes emitted no notification at all. A second open
tab therefore kept the stale chip until a reload: the delta feed carries the
label change (and its tombstone), but a local-first client only ever pulls that
feed in response to a poke.

The E2E label-chip spec appeared to cover this and did not: it only ever passed
because other tests running in parallel poked the same account often enough to
trigger an unrelated pull. See docs/plans/E2E_STABILITY.md.

Labels are a per-user layer, so these notifications must reach the label's owner
and nobody else — a collaborator on the same card can neither see nor care about
our chips.
"""

from typing import Dict

from tests_sharing_sync import _SSECollector

from utils import (
    req,
    authorize_for_access_token,
    create_test_user,
    get_access_token,
)


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _user_id(token: str) -> str:
    return req("api/user/me", access_token=token)["id"]


def _create_labeled_card(name: str, label_name: str) -> Dict[str, str]:
    checklist_id = req("api/checklist", "post", b={"name": name, "color_id": "yellow"})["id"]
    label_id = req("api/label", "post", b={"display_name": label_name})["id"]
    req(f"api/checklist/{checklist_id}/label/{label_id}", "put")
    return {"checklist_id": checklist_id, "label_id": label_id}


def test_deleting_a_label_pokes_the_owners_other_tabs():
    ids = _create_labeled_card("LabelDeleteSync", "sync-del")

    with _SSECollector(get_access_token()) as owner:
        req(f"api/label/{ids['label_id']}", "delete")

        assert owner.received(
            cl_id=ids["checklist_id"], upd_prop="checklist_label"
        ), "the card carrying the deleted label was not announced"
        # The poke is what a local-first client acts on: it is the only trigger
        # for the GET /api/changes pull that carries the label tombstone.
        assert owner.wait_for(
            lambda e: e.get("upd_prop") == "changes_available"
            and e.get("cl_id") == ids["checklist_id"]
        ), "no changes_available poke followed the label delete"


def test_renaming_a_label_pokes_the_owners_other_tabs():
    ids = _create_labeled_card("LabelRenameSync", "sync-rename")

    with _SSECollector(get_access_token()) as owner:
        req(
            f"api/label/{ids['label_id']}",
            "patch",
            b={"display_name": "sync-renamed"},
        )

        assert owner.received(
            cl_id=ids["checklist_id"], upd_prop="checklist_label"
        ), "the card carrying the renamed label was not announced"


def test_label_change_is_not_fanned_out_to_a_collaborator():
    """Labels are per-user: a collaborator must not be told about our chips."""
    collaborator_token = _make_user_token("label-sync-collab")
    collaborator_id = _user_id(collaborator_token)
    ids = _create_labeled_card("LabelScopeSync", "sync-scope")
    req(
        f"api/checklist/{ids['checklist_id']}/shares/{collaborator_id}",
        "put",
        b={"permission": "edit"},
    )

    with _SSECollector(collaborator_token) as collaborator:
        # Flush the setup's own traffic first. The SQLite transport resolves
        # recipients lazily when its drain loop reaches the row (up to a second
        # later), so the label *attach* from the setup above can still be sitting
        # in the queue and would land here as a `checklist_label` that has nothing
        # to do with the delete below. Rename the card, wait for that event, then
        # start from a clean slate.
        req(f"api/checklist/{ids['checklist_id']}", "patch", b={"name": "LabelScopeSynced"})
        assert collaborator.received(
            cl_id=ids["checklist_id"], upd_prop="checklist"
        ), "the collaborator is not receiving this card's events at all"
        collaborator.events.clear()

        req(f"api/label/{ids['label_id']}", "delete")

        assert not collaborator.received(
            cl_id=ids["checklist_id"], upd_prop="checklist_label", timeout=3.0
        ), "the owner's label change leaked to a collaborator"


def test_deleting_an_unattached_label_pokes_nobody():
    """No cards carry it, so there is nothing to announce."""
    label_id = req("api/label", "post", b={"display_name": "sync-orphan"})["id"]

    with _SSECollector(get_access_token()) as owner:
        req(f"api/label/{label_id}", "delete")

        assert not owner.wait_for(
            lambda e: e.get("upd_prop") == "checklist_label", timeout=3.0
        ), "a label attached to no card should announce nothing"
