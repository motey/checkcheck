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
    CheckListCreate,
    ChecklistColorScheme,
    CheckList,
    CheckListCreate,
    CheckListUpdate,
)
from checkcheckserver.db.checklist import CheckListCRUD
from checkcheckserver.db.checklist_position import (
    CheckListPosition,
    CheckListPositionCRUD,
)
from checkcheckserver.model.checklist_item_position import (
    CheckListItemPosition,
    CheckListItemPositionCreate,
    CheckListItemPositionUpdate,
    CheckListItemPositionApiCreate,
    CheckListItemPositionApiUpdate,
    CheckListItemPositionPublicWithoutChecklistID,
)
from checkcheckserver.model.checklist_item_state import (
    CheckListItemState,
    CheckListItemStateCreate,
    CheckListItemStateUpdate,
    CheckListItemStateWithoutChecklistID,
)
from checkcheckserver.db.checklist_collaborator import CheckListCollaboratorCRUD
from checkcheckserver.model.checklist_collaborator import CheckListCollaborator
from checkcheckserver.db.checklist_item_position import CheckListItemPositionCRUD
from checkcheckserver.db.checklist_item_state import CheckListItemStateCRUD
from checkcheckserver.db.checklist_item import CheckListItemCRUD
from checkcheckserver.model.checklist_item import (
    CheckListItem,
    CheckListItemCreateAPI,
    CheckListItemUpdate,
    CheckListItemCreate,
    CheckListItemRead,
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
    verify_item_belongs_to_checklist,
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


fast_api_checklist_item_router: APIRouter = APIRouter()

CheckListItemQueryParams: Type[QueryParamsInterface] = create_query_params_class(
    CheckList, no_ordering=True
)


from pydantic import BaseModel, Field


class CheckListsItemPreview(BaseModel):
    items: List[CheckListItemRead]
    item_count: int = Field(
        default=0, description="Total count of checklist items in the backend."
    )
    item_checked_count: int = Field(
        default=0, description="Total count of checked checklist items in the backend."
    )
    item_unchecked_count: int = Field(
        default=0,
        description="Total count of unchecked checklist items in the backend.",
    )


@fast_api_checklist_item_router.get(
    "/item",
    response_model=Dict[uuid.UUID, CheckListsItemPreview],
    description=f"List first items of all or certain checklists. This should only be used as a bootstrap endpoint to initaly create an overview panel of all checklists. Therefor the maximum items count per checklist is limited to 32.",
)
async def list_items(
    checklist_ids: Optional[List[uuid.UUID]] = Query(
        default_factory=list,
        description="Only return certain checklist items by id. If left empty all checklists will be returned.",
    ),
    checked: Optional[bool] = Query(None),
    limit_per_checklist: Annotated[int | None, Query(lt=24)] = 9,
    checklist_item_crud: CheckListItemCRUD = Depends(CheckListItemCRUD.get_crud),
    checklist_ids_with_user_access: List[uuid.UUID] = Depends(
        checklist_ids_with_access
    ),
    current_user: User = Depends(get_current_user),
) -> Dict[uuid.UUID, CheckListsItemPreview]:
    if checklist_ids:
        for checklist_id in checklist_ids:
            if checklist_id not in checklist_ids_with_user_access:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access to checklist with ID {checklist_id} not allowed.",
                )
    else:
        checklist_ids = checklist_ids_with_user_access
    preview_per_checklist = await checklist_item_crud.list_multiple_checklist_items(
        checklist_ids=(
            checklist_ids if checklist_ids else checklist_ids_with_user_access
        ),
        checked=checked,
        limit_per_checklist=limit_per_checklist,
    )
    result = {}
    for id, items in preview_per_checklist.items():
        result[id] = CheckListsItemPreview(
            items=items,
            item_count=await checklist_item_crud.count(id),
            item_checked_count=await checklist_item_crud.count(id, checked=True),
            item_unchecked_count=await checklist_item_crud.count(id, checked=False),
        )
    return result


