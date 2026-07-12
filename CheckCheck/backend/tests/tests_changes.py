"""WI-4 — delta feed: ``GET /api/changes?since=<cursor>``.

One endpoint that tells a device everything visible to it that changed since its
cursor. The cursor is a global, server-set, strictly-monotonic ``server_seq``
stamped on every syncable write; a client stores the ``next_cursor`` it gets back
and passes it as ``since`` next time.

These drive the real REST routes so the whole spine is exercised together:
server_seq stamping (model + mapper events), the per-entity change/tombstone
queries, access-gain (share delivers the whole tree) and access-loss
(``removed_checklist_ids``), and the ``full_resync`` fallback. Two simulated
devices converging through the endpoint is the core property under test.
"""

from typing import Dict, List, Optional

from utils import (
    req,
    authorize_for_access_token,
    create_test_user,
    find_first_dict_in_list,
)


def _make_user_token(user_name: str) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_access_token(user_name, pw)


def _user_id(token: str) -> str:
    return req("api/user/me", access_token=token)["id"]


def _changes(
    since: int = 0,
    known: Optional[List[str]] = None,
    token: Optional[str] = None,
) -> Dict:
    q: Dict = {"since": since}
    if known is not None:
        # req() joins a list value with commas — exactly the format the endpoint's
        # `known` param parses.
        q["known"] = known
    return req("api/changes", q=q, access_token=token)


def _cursor(token: Optional[str] = None) -> int:
    """Current server high-water mark, as the caller would see it."""
    return _changes(since=0, token=token)["next_cursor"]


def _cl_ids(delta: Dict) -> List[str]:
    return [c["id"] for c in delta["checklists"]]


def _item_ids(delta: Dict) -> List[str]:
    return [i["id"] for i in delta["items"]]


# ── ordinary changes ──────────────────────────────────────────────────────────


def test_new_checklist_and_item_appear_then_cursor_converges():
    start = _cursor()

    cl_id = req("api/checklist", "post", b={"name": "delta card"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "hello"})["id"]

    delta = _changes(since=start)
    assert cl_id in _cl_ids(delta), "new checklist must appear in the delta"
    assert item_id in _item_ids(delta), "new item must appear in the delta"

    # Advancing to the returned cursor and re-pulling yields none of our rows —
    # the device has converged.
    converged = _changes(since=delta["next_cursor"])
    assert cl_id not in _cl_ids(converged)
    assert item_id not in _item_ids(converged)

    req(f"api/checklist/{cl_id}", "delete")


def test_item_state_change_surfaces_the_item():
    cl_id = req("api/checklist", "post", b={"name": "state host"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "check me"})["id"]

    start = _cursor()
    req(f"api/checklist/{cl_id}/item/{item_id}/state", "patch", b={"checked": True})

    delta = _changes(since=start)
    item = find_first_dict_in_list(delta["items"], {"id": item_id})
    assert item["state"]["checked"] is True, "checked-state change must surface"

    req(f"api/checklist/{cl_id}", "delete")


def test_item_position_change_surfaces_the_item():
    cl_id = req("api/checklist", "post", b={"name": "pos host"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "movable"})["id"]

    start = _cursor()
    req(f"api/checklist/{cl_id}/item/{item_id}/position", "patch", b={"index": 7.25})

    delta = _changes(since=start)
    item = find_first_dict_in_list(delta["items"], {"id": item_id})
    assert item["position"]["index"] == 7.25

    req(f"api/checklist/{cl_id}", "delete")


def test_checklist_rename_surfaces_card_not_stale():
    cl_id = req("api/checklist", "post", b={"name": "before"})["id"]

    start = _cursor()
    req(f"api/checklist/{cl_id}", "patch", b={"name": "after"})

    delta = _changes(since=start)
    card = find_first_dict_in_list(delta["checklists"], {"id": cl_id})
    assert card["name"] == "after"
    assert card["my_permission"] == "owner", "owner must be reported for own card"

    req(f"api/checklist/{cl_id}", "delete")


# ── tombstones ────────────────────────────────────────────────────────────────


