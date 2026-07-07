from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, Query

from checkcheckserver.db.user import User
from checkcheckserver.api.auth.security import get_current_user
from checkcheckserver.api.access import (
    attach_my_permission,
    ChecklistAccessLevel,
)
from checkcheckserver.db.checklist import CheckListCRUD
from checkcheckserver.db.checklist_item import CheckListItemCRUD
from checkcheckserver.db.checklist_label import ChecklistLabelCRUD
from checkcheckserver.db.checklist_collaborator import CheckListCollaboratorCRUD
from checkcheckserver.db.checklist_position import CheckListPositionCRUD
from checkcheckserver.db.label import LabelCRUD
from checkcheckserver.db.sync_seq import get_current_server_seq
from checkcheckserver.model.changes import ChangesResponse
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

config = Config()
log = get_logger()


fast_api_changes_router: APIRouter = APIRouter()


def _parse_known_ids(known: Optional[str]) -> List[uuid.UUID]:
    """Parse the caller's comma-separated ``known`` checklist ids (the cards it
    currently has cached). Malformed entries are skipped rather than 400-ing the
    whole pull — a client should never be locked out of syncing by one bad id."""
    if not known:
        return []
    parsed: List[uuid.UUID] = []
    for raw in known.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed.append(uuid.UUID(raw))
        except ValueError:
            log.warning("changes: skipping unparseable known id %r", raw)
    return parsed


@fast_api_changes_router.get(
    "/changes",
    response_model=ChangesResponse,
    description=(
        "Delta feed (2.0 sync). Returns everything visible to the caller that "
        "changed since their cursor.\n\n"
        "**Cursor** — pass the previous response's `next_cursor` as `since` (start "
        "at `0` for a fresh device). The cursor is a global, server-set, strictly "
        "monotonic `server_seq` stamped on every syncable write; it is client-"
        "owned and per-device (the server keeps no per-client state). A `since` "
        "greater than the server's high-water mark (client ahead of a reset/"
        "restored DB) returns `full_resync=true` with the full accessible state.\n\n"
        "**Access changes** — cards the caller just gained access to are shipped "
        "in full (card + all items), since their rows predate the grant. Cards the "
        "caller lost access to are returned in `removed_checklist_ids`; to compute "
        "that, pass the ids the client currently caches as a comma-separated "
        "`known` query param."
    ),
)
async def get_changes(
    since: int = Query(
        0,
        description="The caller's sync cursor (a server_seq). 0 = full bootstrap.",
    ),
    known: Optional[str] = Query(
        None,
        description=(
            "Comma-separated checklist ids the client currently has cached. Used "
            "to compute removed_checklist_ids (access revocations). Omit on the "
            "first pull."
        ),
    ),
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    checklist_item_crud: CheckListItemCRUD = Depends(CheckListItemCRUD.get_crud),
    checklist_label_crud: ChecklistLabelCRUD = Depends(ChecklistLabelCRUD.get_crud),
    checklist_collaborator_crud: CheckListCollaboratorCRUD = Depends(
        CheckListCollaboratorCRUD.get_crud
    ),
    checklist_position_crud: CheckListPositionCRUD = Depends(
        CheckListPositionCRUD.get_crud
    ),
    label_crud: LabelCRUD = Depends(LabelCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> ChangesResponse:
    user_id = current_user.id

    # Read the high-water mark FIRST so next_cursor can never sit above a row this
    # pull misses (at worst a mid-pull commit is re-delivered next time).
    current_seq = await get_current_server_seq(checklist_crud.session)

    full_resync = False
    if since < 0 or since > current_seq:
        # Client ahead of the server (DB reset/restore) or a nonsense cursor:
        # rebuild from scratch.
        full_resync = True
        since = 0

    # Cards the caller can currently see (owner + accepted collaborator).
    accessible_ids = set(await checklist_crud.list_access_ids(user_id=user_id))

    # Access just (re)granted since the cursor → ship the whole tree for these.
    # Keyed off the caller's position-row creation (granted_seq), which every grant
    # path stamps — including ownership transfer to a non-collaborator, which the
    # old accepted-collaborator-seq signal missed (review finding 1).
    gain_ids = set(
        await checklist_position_crud.list_gained_access_checklist_ids(
            user_id=user_id, since=since
        )
    ) & accessible_ids

    # Cards whose card-level state (row / this user's position / labels /
    # collaborator permission) changed. Union with gain_ids for the card-level
    # payload.
    changed_card_ids = set(
        await checklist_crud.list_changed_checklist_ids_for_user(
            user_id=user_id, since=since
        )
    )
    card_ids_to_emit = changed_card_ids | gain_ids

    checklists = await checklist_crud.list_full_by_ids_for_user(
        checklist_ids=list(card_ids_to_emit), user_id=user_id
    )
    # Per-user labels + effective permission, batched like the grid route. The
    # eager-loaded (unscoped) labels are replaced with the caller's own set.
    labels_by_checklist = (
        await checklist_label_crud.list_labels_for_user_by_checklist(
            checklist_ids=list(card_ids_to_emit), user_id=user_id
        )
    )
    collaborator_permissions = (
        await checklist_collaborator_crud.permissions_for_user_by_checklist(
            checklist_ids=list(card_ids_to_emit), user_id=user_id
        )
    )
    for checklist in checklists:
        checklist.labels = labels_by_checklist.get(checklist.id, [])
        if checklist.owner_id == user_id:
            attach_my_permission(checklist, ChecklistAccessLevel.owner)
        else:
            attach_my_permission(
                checklist,
                collaborator_permissions.get(checklist.id, ChecklistAccessLevel.view),
            )

    # Items: changed items for accessible cards the caller already had, plus the
    # full tree for gained-access cards. Item changes surface independently of the
    # card row, so scan every accessible (non-gain) card, not only changed cards.
    items = await checklist_item_crud.list_changed_items(
        since=since,
        changed_checklist_ids=list(accessible_ids - gain_ids),
        full_checklist_ids=list(gain_ids),
    )

    # Tombstones.
    checklist_tombstones = await checklist_crud.list_tombstoned_checklist_ids_for_user(
        user_id=user_id, since=since
    )
    item_tombstones = await checklist_item_crud.list_tombstoned_item_ids(
        checklist_ids=list(accessible_ids), since=since
    )
    label_tombstones = await label_crud.list_tombstoned_ids(
        user_id=user_id, since=since
    )
    labels = await label_crud.list_changed(user_id=user_id, since=since)

    # Access revocations: ids the client caches that it can no longer see and that
    # are not already reported as tombstoned.
    tombstone_set = set(checklist_tombstones)
    removed_checklist_ids = [
        kid
        for kid in _parse_known_ids(known)
        if kid not in accessible_ids and kid not in tombstone_set
    ]

    return ChangesResponse(
        next_cursor=current_seq,
        full_resync=full_resync,
        checklists=checklists,
        items=items,
        labels=labels,
        checklist_tombstones=checklist_tombstones,
        item_tombstones=item_tombstones,
        label_tombstones=label_tombstones,
        removed_checklist_ids=removed_checklist_ids,
    )
