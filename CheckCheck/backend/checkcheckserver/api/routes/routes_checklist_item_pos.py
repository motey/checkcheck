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

log = get_logger()


fast_api_checklist_item_pos_router: APIRouter = APIRouter()


@fast_api_checklist_item_pos_router.get(
    "/checklist/{checklist_id}/item/{checklist_item_id}/position",
    response_model=CheckListItemPositionPublicWithoutChecklistID,
    description=f"Get the position for a specific checklist item",
)
async def get_checklist_item_position(
    checklist_item_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(user_has_checklist_access),
    checklist_item_pos_crud: CheckListItemPositionCRUD = Depends(
        CheckListItemPositionCRUD.get_crud
    ),
) -> CheckListItemPositionPublicWithoutChecklistID:
    return await checklist_item_pos_crud.get(
        checklist_item_id=checklist_item_id,
        raise_exception_if_none=HTTPException(status_code=status.HTTP_404_NOT_FOUND),
    )


@fast_api_checklist_item_pos_router.patch(
    "/checklist/{checklist_id}/item/{checklist_item_id}/position",
    response_model=CheckListItemPosition,
    description=f"Get the position for a specific checklist item",
)
async def set_checklist_item_position(
    position: CheckListItemPositionApiUpdate,
    checklist_item_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(user_has_checklist_access),
    checklist_item_pos_crud: CheckListItemPositionCRUD = Depends(
        CheckListItemPositionCRUD.get_crud
    ),
) -> CheckListItemPosition:
    return await checklist_item_pos_crud.update(
        checklist_item_position_update=CheckListItemPositionUpdate(
            checklist_item_id=checklist_item_id,
            **position.model_dump(exclude_unset=True),
        ),
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item with uuid '{checklist_item_id}' can not be found.",
        ),
    )


@fast_api_checklist_item_pos_router.patch(
    "/checklist/{checklist_id}/item/{checklist_item_id}/move/under/{other_checklist_item_id}",
    response_model=CheckListItemPosition,
    description=f"Move a checklist item under another checklist item in the positon index (the new checklist-position-index-value will be higher compared to the other checklist-position-index-value)",
)
async def move_item_under_other_item(
    checklist_item_id: uuid.UUID,
    other_checklist_item_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(user_has_checklist_access),
    checklist_item_pos_crud: CheckListItemPositionCRUD = Depends(
        CheckListItemPositionCRUD.get_crud
    ),
) -> CheckListItemPosition:
    target_pos = await checklist_item_pos_crud.get(
        checklist_item_id=checklist_item_id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item with uuid '{checklist_item_id}' can not be found.",
        ),
    )
    other_item_pos = await checklist_item_pos_crud.get(
        checklist_item_id=other_checklist_item_id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Other item with uuid '{other_checklist_item_id}' can not be found.",
        ),
    )

    item_under_other_item_pos: CheckListItemPosition | None = (
        await checklist_item_pos_crud.get_next(
            checklist_id=checklist_access.checklist.id,
            current_checklist_item_id=other_checklist_item_id,
        )
    )
    if item_under_other_item_pos is None:
        target_pos.index = float(
            decimal.Decimal(str(other_item_pos.index)) + decimal.Decimal(str(0.4))
        )
        await checklist_item_pos_crud.update(target_pos)
        return target_pos
    target_pos.index = float(
        (
            (
                decimal.Decimal(str(item_under_other_item_pos.index))
                - decimal.Decimal(str(other_item_pos.index))
            )
            / 2
        )
        + decimal.Decimal(str(other_item_pos.index))
    )
    await checklist_item_pos_crud.update(target_pos)
    return target_pos


@fast_api_checklist_item_pos_router.patch(
    "/checklist/{checklist_id}/item/{checklist_item_id}/move/above/{other_checklist_item_id}",
    response_model=CheckListItemPosition,
    description=f"Move a checklist item above another checklist item in the positon index (the new checklist-position-index-value will be lower compared to the other checklist-position-index-value)",
)
async def move_item_above_other_item(
    checklist_item_id: uuid.UUID,
    other_checklist_item_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(user_has_checklist_access),
    checklist_item_pos_crud: CheckListItemPositionCRUD = Depends(
        CheckListItemPositionCRUD.get_crud
    ),
) -> CheckListItemPosition:
    target_pos = await checklist_item_pos_crud.get(
        checklist_item_id=checklist_item_id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item with uuid '{checklist_item_id}' can not be found.",
        ),
    )
    other_item_pos = await checklist_item_pos_crud.get(
        checklist_item_id=other_checklist_item_id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Other item with uuid '{other_checklist_item_id}' can not be found.",
        ),
    )

    item_over_other_item_pos: CheckListItemPosition | None = (
        await checklist_item_pos_crud.get_prev(
            checklist_id=checklist_access.checklist.id,
            current_checklist_item_id=other_checklist_item_id,
        )
    )
    if item_over_other_item_pos is None:
        target_pos.index = float(
            decimal.Decimal(str(other_item_pos.index)) - decimal.Decimal(str(0.4))
        )
        await checklist_item_pos_crud.update(target_pos)
        return target_pos
    target_pos.index = (
        (
            decimal.Decimal(str(other_item_pos.index))
            - decimal.Decimal(str(item_over_other_item_pos.index))
        )
        / 2
    ) - decimal.Decimal(str(other_item_pos.index))
    await checklist_item_pos_crud.update(target_pos)
    return target_pos


@fast_api_checklist_item_pos_router.patch(
    "/checklist/{checklist_id}/item/{checklist_item_id}/move/bottom",
    response_model=CheckListItemPosition,
    description=f"",
)
async def move_item_to_bottom_of_checklist(
    checklist_item_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(user_has_checklist_access),
    checklist_item_pos_crud: CheckListItemPositionCRUD = Depends(
        CheckListItemPositionCRUD.get_crud
    ),
) -> CheckListItemPosition:
    target_pos = await checklist_item_pos_crud.get(
        checklist_item_id=checklist_item_id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item with uuid '{checklist_item_id}' can not be found.",
        ),
    )
    last_pos = await checklist_item_pos_crud.get_last(
        checklist_id=checklist_access.checklist.id,
    )
    if last_pos.index == target_pos.index:
        await checklist_item_pos_crud.update(target_pos)
        return target_pos
    target_pos.index = decimal.Decimal(str(last_pos.index)) - decimal.Decimal("0.4")
    await checklist_item_pos_crud.update(target_pos)
    return target_pos


@fast_api_checklist_item_pos_router.patch(
    "/checklist/{checklist_id}/item/{checklist_item_id}/move/top",
    response_model=CheckListItemPosition,
    description=f"Get the position for a specific checklist item",
)
async def move_item_to_top_of_checklist(
    checklist_item_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(user_has_checklist_access),
    checklist_item_pos_crud: CheckListItemPositionCRUD = Depends(
        CheckListItemPositionCRUD.get_crud
    ),
) -> CheckListItemPosition:
    target_pos = await checklist_item_pos_crud.get(
        checklist_item_id=checklist_item_id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item with uuid '{checklist_item_id}' can not be found.",
        ),
    )
    first_pos = await checklist_item_pos_crud.get_first(
        checklist_id=checklist_access.checklist.id,
    )
    if first_pos.index == target_pos.index:
        await checklist_item_pos_crud.update(target_pos)
        return target_pos
    target_pos.index = float(
        decimal.Decimal(str(first_pos.index)) + decimal.Decimal("0.4")
    )
    await checklist_item_pos_crud.update(target_pos)
    return target_pos