def test_item_and_checklist_tombstones_are_reported():
    cl_id = req("api/checklist", "post", b={"name": "doomed"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "x"})["id"]

    start = _cursor()

    # Delete the item first and confirm its tombstone reaches the delta while the
    # card is still accessible.
    req(f"api/checklist/{cl_id}/item/{item_id}", "delete")
    delta = _changes(since=start)
    assert item_id in delta["item_tombstones"], "deleted item must be tombstoned"

    # Now delete the card: its tombstone is reported (and it drops out of the
    # access set, so the item tombstone is subsumed by the card tombstone).
    req(f"api/checklist/{cl_id}", "delete")
    delta2 = _changes(since=start)
    assert cl_id in delta2["checklist_tombstones"], "deleted card must be tombstoned"
    assert cl_id not in _cl_ids(delta2), "tombstoned card must not be a live change"


def test_label_attach_and_detach_surface_the_card():
    """Attaching/detaching a label is a card-level change (§3 grouping rules).
    Detach is the tricky half: the link row is HARD-deleted, so there is no live
    row carrying a fresh server_seq — without an explicit signal an offline
    device would keep the stale chip forever."""
    cl_id = req("api/checklist", "post", b={"name": "labeled card"})["id"]
    label_id = req("api/label", "post", b={"display_name": "chip", "sort_order": 10})["id"]

    # Attach: the card must be re-emitted with the label present.
    mid = _cursor()
    req(f"api/checklist/{cl_id}/label/{label_id}", "put")
    delta = _changes(since=mid)
    card = find_first_dict_in_list(delta["checklists"], {"id": cl_id})
    assert card is not None, "label attach must re-emit the card"
    assert label_id in [l["id"] for l in card["labels"]]

    # Detach: the card must be re-emitted again, now WITHOUT the label.
    mid2 = delta["next_cursor"]
    req(f"api/checklist/{cl_id}/label/{label_id}", "delete")
    delta2 = _changes(since=mid2)
    card2 = find_first_dict_in_list(delta2["checklists"], {"id": cl_id})
    assert card2 is not None, "label detach must re-emit the card"
    assert label_id not in [l["id"] for l in card2["labels"]]

    req(f"api/checklist/{cl_id}", "delete")


def test_label_change_and_tombstone_are_reported():
    start = _cursor()
    label_id = req("api/label", "post", b={"display_name": "delta-label"})["id"]

    delta = _changes(since=start)
    assert label_id in [l["id"] for l in delta["labels"]], "new label must appear"

    mid = delta["next_cursor"]
    req(f"api/label/{label_id}", "delete")
    delta2 = _changes(since=mid)
    assert label_id in delta2["label_tombstones"], "deleted label must be tombstoned"
    assert label_id not in [l["id"] for l in delta2["labels"]]


# ── access gained / lost ──────────────────────────────────────────────────────


def test_gaining_access_delivers_the_whole_tree():
    other = _make_user_token("wi4-gain")
    other_id = _user_id(other)

    # Admin owns a card with an item, both created BEFORE the share — so their
    # server_seq predates the collaborator grant.
    cl_id = req("api/checklist", "post", b={"name": "shared tree"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "predates grant"})[
        "id"
    ]

    base = _cursor(token=other)  # other's cursor before any access
    assert cl_id not in _cl_ids(_changes(since=base, token=other))

    # Share to `other` (instant-accepted with the invite flag off).
    req(f"api/checklist/{cl_id}/shares/{other_id}", "put", b={"permission": "edit"})

    delta = _changes(since=base, token=other)
    assert cl_id in _cl_ids(delta), "gained card must be delivered"
    # The item predates the grant (lower seq) but must still arrive in full.
    assert item_id in _item_ids(delta), "children of a gained card must be delivered"
    card = find_first_dict_in_list(delta["checklists"], {"id": cl_id})
    assert card["my_permission"] == "edit"

    req(f"api/checklist/{cl_id}", "delete")


def test_ownership_transfer_delivers_the_whole_tree_to_new_owner():
    """Transferring ownership to a NON-collaborator must ship that user the full
    card tree (review finding 1). The new owner never had a collaborator row, so
    the old accepted-collaborator-seq gain signal missed the items; gain is now
    keyed off the position row every grant path creates, so the tree arrives."""
    other = _make_user_token("wi4-transfer")
    other_id = _user_id(other)

    # Admin owns a card + item, both created before `other` is involved at all —
    # `other` is not (and never was) a collaborator.
    cl_id = req("api/checklist", "post", b={"name": "handover card"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "predates transfer"})[
        "id"
    ]

    base = _cursor(token=other)  # other's cursor before gaining anything
    assert cl_id not in _cl_ids(_changes(since=base, token=other))

    req(
        f"api/checklist/{cl_id}/transfer-ownership",
        "post",
        b={"new_owner_id": other_id},
    )

    delta = _changes(since=base, token=other)
    assert cl_id in _cl_ids(delta), "new owner must receive the card"
    assert item_id in _item_ids(delta), "children of the transferred card must arrive"
    card = find_first_dict_in_list(delta["checklists"], {"id": cl_id})
    assert card["my_permission"] == "owner", "new owner must be reported as owner"

    # Clean up as the new owner (admin no longer owns it).
    req(f"api/checklist/{cl_id}", "delete", access_token=other)


