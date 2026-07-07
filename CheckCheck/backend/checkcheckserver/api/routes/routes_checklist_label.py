from typing import Annotated, Sequence, List, Type, Optional, Dict
from datetime import datetime, timedelta, timezone
import uuid
from pydantic import PositiveInt
from fastapi import (
    Depends,
    Security,
    HTTPException,
    status,
    Query,
    Body,
    Form,
    Path,
    Response,
)
import decimal


from fastapi import Depends, APIRouter


from checkcheckserver.db.user import User

from checkcheckserver.model.checklist import (
    CheckList,
)


from checkcheckserver.model.label import (
    Label,
    LabelUpdate,
    LabelCreate,
    LabelReadAPI,
)
from checkcheckserver.model.checklist_label import CheckListLabel, CheckListLabelCreate
from checkcheckserver.db.label import LabelCRUD
from checkcheckserver.db.checklist_label import ChecklistLabelCRUD
from checkcheckserver.db.checklist_position import CheckListPositionCRUD
from checkcheckserver.db.checklist_color_scheme import (
    ChecklistColorSchemeCRUD,
    ChecklistColorScheme,
)
from checkcheckserver.config import Config
from checkcheckserver.api.auth.security import (
    user_is_admin,
    user_is_usermanager,
    get_current_user,
)

from checkcheckserver.api.access import (
    user_has_checklist_access,
    require_checklist_permission,
    ChecklistAccessLevel,
    checklist_ids_with_access,
    UserChecklistAccess,
)
from checkcheckserver.api.paginator import (
    PaginatedResponse,
    create_query_params_class,
    QueryParamsInterface,
)

config = Config()

from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist import CheckList
from checkcheckserver.db.sync_notification import SyncNotifiationCRUD
from checkcheckserver.model.sync_notifications import SyncNotification

log = get_logger()


fast_api_checklist_label_router: APIRouter = APIRouter()

CheckListItemQueryParams: Type[QueryParamsInterface] = create_query_params_class(
    CheckList, no_ordering=True
)


from pydantic import BaseModel, Field


