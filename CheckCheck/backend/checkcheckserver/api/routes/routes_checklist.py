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
    CheckListCountsPublic,
    SharedFilter,
)
from checkcheckserver.db.checklist import CheckListCRUD
from checkcheckserver.db.checklist_position import (
    CheckListPosition,
    CheckListPositionCreate,
    CheckListPositionCRUD,
)
from checkcheckserver.db.checklist_collaborator import CheckListCollaboratorCRUD
from checkcheckserver.model.checklist_collaborator import CheckListCollaborator
from checkcheckserver.db.checklist_label import ChecklistLabelCRUD
from checkcheckserver.config import Config
from checkcheckserver.api.auth.security import (
    user_is_admin,
    user_is_usermanager,
    get_current_user,
)

from checkcheckserver.api.access import (
    user_has_checklist_access,
    require_checklist_permission,
    attach_my_permission,
    ChecklistAccessLevel,
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
    search: Optional[str] = Query(None),
    shared: Optional[SharedFilter] = Query(
        None,
        description="Restrict to cards shared *with* the caller ('with_me') or "
        "shared *by* the caller ('by_me'). ANDs with label_id/search/archived.",
    ),
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    checklist_label_crud: ChecklistLabelCRUD = Depends(ChecklistLabelCRUD.get_crud),
    checklist_collaborator_crud: CheckListCollaboratorCRUD = Depends(
        CheckListCollaboratorCRUD.get_crud
    ),
    pagination: QueryParamsInterface = Depends(CheckListQueryParams),
    current_user: User = Depends(get_current_user),
) -> PaginatedResponse[CheckListApiWithSubObj]:
    result_checklist_items = await checklist_crud.list(
        user_id=current_user.id,
        archived=archived,
        pagination=pagination,
        include_sub_obj=True,
        label_id=label_id,
        search=search,
        shared=shared,
    )
    total_count = await checklist_crud.count(
        user_id=current_user.id,
        archived=archived,
        label_id=label_id,
        search=search,
        shared=shared,
    )
    # Labels are per-user; the ORM relationship returns every collaborator's
    # labels on a shared card, so scope each card's labels to the caller. Done
    # after every query above so the in-memory reassignment never triggers an
    # autoflush (it is never committed).
    labels_by_checklist = await checklist_label_crud.list_labels_for_user_by_checklist(
        checklist_ids=[cl.id for cl in result_checklist_items],
        user_id=current_user.id,
    )
    for checklist in result_checklist_items:
        checklist.labels = labels_by_checklist.get(checklist.id, [])
    # Attach the caller's effective permission (P0.1) so the client can gate
    # owner-only / collaborator UI. A listed card is one the caller owns or is an
    # accepted collaborator on (pending invites have no position, so never list);
    # resolve owner -> "owner", everyone else from their collaborator level.
    collaborator_permissions = (
        await checklist_collaborator_crud.permissions_for_user_by_checklist(
            checklist_ids=[cl.id for cl in result_checklist_items],
            user_id=current_user.id,
        )
    )
    for checklist in result_checklist_items:
        if checklist.owner_id == current_user.id:
            attach_my_permission(checklist, ChecklistAccessLevel.owner)
        else:
            # Defensive default to the most restrictive level: a non-owned card only
            # reaches this list via an accepted collaboration (per the position
            # inner-join), so a missing entry would be an invariant violation — fall
            # back to "view" rather than 500 the whole grid.
            attach_my_permission(
                checklist,
                collaborator_permissions.get(
                    checklist.id, ChecklistAccessLevel.view
                ),
            )
    return PaginatedResponse(
        total_count=total_count,
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
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
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
    checklist_db.position = index
    # The creator is always the owner.
    attach_my_permission(checklist_db, ChecklistAccessLevel.owner)
    await sync_crud.create(SyncNotification(cl_id=checklist_db.id, upd_prop="checklist_created"))
    return checklist_db


@fast_api_checklist_router.get(
    "/checklist/counts",
    response_model=CheckListCountsPublic,
    description="Aggregate card counts for the sidebar badges (Home, shared, "
    "Archive, per label). One request instead of an N+1 per entry; every count "
    "is access-scoped to the caller and excludes archived cards except the "
    "Archive count itself.",
)
async def get_checklist_counts(
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    current_user: User = Depends(get_current_user),
) -> CheckListCountsPublic:
    return CheckListCountsPublic(
        home=await checklist_crud.count(user_id=current_user.id, archived=False),
        shared_with_me=await checklist_crud.count(
            user_id=current_user.id, archived=False, shared=SharedFilter.with_me
        ),
        shared_by_me=await checklist_crud.count(
            user_id=current_user.id, archived=False, shared=SharedFilter.by_me
        ),
        archived=await checklist_crud.count(user_id=current_user.id, archived=True),
        labels=await checklist_crud.label_counts(user_id=current_user.id),
    )


@fast_api_checklist_router.get(
    "/checklist/{checklist_id}",
    response_model=CheckListApiWithSubObj,
    description=f"Update existing CheckList",
)
async def get_checklist(
    checklist_id: uuid.UUID,
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.view)
    ),
    checklist_label_crud: ChecklistLabelCRUD = Depends(ChecklistLabelCRUD.get_crud),
    checklist_position_crud: CheckListPositionCRUD = Depends(
        CheckListPositionCRUD.get_crud
    ),
    current_user: User = Depends(get_current_user),
) -> CheckListApiWithSubObj:
    checklist = await checklist_crud.get(
        id_=checklist_id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No checklist with id '{checklist_id}'",
        ),
    )
    # CheckListPosition is per-user: a shared card has N position rows (one per
    # collaborator + owner), but CheckList.position is a scalar (uselist=False)
    # joined relationship — the eager-load collapses those rows into the single
    # slot and picks one arbitrarily. So re-scope the position to the caller
    # (mirroring accept_invite / the user-scoped eager-load in list()); otherwise
    # a shared card could report another user's pinned/archived/index — e.g. the
    # owner's card silently unpinning the moment it is shared.
    user_position = await checklist_position_crud.get(
        checklist_id=checklist_id, user_id=current_user.id
    )
    if user_position is not None:
        checklist.position = user_position
    # Labels are per-user; the ORM relationship returns every collaborator's
    # labels, so scope them to the caller before returning. Done last so no
    # query runs after the in-memory reassignment (which is never committed).
    checklist.labels = await checklist_label_crud.list_labels_for_user(
        checklist_id=checklist_id, user_id=current_user.id
    )
    attach_my_permission(checklist, checklist_access.permission_level())
    return checklist