def test_permission_level_change_re_emits_card_without_re_shipping_tree():
    """A permission bump on an already-accepted collaborator re-emits the card so
    ``my_permission`` updates, but does NOT re-ship the tree (the collaborator
    already has it). Guards the card-level collaborator-change signal that
    replaced the old whole-tree collaborator gain."""
    other = _make_user_token("wi4-levelup")
    other_id = _user_id(other)

    cl_id = req("api/checklist", "post", b={"name": "leveled card"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "already synced"})[
        "id"
    ]
    req(f"api/checklist/{cl_id}/shares/{other_id}", "put", b={"permission": "view"})

    # `other` has fully synced the card at this cursor.
    synced = _cursor(token=other)

    # Owner raises the level view -> edit.
    req(f"api/checklist/{cl_id}/shares/{other_id}", "put", b={"permission": "edit"})

    delta = _changes(since=synced, token=other)
    card = find_first_dict_in_list(delta["checklists"], {"id": cl_id})
    assert card is not None, "a permission change must re-emit the card"
    assert card["my_permission"] == "edit", "the new level must be reported"
    # The item did not change and predates `synced`, so it is not re-shipped.
    assert item_id not in _item_ids(delta), "unchanged tree must not be re-shipped"

    req(f"api/checklist/{cl_id}", "delete")


def test_losing_access_is_reported_in_removed_ids():
    other = _make_user_token("wi4-lose")
    other_id = _user_id(other)

    cl_id = req("api/checklist", "post", b={"name": "revoke me"})["id"]
    req(f"api/checklist/{cl_id}/shares/{other_id}", "put", b={"permission": "edit"})

    # `other` has the card cached now; capture their cursor.
    after_share = _cursor(token=other)
    assert cl_id in _cl_ids(_changes(since=0, token=other))

    # Admin revokes the share.
    req(f"api/checklist/{cl_id}/shares/{other_id}", "delete")

    # `other` reconnects and tells the server which cards it still caches.
    delta = _changes(since=after_share, known=[cl_id], token=other)
    assert cl_id in delta["removed_checklist_ids"], "revoked card must be reported"

    req(f"api/checklist/{cl_id}", "delete")


def test_revocation_advances_the_global_seq_so_the_poke_is_not_skipped():
    """A revoked local-first client only pulls `/api/changes` when the
    `changes_available` poke carries a server_seq AHEAD of its cursor (the §9b
    poke-skip). Revocation HARD-deletes the collaborator + position rows, which
    leaves no server_seq trace, so the route must advance the global seq itself
    (re-stamping the owner's position). Without that, the poke sits AT the removed
    user's cursor, the client skips the pull, and the card lingers until a manual
    reload — the WI-15 flag-on regression this guards against."""
    other = _make_user_token("wi15-revoke-seq")
    other_id = _user_id(other)

    cl_id = req("api/checklist", "post", b={"name": "revoke seq"})["id"]
    req(f"api/checklist/{cl_id}/shares/{other_id}", "put", b={"permission": "edit"})

    # The cursor `other` holds right after the share — the poke must beat this.
    after_share = _cursor(token=other)

    req(f"api/checklist/{cl_id}/shares/{other_id}", "delete")

    # The poke's server_seq is the global high-water mark; it must now be ahead.
    assert _cursor() > after_share, (
        "revocation must advance the global server_seq so the poke is not skipped"
    )

    req(f"api/checklist/{cl_id}", "delete")


# ── full resync ───────────────────────────────────────────────────────────────


def test_cursor_ahead_of_server_triggers_full_resync():
    cl_id = req("api/checklist", "post", b={"name": "resync card"})["id"]
    high_water = _cursor()

    delta = _changes(since=high_water + 10_000_000)
    assert delta["full_resync"] is True, "a cursor past the server must full-resync"
    # A full resync is computed as if since=0, so it returns the caller's whole
    # accessible state.
    assert cl_id in _cl_ids(delta)
    assert delta["next_cursor"] >= high_water

    req(f"api/checklist/{cl_id}", "delete")


