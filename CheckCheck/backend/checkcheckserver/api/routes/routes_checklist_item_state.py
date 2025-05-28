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
from checkcheckserver.log import get_logger

config = Config()


log = get_logger()


fast_api_checklist_item_state_router: APIRouter = APIRouter()


@fast_api_checklist_item_state_router.get(
    "/checklist/{checklist_id}/item/{checklist_item_id}/state",
    response_model=CheckListItemStateWithoutChecklistID,
    description=f"Get the checked value for a specific checklist item",
)
async def get_checklist_item_checked_state(
    checklist_item_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(user_has_checklist_access),
    checklist_item_state_crud: CheckListItemStateCRUD = Depends(
        CheckListItemStateCRUD.get_crud
    ),
) -> CheckListItemStateWithoutChecklistID:
    return await checklist_item_state_crud.get_checklist_item_state(
        checklist_item_id=checklist_item_id,
        raise_exception_if_none=HTTPException(status_code=status.HTTP_404_NOT_FOUND),
    )


@fast_api_checklist_item_state_router.patch(
    "/checklist/{checklist_id}/item/{checklist_item_id}/state",
    response_model=CheckListItemStateWithoutChecklistID,
    description=f"Set the chacked value for a specific checklist item",
)
async def set_checklist_item_checked_state(
    val: CheckListItemStateUpdate,
    checklist_item_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(user_has_checklist_access),
    checklist_item_state_crud: CheckListItemStateCRUD = Depends(
        CheckListItemStateCRUD.get_crud
    ),
) -> CheckListItemStateWithoutChecklistID:
    return await checklist_item_state_crud.update(update_obj=val, id_=checklist_item_id)
