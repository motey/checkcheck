from typing import Annotated, Sequence, List, Type, Optional
from datetime import datetime, timedelta, timezone
import uuid
import decimal
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


from fastapi import Depends, APIRouter


from checkcheckserver.db.user import User

"""
from checkcheckserver.model.checklist import (
    CheckListCreate,
    ChecklistColorScheme,
    CheckList,
    CheckListCreate,
    CheckListUpdate,
    CheckListPublic,
)
"""

from checkcheckserver.model.checklist import (
    CheckList,
    CheckListCreate,
    CheckListUpdate,
    CheckListApi,
    CheckListApiCreate,
    CheckListApiWithSubObj,
)
from checkcheckserver.db.checklist import CheckListCRUD
from checkcheckserver.db.checklist_position import (
    CheckListPosition,
    CheckListPositionCreate,
    CheckListPositionCRUD,
)
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


fast_api_checklist_router: APIRouter = APIRouter()

CheckListQueryParams: Type[QueryParamsInterface] = create_query_params_class(
    CheckList, no_ordering=True
)
CheckListPublicQueryParams: Type[QueryParamsInterface] = create_query_params_class(
    CheckListApi
)


@fast_api_checklist_router.get(
    "/checklist",
    response_model=PaginatedResponse[CheckListApiWithSubObj],
    description=f"List all CheckLists of the current user with their positions and configuration. This is a rather expensive endpoint and should be only used when really needed.",
)
async def list_checklists(
    archived: Optional[bool] = Query(False),
    label_id: Optional[uuid.UUID] = None,
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    checklist_pos_crud: CheckListPositionCRUD = Depends(CheckListPositionCRUD.get_crud),
    pagination: QueryParamsInterface = Depends(CheckListQueryParams),
    current_user: User = Depends(get_current_user),
) -> PaginatedResponse[CheckListApiWithSubObj]:
    result_checklist_items = await checklist_crud.list(
        user_id=current_user.id,
        archived=archived,
        pagination=pagination,
        include_sub_obj=True,
        label_id=label_id,
    )
    log.debug(f"result_checklist_items {result_checklist_items}")
    return PaginatedResponse(
        total_count=await checklist_crud.count(
            user_id=current_user.id,
            archived=archived,
        ),
        offset=pagination.offset,
        count=len(result_checklist_items),
        items=result_checklist_items,
    )


@fast_api_checklist_router.post(
    "/checklist",
    response_model=CheckListApiWithSubObj,
    description=f"Create a new CheckList.",
)
async def create_checklist(
    checklist: CheckListApiCreate,
    current_user: User = Depends(get_current_user),
    checklist_position_crud: CheckListPositionCRUD = Depends(
        CheckListPositionCRUD.get_crud
    ),
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
) -> CheckListApiWithSubObj:
    checklist_db: CheckList = await checklist_crud.create(
        CheckListCreate(
            **checklist.model_dump(exclude=["position"]), owner_id=current_user.id
        ),
    )
    if checklist.position is None:
        highest_existing_index_position = await checklist_position_crud.get_last(
            user_id=current_user.id
        )
        log.debug(f"highest_existing_order_position {highest_existing_index_position}")
        new_order_position = (
            float(
                decimal.Decimal(str(highest_existing_index_position.index))
                + decimal.Decimal("0.4")
            )
            if highest_existing_index_position is not None
            else 0
        )

        index = CheckListPositionCreate(
            checklist_id=checklist_db.id,
            user_id=current_user.id,
            index=new_order_position,
        )
    else:
        index = CheckListPositionCreate(
            checklist_id=checklist_db.id,
            user_id=current_user.id,
            index=checklist.position.index,
        )
    index: CheckListPosition = await checklist_position_crud.create(index)
    checklist_db: CheckList = await checklist_crud.get(
        checklist_db.id,
    )
    print("checklist_db", checklist_db.model_dump())
    checklist_db.position = index
    return checklist_db


@fast_api_checklist_router.get(
    "/checklist/{checklist_id}",
    response_model=CheckListApiWithSubObj,
    description=f"Update existing CheckList",
)
async def get_checklist(
    checklist_id: uuid.UUID,
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    checklist_access: UserChecklistAccess = Security(user_has_checklist_access),
) -> CheckListApiWithSubObj:
    return await checklist_crud.get(
        id_=checklist_id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No checklist with id '{checklist_id}'",
        ),
    )


@fast_api_checklist_router.patch(
    "/checklist/{checklist_id}",
    response_model=CheckListApiWithSubObj,
    description=f"Update existing CheckList",
)
async def update_checklist(
    checklist_id: uuid.UUID,
    checklist: CheckListUpdate,
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    checklist_access: UserChecklistAccess = Security(user_has_checklist_access),
) -> CheckListApiWithSubObj:
    return await checklist_crud.update(
        id_=checklist_id,
        update_obj=checklist,
        raise_exception_if_not_exists=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No checklist with id '{checklist_id}'",
        ),
    )


@fast_api_checklist_router.delete(
    "/checklist/{checklist_id}",
    description=f"Delete existing CheckList. The respective users specific CheckListPosition will be deleted as well.",
    response_class=Response,
    status_code=204,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": None},
    },
)
async def delete_checklist(
    checklist_id: uuid.UUID,
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    checklist_collaborator_crud: CheckListCollaboratorCRUD = Depends(
        CheckListCollaboratorCRUD.get_crud
    ),
    checklist_position_crud: CheckListPositionCRUD = Depends(
        CheckListPositionCRUD.get_crud
    ),
    checklist_access: UserChecklistAccess = Security(user_has_checklist_access),
):
    if checklist_access.user_is_collaborator:
        await checklist_position_crud.delete(
            user_id=checklist_access.user.id, checklist_id=checklist_id
        )
        await checklist_collaborator_crud.delete(
            user_id=checklist_access.user.id, checklist_id=checklist_id
        )
        return

    if checklist_access.user_is_owner:
        await checklist_collaborator_crud.delete(checklist_id=checklist_id)
        await checklist_position_crud.delete(checklist_id=checklist_id)
        await checklist_crud.delete(
            id_=checklist_id,
            raise_exception_if_not_exists=HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No checklist with id '{checklist_id}'",
            ),
        )