def test_bootstrap_from_zero_returns_current_state():
    cl_id = req("api/checklist", "post", b={"name": "bootstrap card"})["id"]
    delta = _changes(since=0)
    assert delta["full_resync"] is False
    assert cl_id in _cl_ids(delta), "since=0 bootstrap must include current cards"
    req(f"api/checklist/{cl_id}", "delete")


# ── two-device convergence ────────────────────────────────────────────────────


def test_two_devices_converge_through_the_feed():
    """A card edited across several ops on 'device A' is applied by 'device B'
    walking its cursor forward; a final pull is empty (converged)."""
    cl_id = req("api/checklist", "post", b={"name": "conv"})["id"]

    cursor = _cursor()  # device B starts here

    # Device A makes a burst of edits.
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "one"})["id"]
    req(f"api/checklist/{cl_id}", "patch", b={"name": "conv-renamed"})
    req(f"api/checklist/{cl_id}/item/{item_id}", "patch", b={"text": "one-edited"})
    req(f"api/checklist/{cl_id}/item/{item_id}/state", "patch", b={"checked": True})

    # Device B pulls once and sees the converged end-state (LWW: latest values).
    delta = _changes(since=cursor)
    card = find_first_dict_in_list(delta["checklists"], {"id": cl_id})
    assert card["name"] == "conv-renamed"
    item = find_first_dict_in_list(delta["items"], {"id": item_id})
    assert item["text"] == "one-edited"
    assert item["state"]["checked"] is True

    # Re-pulling at the new cursor is empty for our rows — B has converged with A.
    converged = _changes(since=delta["next_cursor"])
    assert cl_id not in _cl_ids(converged)
    assert item_id not in _item_ids(converged)

    req(f"api/checklist/{cl_id}", "delete")


# ── Chunk C: cursor edges & revocation corner cases ───────────────────────────
# These pin behaviours the surrounding tests assert only by construction (the
# revocation, boundary, and `known`-parsing branches in routes_changes.py).


def test_tombstoned_known_id_is_not_also_a_revocation():
    """A card the client caches that gets HARD-removed from its access is a
    revocation (`removed_checklist_ids`); a card that gets tombstoned is a
    deletion (`checklist_tombstones`). The two are disjoint by design — the
    client handles them on different local paths — so a tombstoned id passed in
    `known` must report ONLY as a tombstone, never also as a revocation
    (routes_changes.py `kid not in tombstone_set` branch)."""
    cl_id = req("api/checklist", "post", b={"name": "tombstone-not-revoke"})["id"]

    start = _cursor()
    req(f"api/checklist/{cl_id}", "delete")

    # The client still caches the (now deleted) card, so it reports it in `known`.
    delta = _changes(since=start, known=[cl_id])
    assert cl_id in delta["checklist_tombstones"], "deleted card must be tombstoned"
    assert cl_id not in delta["removed_checklist_ids"], (
        "a tombstoned id must not ALSO be reported as a revocation"
    )


def test_unparseable_known_entries_are_skipped_valid_still_revoked():
    """Malformed `known` entries (garbage, empty) are skipped, not 400 — one bad
    id must never lock a device out of syncing. The valid revoked id in the same
    list is still reported (routes_changes.py `_parse_known_ids`)."""
    other = _make_user_token("wi4-known-garbage")
    other_id = _user_id(other)

    cl_id = req("api/checklist", "post", b={"name": "garbage-known"})["id"]
    req(f"api/checklist/{cl_id}/shares/{other_id}", "put", b={"permission": "edit"})

    after_share = _cursor(token=other)
    req(f"api/checklist/{cl_id}/shares/{other_id}", "delete")

    # known = "not-a-uuid,,<valid-revoked-uuid>" — the empty and unparseable
    # entries are dropped, the valid id still yields its revocation, HTTP 200.
    delta = _changes(
        since=after_share, known=["not-a-uuid", "", cl_id], token=other
    )
    assert cl_id in delta["removed_checklist_ids"], (
        "a valid revoked id must survive alongside skipped garbage entries"
    )

    req(f"api/checklist/{cl_id}", "delete")


def test_negative_cursor_triggers_full_resync():
    """A `since < 0` is a nonsense cursor and must rebuild from scratch, exactly
    like the 'client ahead of the server' branch (routes_changes.py:99). The
    resync returns the caller's whole accessible state."""
    cl_id = req("api/checklist", "post", b={"name": "neg-cursor"})["id"]

    delta = _changes(since=-1)
    assert delta["full_resync"] is True, "a negative cursor must full-resync"
    assert cl_id in _cl_ids(delta), "a full resync returns the whole accessible state"

    req(f"api/checklist/{cl_id}", "delete")


