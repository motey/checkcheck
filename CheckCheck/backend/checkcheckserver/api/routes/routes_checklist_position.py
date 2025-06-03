from typing import Annotated, Sequence, List, Type
from datetime import datetime, timedelta, timezone
import uuid

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
from checkcheckserver.model.checklist_position import (
    CheckListPositionUpdate,
    CheckListPosition,
)
from checkcheckserver.db.checklist_position import CheckListPositionCRUD
from checkcheckserver.db.checklist_collaborator import CheckListCollaboratorCRUD
from checkcheckserver.model.checklist_collaborator import CheckListCollaborator
from checkcheckserver.config import Config
from checkcheckserver.api.auth.security import (
    user_is_admin,
    user_is_usermanager,
    get_current_user,
)

from checkcheckserver.api.access import (
    user_has_checklist_access,
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

log = get_logger()


fast_api_checklist_position_router: APIRouter = APIRouter()

CheckListPosQueryParams: Type[QueryParamsInterface] = create_query_params_class(
    CheckListPosition, default_order_by_attr="index"
)


@fast_api_checklist_position_router.get(
    "/position",
    response_model=PaginatedResponse[CheckListPosition],
    description=f"List all CheckListPosition objects of the current user.",
)
async def list_checklist_positions(
    archived: bool = Query(None),
    checklist_pos_crud: CheckListPositionCRUD = Depends(CheckListPositionCRUD.get_crud),
    pagination: QueryParamsInterface = Depends(CheckListPosQueryParams),
    current_user: User = Depends(get_current_user),
) -> PaginatedResponse[CheckListPosition]:
    result_items = await checklist_pos_crud.list(
        filter_user_id=current_user.id,
        archived=archived,
        pagination=pagination,
    )
    total_item_count = await checklist_pos_crud.count(
        filter_user_id=current_user.id,
        archived=archived,
    )
    return PaginatedResponse(
        total_count=total_item_count,
        offset=pagination.offset,
        count=len(result_items),
        items=result_items,
    )


@fast_api_checklist_position_router.get(
    "/checklist/{checklist_id}/position",
    response_model=CheckListPosition,
    description=f"List all CheckListPosition objects of the current user.",
)
async def get_checklist_position(
    checklist_pos_crud: CheckListPositionCRUD = Depends(CheckListPositionCRUD.get_crud),
    checklist_access: UserChecklistAccess = Security(user_has_checklist_access),
    current_user: User = Depends(get_current_user),
) -> PaginatedResponse[CheckList]:
    result_item = await checklist_pos_crud.get(
        user_id=current_user.user_name,
        checklist_id=checklist_access.checklist.id,
    )
    return result_item


@fast_api_checklist_position_router.patch(
    "/checklist/{checklist_id}/position",
    response_model=CheckListPosition,
    description=f"Update existing CheckListPosition object",
)
async def update_checklist_position(
    checklist_obj: CheckListPositionUpdate,
    checklist_pos_crud: CheckListPositionCRUD = Depends(CheckListPositionCRUD.get_crud),
    checklist_access: UserChecklistAccess = Security(user_has_checklist_access),
    current_user: User = Depends(get_current_user),
) -> CheckListPosition:
    result_item = await checklist_pos_crud.update(
        update_obj=checklist_obj,
        user_id=current_user.id,
        checklist_id=checklist_access.checklist.id,
    )
    return result_item


@fast_api_checklist_position_router.patch(
    "/checklist/{checklist_id}/move/under/{other_checklist_id}",
    response_model=CheckListPosition,
    description=f"Move a checklist under another checklist in the positon index (the new checklist-position-index-value will be lower to the other checklist-position-index-value)",
)
async def move_checklist_under_other_checklist(
    checklist_id: uuid.UUID,
    other_checklist_id: uuid.UUID,
    checklist_pos_crud: CheckListPositionCRUD = Depends(CheckListPositionCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> CheckListPosition:
    """Move a checklist under another checklist in the positon index (the new checklist-position-index-value will be lower to the other checklist-position-index-value)

    Args:
        checklist_id (uuid.UUID): _description_
        other_checklist_id (uuid.UUID): _description_
        checklist_pos_crud (CheckListPositionCRUD, optional): _description_. Defaults to Depends(CheckListPositionCRUD.get_crud).
        current_user (User, optional): _description_. Defaults to Depends(get_current_user).

    Returns:
        CheckListPosition: _description_
    """
    target_pos = await checklist_pos_crud.get(
        checklist_id=checklist_id,
        user_id=current_user.id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checklist with uuid '{checklist_id}' can not be found.",
        ),
    )
    other_pos = await checklist_pos_crud.get(
        checklist_id=other_checklist_id,
        user_id=current_user.id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checklist with uuid '{checklist_id}' can not be found.",
        ),
    )

    checklist_under_other_checklist_pos: CheckListPosition | None = (
        await checklist_pos_crud.get_prev(
            checklist_id=other_checklist_id, user_id=current_user.id
        )
    )
    if checklist_under_other_checklist_pos is None:
        target_pos.index = float(
            decimal.Decimal(str(other_pos.index)) - decimal.Decimal(str(0.4))
        )
        await checklist_pos_crud.update(
            target_pos, checklist_id=checklist_id, user_id=current_user.id
        )
        return target_pos
    target_pos.index = float(
        (
            (
                decimal.Decimal(str(other_pos.index))
                - decimal.Decimal(str(checklist_under_other_checklist_pos.index))
            )
            / 2
        )
        + decimal.Decimal(str(checklist_under_other_checklist_pos.index))
    )
    await checklist_pos_crud.update(
        target_pos, checklist_id=checklist_id, user_id=current_user.id
    )
    return target_pos


@fast_api_checklist_position_router.patch(
    "/checklist/{checklist_id}/move/above/{other_checklist_id}",
    response_model=CheckListPosition,
    description=f"Move a checklist above another checklist in the positon index (the new checklist-position-index-value will be higher compared to the other checklist-position-index-value)",
)
async def move_checklist_above_other_checklist(
    checklist_id: uuid.UUID,
    other_checklist_id: uuid.UUID,
    checklist_pos_crud: CheckListPositionCRUD = Depends(CheckListPositionCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> CheckListPosition:
    """Move a checklist above another checklist in the positon index (the new checklist-position-index-value will be higher compared to the other checklist-position-index-value)

    Args:
        checklist_id (uuid.UUID): _description_
        other_checklist_id (uuid.UUID): _description_
        checklist_pos_crud (CheckListPositionCRUD, optional): _description_. Defaults to Depends(CheckListPositionCRUD.get_crud).
        current_user (User, optional): _description_. Defaults to Depends(get_current_user).

    Returns:
        CheckListPosition: _description_
    """
    target_pos = await checklist_pos_crud.get(
        checklist_id=checklist_id,
        user_id=current_user.id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checklist with uuid '{checklist_id}' can not be found.",
        ),
    )
    other_pos = await checklist_pos_crud.get(
        checklist_id=other_checklist_id,
        user_id=current_user.id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checklist with uuid '{checklist_id}' can not be found.",
        ),
    )

    checklist_above_other_checklist_pos: CheckListPosition | None = (
        await checklist_pos_crud.get_next(
            checklist_id=other_checklist_id, user_id=current_user.id
        )
    )
    if checklist_above_other_checklist_pos is None:
        target_pos.index = float(
            decimal.Decimal(str(other_pos.index)) + decimal.Decimal(str(0.4))
        )
        await checklist_pos_crud.update(
            target_pos, checklist_id=checklist_id, user_id=current_user.id
        )
        return target_pos
    target_pos.index = float(
        (
            (
                decimal.Decimal(str(checklist_above_other_checklist_pos.index))
                - decimal.Decimal(str(other_pos.index))
            )
            / 2
        )
        + decimal.Decimal(str(checklist_above_other_checklist_pos.index))
    )
    await checklist_pos_crud.update(
        target_pos, checklist_id=checklist_id, user_id=current_user.id
    )
    return target_pos