@fast_api_checklist_label_router.get(
    "/label",
    response_model=List[LabelReadAPI],
    description=f"List all labels of the current user",
)
async def list_labels(
    label_crud: LabelCRUD = Depends(LabelCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> List[LabelReadAPI]:
    return await label_crud.list(user_id=current_user.id)


@fast_api_checklist_label_router.post(
    "/label",
    response_model=LabelReadAPI,
    description=f"Create new label for current user.",
)
async def create_label(
    label_create: LabelCreate,
    label_crud: LabelCRUD = Depends(LabelCRUD.get_crud),
    color_crud: ChecklistColorSchemeCRUD = Depends(ChecklistColorSchemeCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> LabelReadAPI:
    log.debug(("label_create", label_create))
    # Idempotent create (WI-3): the client may supply the label's UUID so an
    # outbox replay doesn't duplicate it. If a label with that id already exists,
    # don't create a second one.
    if label_create.id is not None:
        existing = await label_crud.get(label_create.id, include_deleted=True)
        if existing is not None:
            if existing.owner_id != current_user.id:
                # id taken by another user's label — a UUID collision, not a replay.
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Label id '{label_create.id}' already exists.",
                )
            if existing.deleted_at is not None:
                # Re-creating a since-tombstoned label must not resurrect it.
                raise HTTPException(
                    status_code=status.HTTP_410_GONE,
                    detail=f"Label '{label_create.id}' has been deleted.",
                )
            # Same owner, still live → replay. Return the existing label unchanged.
            return existing
    if label_create.sort_order is None:
        max_label_order = await label_crud.get_max_sort_order(user_id=current_user.id)
        label_create.sort_order = max_label_order + 10

    label = Label.model_validate(
        label_create.model_dump(exclude_unset=True) | {"owner_id": current_user.id}
    )
    log.debug(("label", label))
    if label.color_id:
        label.color = await color_crud.get(label.color_id)
    return await label_crud.create(label)


@fast_api_checklist_label_router.patch(
    "/label/{label_id}",
    response_model=LabelReadAPI,
    description=f"Update existing label. Current user must be owner.",
)
async def update_label(
    label_id: uuid.UUID,
    label_update: LabelUpdate,
    label_crud: LabelCRUD = Depends(LabelCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> LabelReadAPI:
    existing_label: Label = await label_crud.get(
        label_id,
        raise_exception_if_none=HTTPException(status_code=status.HTTP_404_NOT_FOUND),
        include_deleted=True,
    )
    if existing_label.owner_id != current_user.id:
        # 404, not 401: labels are strictly per-user, so a foreign label reads as
        # "does not exist" (no existence leak — matches add_label_to_checklist).
        # 401 would additionally trip the client's global session-expiry handling.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if existing_label.deleted_at is not None:
        # Stale edit of a tombstoned label must not resurrect it (WI-2).
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=f"Label '{label_id}' has been deleted.",
        )
    return await label_crud.update(id_=label_id, update_obj=label_update)


@fast_api_checklist_label_router.delete(
    "/label/{label_id}",
    description=f"Delete existing label. Current user must be owner.",
)
async def delete_label(
    label_id: uuid.UUID,
    label_crud: LabelCRUD = Depends(LabelCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> LabelReadAPI:
    existing_label: Label = await label_crud.get(
        label_id,
        raise_exception_if_none=HTTPException(status_code=status.HTTP_404_NOT_FOUND),
        include_deleted=True,
    )
    if existing_label.owner_id != current_user.id:
        # 404, not 401 — same reasoning as update_label above.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    # Soft delete (WI-2). Idempotent: re-deleting an already-tombstoned label is a
    # no-op success so an outbox replay is safe. Chips referencing this label are
    # masked at read time (the CheckListLabel link rows are left in place).
    await label_crud.soft_delete(id_=label_id)
    return existing_label


@fast_api_checklist_label_router.put(
    "/label/sort",
    description=f"Provide list of label ids to set new sort order of these labels.",
    response_model=List[LabelReadAPI],
)
async def sort_labels(
    label_order: List[uuid.UUID],
    label_crud: LabelCRUD = Depends(LabelCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> List[LabelReadAPI]:
    return await label_crud.sort(user_id=current_user.id, label_order=label_order)


@fast_api_checklist_label_router.get(
    "/checklist/{checklist_id}/label",
    response_model=List[LabelReadAPI],
    description=f"List all labels of an existing checklist.",
)
async def list_labels_of_checklist(
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.view)
    ),
    checklist_label_crud: ChecklistLabelCRUD = Depends(ChecklistLabelCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> List[LabelReadAPI]:
    # Labels are per-user: return only the caller's labels on this card, never
    # those another collaborator attached (see list_labels_for_user).
    return await checklist_label_crud.list_labels_for_user(
        checklist_id=checklist_access.checklist.id,
        user_id=current_user.id,
    )


@fast_api_checklist_label_router.put(
    "/checklist/{checklist_id}/label/{label_id}",
    response_model=LabelReadAPI,
    description=f"Add an existing label to an existign checklist.",
)
async def add_label_to_checklist(
    label_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.view)
    ),
    label_crud: LabelCRUD = Depends(LabelCRUD.get_crud),
    checklist_label_crud: ChecklistLabelCRUD = Depends(ChecklistLabelCRUD.get_crud),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> LabelReadAPI:
    checklist_id = checklist_access.checklist.id
    label_not_exist_exception = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Label does not exist"
    )
    existing_label: Label = await label_crud.get(
        label_id,
        raise_exception_if_none=label_not_exist_exception,
    )
    if existing_label.owner_id != current_user.id:
        raise label_not_exist_exception

    await checklist_label_crud.create(
        CheckListLabelCreate(
            checklist_id=checklist_id,
            label_id=label_id,
            user_id=current_user.id,
        ),
        exists_ok=True,
    )
    await sync_crud.create(SyncNotification(cl_id=checklist_id, upd_prop="checklist_label"))
    return existing_label


@fast_api_checklist_label_router.delete(
    "/checklist/{checklist_id}/label/{label_id}",
    description=f"Remove an existing label from an existign checklist.",
)
async def remove_label_from_checklist(
    label_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.view)
    ),
    label_crud: LabelCRUD = Depends(LabelCRUD.get_crud),
    checklist_label_crud: ChecklistLabelCRUD = Depends(ChecklistLabelCRUD.get_crud),
    checklist_position_crud: CheckListPositionCRUD = Depends(
        CheckListPositionCRUD.get_crud
    ),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
    current_user: User = Depends(get_current_user),
):
    await checklist_label_crud.delete(
        checklist_id=checklist_access.checklist.id,
        label_id=label_id,
        user_id=current_user.id,
    )
    # The link row is HARD-deleted (WI-2 kept checklist_label untombstoned), so
    # the detach itself leaves no server_seq trace and the delta feed would never
    # re-emit the card — an offline device would keep the stale chip forever.
    # Re-stamp the caller's position row (per-user, like the label set) so the
    # feed's card-level query picks the card up for this user only.
    await checklist_position_crud.touch(
        checklist_id=checklist_access.checklist.id, user_id=current_user.id
    )
    await sync_crud.create(SyncNotification(cl_id=checklist_access.checklist.id, upd_prop="checklist_label"))