def test_cursor_at_high_water_is_empty_and_not_a_resync():
    """The exact boundary `since == current_seq`: the change scans are strictly
    `> since`, so pulling at the high-water mark returns none of the rows written
    at or below it and does NOT trigger a resync (guards the `>`-vs-`>=`
    off-by-one that would re-ship every card each poll)."""
    cl_id = req("api/checklist", "post", b={"name": "boundary card"})["id"]

    # With nothing else writing, the card's create is the high-water write, so
    # this cursor sits exactly at its seq.
    boundary = _cursor()

    delta = _changes(since=boundary)
    assert delta["full_resync"] is False, "a cursor AT the high-water is not a resync"
    assert cl_id not in _cl_ids(delta), (
        "the boundary write itself must be excluded (scan is strictly > since)"
    )

    req(f"api/checklist/{cl_id}", "delete")


def test_permission_downgrade_re_emits_card_then_write_403s():
    """Downgrading an accepted collaborator edit→view re-emits the card so the
    device learns its lowered `my_permission`, and a write legal at the old level
    now 403s. Covers the server half of finding A3: an offline device that
    queued an edit before the downgrade is told of the change AND its replay is
    rejected, not silently applied (the revoke case is covered; downgrade was
    not)."""
    other = _make_user_token("wi4-downgrade")
    other_id = _user_id(other)

    cl_id = req("api/checklist", "post", b={"name": "downgrade card"})["id"]
    item_id = req(f"api/checklist/{cl_id}/item", "post", b={"text": "editable"})["id"]
    req(f"api/checklist/{cl_id}/shares/{other_id}", "put", b={"permission": "edit"})

    # `other` can write at edit level.
    req(
        f"api/checklist/{cl_id}/item/{item_id}",
        "patch",
        b={"text": "edited by collaborator"},
        access_token=other,
    )
    synced = _cursor(token=other)

    # Owner downgrades edit → view.
    req(f"api/checklist/{cl_id}/shares/{other_id}", "put", b={"permission": "view"})

    # The downgrade re-emits the card with the lowered permission.
    delta = _changes(since=synced, token=other)
    card = find_first_dict_in_list(delta["checklists"], {"id": cl_id})
    assert card is not None, "a permission downgrade must re-emit the card"
    assert card["my_permission"] == "view", "the lowered level must be reported"

    # A queued-style write replayed after the downgrade is now rejected.
    req(
        f"api/checklist/{cl_id}/item/{item_id}",
        "patch",
        b={"text": "stale offline edit"},
        access_token=other,
        expected_http_code=403,
    )

    req(f"api/checklist/{cl_id}", "delete")


def test_self_leave_advances_seq_and_lands_in_removed_ids():
    """A collaborator removing their OWN access ('leave list') shares the revoke
    code path with an owner-revoke: it hard-deletes the collaborator + position
    rows (no seq trace of their own) and re-stamps the owner's position to
    advance the global seq, so the leaver's next poke sits AHEAD of its cursor
    and it actually pulls the removal. `test_revocation_advances_the_global_seq…`
    pins the owner-revoke case; the leave path shares the code but had no seq
    assertion."""
    other = _make_user_token("wi4-selfleave")
    other_id = _user_id(other)

    cl_id = req("api/checklist", "post", b={"name": "leave me"})["id"]
    req(f"api/checklist/{cl_id}/shares/{other_id}", "put", b={"permission": "edit"})

    after_share = _cursor(token=other)  # the leaver's cursor before leaving
    before_leave_seq = _cursor()  # global high-water the poke must beat

    # `other` leaves via the self-remove path.
    req(
        f"api/checklist/{cl_id}/shares/{other_id}",
        "delete",
        access_token=other,
        expected_http_code=204,
    )

    assert _cursor() > before_leave_seq, (
        "self-leave must advance the global server_seq like an owner-revoke, so "
        "the poke is not skipped"
    )

    # The leaver reconnects and is told the card is gone.
    delta = _changes(since=after_share, known=[cl_id], token=other)
    assert cl_id in delta["removed_checklist_ids"], (
        "self-leave must land the card in the leaver's removed_checklist_ids"
    )

    req(f"api/checklist/{cl_id}", "delete")
