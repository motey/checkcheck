"""Card actions that create a new card from an existing one: duplicate a card,
and move its checked items to a new card. The copy itself lives in
``api/checklist_copy.py``; these routes own authorization, the idempotent replay
check and the sync pokes."""

from typing import Optional

from fastapi import APIRouter, Body, Depends, Security

from checkcheckserver.db.user import User
from checkcheckserver.api.auth.security import get_current_user
from checkcheckserver.api.access import (
    require_checklist_permission,
    ChecklistAccessLevel,
    UserChecklistAccess,
)
from checkcheckserver.api.checklist_copy import (
    DUPLICATE_NAME_SUFFIX,
    MOVE_CHECKED_NAME_SUFFIX,
    copy_checklist,
    find_replayed_copy,
    load_copy_response,
)
from checkcheckserver.db.checklist import CheckListCRUD
from checkcheckserver.db.checklist_position import CheckListPositionCRUD
from checkcheckserver.db.checklist_label import ChecklistLabelCRUD
from checkcheckserver.db.checklist_item import CheckListItemCRUD
from checkcheckserver.db.sync_notification import SyncNotifiationCRUD
from checkcheckserver.model.checklist import (
    CheckListApiWithSubObj,
    CheckListCopyRequest,
)
from checkcheckserver.model.sync_notifications import SyncNotification
from checkcheckserver.log import get_logger

log = get_logger()

fast_api_checklist_copy_router: APIRouter = APIRouter()


@fast_api_checklist_copy_router.post(
    "/checklist/{checklist_id}/duplicate",
    response_model=CheckListApiWithSubObj,
    description=(
        "Create a copy of a checklist owned by the caller: the name gets a "
        f"'{DUPLICATE_NAME_SUFFIX}' suffix, the note, color, view settings and the "
        "caller's own labels are copied, and so are all items, in the same order "
        "and all unchecked. Shares, public links and reminders are not copied. "
        "Requires 'view' on the source. Idempotent when the body carries a client "
        "`id` for the new card (a replay returns the existing copy)."
    ),
)
async def duplicate_checklist(
    copy_request: Optional[CheckListCopyRequest] = Body(default=None),
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.view)
    ),
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    checklist_position_crud: CheckListPositionCRUD = Depends(
        CheckListPositionCRUD.get_crud
    ),
    checklist_label_crud: ChecklistLabelCRUD = Depends(ChecklistLabelCRUD.get_crud),
    checklist_item_crud: CheckListItemCRUD = Depends(CheckListItemCRUD.get_crud),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> CheckListApiWithSubObj:
    copy_request = copy_request or CheckListCopyRequest()
    replayed = await find_replayed_copy(copy_request, current_user.id, checklist_crud)
    if replayed is not None:
        return await load_copy_response(
            replayed.id,
            current_user.id,
            checklist_crud,
            checklist_position_crud,
            checklist_label_crud,
        )
    source = checklist_access.checklist
    source_items = await checklist_item_crud.list(checklist_id=source.id)
    card = await copy_checklist(
        source=source,
        request=copy_request,
        user_id=current_user.id,
        name_suffix=DUPLICATE_NAME_SUFFIX,
        source_items=source_items,
        keep_checked_state=False,
        tombstone_source_items=False,
        checklist_crud=checklist_crud,
        checklist_position_crud=checklist_position_crud,
        checklist_label_crud=checklist_label_crud,
        checklist_item_crud=checklist_item_crud,
    )
    # Resolves to the new card's owner only (it has no collaborators), i.e. the
    # caller's other devices; their delta pull ships the whole new card.
    await sync_crud.create(
        SyncNotification(cl_id=card.id, upd_prop="checklist_created")
    )
    return card


@fast_api_checklist_copy_router.post(
    "/checklist/{checklist_id}/items/move-checked",
    response_model=CheckListApiWithSubObj,
    description=(
        "Move every currently checked item of a checklist into a new checklist "
        "owned by the caller. The new card's name gets a "
        f"'{MOVE_CHECKED_NAME_SUFFIX}' suffix and it gets the note, color, view "
        "settings and the caller's own labels of the source; the items keep their "
        "order and checked state and are removed (tombstoned) from the source. "
        "Shares, public links and reminders are not copied. Requires 'edit' on the "
        "source (the same as deleting checked items). Idempotent when the body "
        "carries a client `id` for the new card: a replay returns the existing "
        "card and moves nothing again."
    ),
)
async def move_checked_items(
    copy_request: Optional[CheckListCopyRequest] = Body(default=None),
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.edit)
    ),
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    checklist_position_crud: CheckListPositionCRUD = Depends(
        CheckListPositionCRUD.get_crud
    ),
    checklist_label_crud: ChecklistLabelCRUD = Depends(ChecklistLabelCRUD.get_crud),
    checklist_item_crud: CheckListItemCRUD = Depends(CheckListItemCRUD.get_crud),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> CheckListApiWithSubObj:
    copy_request = copy_request or CheckListCopyRequest()
    replayed = await find_replayed_copy(copy_request, current_user.id, checklist_crud)
    if replayed is not None:
        return await load_copy_response(
            replayed.id,
            current_user.id,
            checklist_crud,
            checklist_position_crud,
            checklist_label_crud,
        )
    source = checklist_access.checklist
    checked_items = await checklist_item_crud.list(
        checklist_id=source.id, checked=True
    )
    card = await copy_checklist(
        source=source,
        request=copy_request,
        user_id=current_user.id,
        name_suffix=MOVE_CHECKED_NAME_SUFFIX,
        source_items=checked_items,
        keep_checked_state=True,
        tombstone_source_items=True,
        checklist_crud=checklist_crud,
        checklist_position_crud=checklist_position_crud,
        checklist_label_crud=checklist_label_crud,
        checklist_item_crud=checklist_item_crud,
    )
    await sync_crud.create(
        SyncNotification(cl_id=card.id, upd_prop="checklist_created")
    )
    if checked_items:
        # Everyone on the source card: one card-level poke for the whole batch
        # (`cli_id=None`, like "delete checked items"); the delta feed carries the
        # item tombstones.
        await sync_crud.create(
            SyncNotification(cl_id=source.id, cli_id=None, upd_prop="item_deleted")
        )
    return card
