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

from checkcheckserver.model.label import Label, LabelUpdate, LabelCreateAPI
from checkcheckserver.model.checklist_label import CheckListLabel, CheckListLabelCreate
from checkcheckserver.db.label import LabelCRUD
from checkcheckserver.db.checklist_label import ChecklistLabelCRUD

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


fast_api_checklist_label_router: APIRouter = APIRouter()

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


@fast_api_checklist_label_router.get(
    "/label",
    response_model=List[Label],
    description=f"List all labels of the current user",
)
async def list_labels(
    label_crud: LabelCRUD = Depends(LabelCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> Dict[uuid.UUID, CheckListsItemPreview]:
    return await label_crud.list(user_id=current_user.id)


@fast_api_checklist_label_router.post(
    "/label",
    response_model=Label,
    description=f"Create new label for current user.",
)
async def create_label(
    label_create: LabelCreateAPI,
    label_crud: LabelCRUD = Depends(LabelCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> Label:
    return await label_crud.create(
        Label.model_validate(
            label_create.model_dump(exclude_unset=True) | {"owner_id": current_user.id}
        )
    )


@fast_api_checklist_label_router.patch(
    "/label/{label_id}",
    response_model=LabelUpdate,
    description=f"Update existing label. Current user must be owner.",
)
async def update_label(
    label_id: uuid.UUID,
    label_update: LabelUpdate,
    label_crud: LabelCRUD = Depends(LabelCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> Label:
    existing_label: Label = await label_crud.get(
        label_id,
        raise_exception_if_none=HTTPException(status_code=status.HTTP_404_NOT_FOUND),
    )
    if existing_label.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return await label_crud.update(id_=label_id, update_obj=label_update)


@fast_api_checklist_label_router.delete(
    "/label/{label_id}",
    description=f"Delete existing label. Current user must be owner.",
)
async def update_label(
    label_id: uuid.UUID,
    label_crud: LabelCRUD = Depends(LabelCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> Label:
    existing_label: Label = await label_crud.get(
        label_id,
        raise_exception_if_none=HTTPException(status_code=status.HTTP_404_NOT_FOUND),
    )
    if existing_label.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return await label_crud.delete(id_=label_id)