@fast_api_checklist_router.patch(
    "/checklist/{checklist_id}",
    response_model=CheckListApiWithSubObj,
    description=f"Update existing CheckList",
)
async def update_checklist(
    checklist_id: uuid.UUID,
    checklist: CheckListUpdate,
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.edit)
    ),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
) -> CheckListApiWithSubObj:
    result = await checklist_crud.update(
        id_=checklist_id,
        update_obj=checklist,
        raise_exception_if_not_exists=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No checklist with id '{checklist_id}'",
        ),
    )
    attach_my_permission(result, checklist_access.permission_level())
    await sync_crud.create(SyncNotification(cl_id=checklist_id, upd_prop="checklist"))
    return result


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
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
):
    if checklist_access.user_is_collaborator():
        leaver_id = checklist_access.user.id
        await checklist_position_crud.delete(
            user_id=leaver_id, checklist_id=checklist_id
        )
        await checklist_collaborator_crud.delete(
            user_id=leaver_id, checklist_id=checklist_id
        )
        # Only the leaver should drop the card from their view. Pin the target
        # explicitly: by now their collaborator row is gone, so dynamic
        # resolution would exclude them and notify everyone *else* instead.
        await sync_crud.create(
            SyncNotification(cl_id=checklist_id, upd_prop="checklist_deleted"),
            target_user_ids=[leaver_id],
        )
        # Everyone still on the card sees the collaborator set changed.
        await sync_crud.create(
            SyncNotification(cl_id=checklist_id, upd_prop="share_removed")
        )
        return

    if checklist_access.user_is_owner():
        # Capture who to notify *before* tombstoning, while the collaborator rows
        # still resolve the target set.
        target_user_ids = await sync_crud.resolve_target_user_ids(checklist_id)
        # Tombstone the checklist only (WI-2 cascade rule): every child row
        # (collaborators, per-user positions, items/states/item-positions) is left
        # untouched and masked by the parent tombstone. The access query filters
        # `CheckList.deleted_at IS NULL`, so collaborators lose access on their
        # next read; the delete propagates to offline clients via the delta feed
        # (WI-4) and a stale edit cannot resurrect the card. We deliberately do
        # NOT hard-delete the positions here: the card ORM object loaded by the
        # access guard eager-joins its position, and deleting that row out from
        # under it would break the tombstone flush's save-update cascade.
        await checklist_crud.soft_delete(
            id_=checklist_id,
            raise_exception_if_not_exists=HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No checklist with id '{checklist_id}'",
            ),
        )
        await sync_crud.create(
            SyncNotification(cl_id=checklist_id, upd_prop="checklist_deleted"),
            target_user_ids=target_user_ids,
        )