@fast_api_checklist_item_router.get(
    "/checklist/{checklist_id}/item",
    response_model=PaginatedResponse[CheckListItemRead],
    description=f"List all items of a certain checklist.",
)
async def list_checklist_items(
    checklist_id: uuid.UUID,
    checked: Optional[bool] = Query(None),
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.view)
    ),
    checklist_item_crud: CheckListItemCRUD = Depends(CheckListItemCRUD.get_crud),
    pagination: QueryParamsInterface = Depends(CheckListItemQueryParams),
    current_user: User = Depends(get_current_user),
) -> PaginatedResponse[CheckListItemRead]:
    result_items = await checklist_item_crud.list(
        checklist_id=checklist_id,
        checked=checked,
        pagination=pagination,
    )
    return PaginatedResponse(
        total_count=await checklist_item_crud.count(
            checklist_id=checklist_id,
            checked=checked,
        ),
        offset=pagination.offset,
        count=len(result_items),
        items=result_items,
    )


@fast_api_checklist_item_router.get(
    "/checklist/{checklist_id}/item/{checklist_item_id}",
    response_model=CheckListItemRead,
    description=f"Get a certain checklist.",
    dependencies=[Depends(verify_item_belongs_to_checklist)],
)
async def get_checklist_item(
    checklist_item_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.view)
    ),
    checklist_item_crud: CheckListItemCRUD = Depends(CheckListItemCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> CheckListItemRead:
    return await checklist_item_crud.get(
        id_=checklist_item_id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Item with id {checklist_item_id} does not exist.",
        ),
    )


@fast_api_checklist_item_router.post(
    "/checklist/{checklist_id}/item",
    response_model=CheckListItemRead,
    description=f"Create new item in existing checklist.",
)
async def create_checklist_item(
    checklist_item_create: CheckListItemCreateAPI,
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.edit)
    ),
    checklist_item_crud: CheckListItemCRUD = Depends(CheckListItemCRUD.get_crud),
    checklist_item_pos_crud: CheckListItemPositionCRUD = Depends(
        CheckListItemPositionCRUD.get_crud
    ),
    checklist_item_state_crud: CheckListItemStateCRUD = Depends(
        CheckListItemStateCRUD.get_crud
    ),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> CheckListItemRead:
    checklist_id = checklist_access.checklist.id
    # Idempotent create (WI-3): the client may supply the item's UUID so an outbox
    # replay doesn't duplicate it. If an item with that id already exists, don't
    # create a second one.
    if checklist_item_create.id is not None:
        existing = await checklist_item_crud.get(
            id_=checklist_item_create.id, include_deleted=True
        )
        if existing is not None:
            if existing.checklist_id != checklist_id:
                # The id is taken by an item in another card — a UUID collision,
                # not a replay. Terminal for the outbox.
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Item id '{checklist_item_create.id}' already exists.",
                )
            if existing.deleted_at is not None:
                # Re-creating a since-tombstoned item must not resurrect it.
                raise HTTPException(
                    status_code=status.HTTP_410_GONE,
                    detail=f"Item '{checklist_item_create.id}' has been deleted.",
                )
            # Same card, still live → replay. Return the existing item unchanged.
            return await checklist_item_crud.get(checklist_item_create.id)
    new_checklist_item_id = checklist_item_create.id or uuid.uuid4()
    if checklist_item_create.position is None:
        checklist_item_create.position = CheckListItemPositionApiCreate()
    if checklist_item_create.position.index is None:
        last_checklist_item_pos = await checklist_item_pos_crud.get_last(
            checklist_access.checklist.id
        )
        # https://0.30000000000000004.com/#python-3
        checklist_item_create.position.index = (
            float(
                decimal.Decimal(str(last_checklist_item_pos.index))
                + decimal.Decimal(str(0.4))
            )
            if last_checklist_item_pos is not None
            else 0.4
        )

    checklist_item_position = CheckListItemPositionCreate(
        checklist_item_id=new_checklist_item_id,
        **checklist_item_create.position.model_dump(),
    )
    if checklist_item_create.state is None:
        checklist_item_state = CheckListItemStateCreate(
            checklist_item_id=new_checklist_item_id, checked=False
        )
    else:
        checklist_item_state = CheckListItemStateCreate(
            checklist_item_id=new_checklist_item_id,
            **checklist_item_create.state.model_dump(),
        )

    checklist_item = CheckListItemCreate(
        id=new_checklist_item_id,
        checklist_id=checklist_id,
        **checklist_item_create.model_dump(exclude=["position", "state", "id"]),
    )

    await checklist_item_crud.create(checklist_item)
    await checklist_item_pos_crud.create(checklist_item_position)
    await checklist_item_state_crud.create(checklist_item_state)
    await sync_crud.create(SyncNotification(
        cl_id=checklist_id, cli_id=new_checklist_item_id, upd_prop="item_created"
    ))
    return await checklist_item_crud.get(new_checklist_item_id)


@fast_api_checklist_item_router.patch(
    "/checklist/{checklist_id}/item/{checklist_item_id}",
    response_model=CheckListItemRead,
    description=f"Update an existing item in a checklist.",
)
async def update_checklist_item(
    checklist_item_id: uuid.UUID,
    checklist_item_update: CheckListItemUpdate,
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.edit)
    ),
    checklist_item_crud: CheckListItemCRUD = Depends(CheckListItemCRUD.get_crud),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> CheckListItemRead:
    checklist_id = checklist_access.checklist.id
    checklist_item_not_exists_error = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Item with id {checklist_item_id} does not exist.",
    )
    db_item: CheckListItem = await checklist_item_crud.get(
        id_=checklist_item_id,
        raise_exception_if_none=checklist_item_not_exists_error,
        include_deleted=True,
    )
    if db_item.checklist_id != checklist_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Item with id {checklist_item_id} not existing in checklist with id {checklist_id}",
        )
    if db_item.deleted_at is not None:
        # A stale edit must not resurrect a tombstoned item (WI-2). 410 Gone is
        # terminal for the outbox.
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=f"Item '{checklist_item_id}' has been deleted.",
        )
    db_item: CheckListItem = await checklist_item_crud.update(
        checklist_item_update,
        id_=checklist_item_id,
        raise_exception_if_not_exists=checklist_item_not_exists_error,
    )
    await sync_crud.create(SyncNotification(
        cl_id=checklist_id, cli_id=checklist_item_id, upd_prop="item_text"
    ))
    return db_item


@fast_api_checklist_item_router.delete(
    "/checklist/{checklist_id}/item/{checklist_item_id}",
    description=f"Delete an existing item from a checklist.",
    response_model=bool,
)
async def delete_checklist_item(
    checklist_item_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.edit)
    ),
    checklist_item_crud: CheckListItemCRUD = Depends(CheckListItemCRUD.get_crud),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> bool:
    checklist_id = checklist_access.checklist.id
    checklist_item_not_exists_error = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Item with id {checklist_item_id} does not exist.",
    )
    # Fetch tombstone-aware: a re-delete (outbox replay) of an already-deleted
    # item is idempotent success, not an error.
    db_item: CheckListItem = await checklist_item_crud.get(
        id_=checklist_item_id,
        raise_exception_if_none=checklist_item_not_exists_error,
        include_deleted=True,
    )
    if db_item.checklist_id != checklist_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Item with id {checklist_item_id} not existing in checklist with id {checklist_id}",
        )
    if db_item.deleted_at is not None:
        # Already tombstoned — nothing to do, no duplicate sync poke.
        return True
    # Soft delete (WI-2): tombstone the item so the removal reaches offline
    # clients and cannot be resurrected by a stale edit. State/position children
    # are left in place, masked by this tombstone.
    await checklist_item_crud.soft_delete(id_=checklist_item_id)
    await sync_crud.create(SyncNotification(
        cl_id=checklist_id, cli_id=checklist_item_id, upd_prop="item_deleted"
    ))
    return True
