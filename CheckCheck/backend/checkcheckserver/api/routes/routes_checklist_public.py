"""Anonymous consumption surface for public URL shares (Phase 5 of card sharing).

These endpoints live under ``/public/checklist/{token}/...`` and are the *only*
routes reachable without a logged-in ``User``. Authentication is by capability:
``resolve_public_checklist_access`` turns the path ``token`` into a
``UserChecklistAccess`` carrying an anonymous principal and the link's level, so
the very same ``has_at_least`` guards used on the authed surface apply here. The
existing authed routes are untouched — ``get_current_user`` stays mandatory there.

Anonymous visitors render a card with the **owner's** per-user settings (position,
labels), since an anonymous visitor has no per-user rows of their own. Writes
(check/edit links) emit the normal sync notifications; the fan-out resolves owner
+ collaborators + active public tokens, so authed users and other anonymous
viewers all see the change live.
"""

import datetime
import uuid
import decimal
from typing import Type, Optional

from fastapi import APIRouter, Depends, Security, HTTPException, status, Query, Header
from pydantic import BaseModel, Field

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

from checkcheckserver.db.user import User
from checkcheckserver.api.auth.security import get_current_user
from checkcheckserver.api.access import (
    resolve_public_checklist_access,
    require_public_checklist_permission,
    verify_item_belongs_to_public_checklist,
    link_is_resolvable,
    attach_my_permission,
    ChecklistAccessLevel,
    UserChecklistAccess,
)
from checkcheckserver.api.share_password import (
    verify_share_password,
    verify_share_grant,
    make_share_grant,
    GRANT_TTL_SECONDS,
)
from checkcheckserver.api.routes.routes_checklist_share import (
    require_public_links_enabled,
    _ensure_position,
)
from checkcheckserver.model.checklist import CheckListApiWithSubObj
from checkcheckserver.db.checklist import CheckListCRUD
from checkcheckserver.db.checklist_label import ChecklistLabelCRUD
from checkcheckserver.db.checklist_position import CheckListPositionCRUD
from checkcheckserver.db.checklist_collaborator import CheckListCollaboratorCRUD
from checkcheckserver.db.checklist_public_share import CheckListPublicShareCRUD
from checkcheckserver.db.checklist_item import CheckListItemCRUD
from checkcheckserver.db.checklist_item_state import CheckListItemStateCRUD
from checkcheckserver.db.checklist_item_position import CheckListItemPositionCRUD
from checkcheckserver.model.checklist_item import (
    CheckListItem,
    CheckListItemRead,
    CheckListItemCreateAPI,
    CheckListItemCreate,
    CheckListItemUpdate,
)
from checkcheckserver.model.checklist_item_position import (
    CheckListItemPositionCreate,
    CheckListItemPositionApiCreate,
)
from checkcheckserver.model.checklist_item_state import (
    CheckListItemStateCreate,
    CheckListItemStateUpdate,
    CheckListItemStateWithoutChecklistID,
)
from checkcheckserver.api.paginator import (
    PaginatedResponse,
    create_query_params_class,
    QueryParamsInterface,
)
from checkcheckserver.model.checklist import CheckList
from checkcheckserver.db.sync_notification import SyncNotifiationCRUD
from checkcheckserver.model.sync_notifications import SyncNotification

config = Config()
log = get_logger()

fast_api_checklist_public_router: APIRouter = APIRouter()

CheckListItemPublicQueryParams: Type[QueryParamsInterface] = create_query_params_class(
    CheckList, no_ordering=True
)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class UnlockRequest(BaseModel):
    password: str = Field(description="The link's passphrase.")


class UnlockResult(BaseModel):
    grant: str = Field(
        description=(
            "Short-lived signed grant. Replay it on subsequent calls via the "
            "'X-Share-Grant' header or '?share_grant=' query so you don't resend "
            "the passphrase."
        ),
    )
    expires_in: int = Field(description="Grant lifetime in seconds.")


@fast_api_checklist_public_router.get(
    "/public/checklist/{token}",
    response_model=CheckListApiWithSubObj,
    description="Open a publicly shared checklist (anonymous). Renders with the owner's per-user settings.",
)
async def get_public_checklist(
    checklist_access: UserChecklistAccess = Depends(resolve_public_checklist_access),
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    checklist_label_crud: ChecklistLabelCRUD = Depends(ChecklistLabelCRUD.get_crud),
    checklist_position_crud: CheckListPositionCRUD = Depends(
        CheckListPositionCRUD.get_crud
    ),
) -> CheckListApiWithSubObj:
    checklist_id = checklist_access.checklist.id
    owner_id = checklist_access.checklist.owner_id
    checklist = await checklist_crud.get(
        id_=checklist_id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This public link is not available.",
        ),
    )
    # An anonymous visitor has no per-user rows, so render with the owner's:
    # their card position/collapse settings and their private label set.
    owner_position = await checklist_position_crud.get(
        checklist_id=checklist_id, user_id=owner_id
    )
    if owner_position is not None:
        checklist.position = owner_position
    checklist.labels = await checklist_label_crud.list_labels_for_user(
        checklist_id=checklist_id, user_id=owner_id
    )
    # Anonymous visitor: my_permission reflects the link's level (never "owner").
    attach_my_permission(checklist, checklist_access.permission_level())
    return checklist


@fast_api_checklist_public_router.post(
    "/public/checklist/{token}/unlock",
    response_model=UnlockResult,
    dependencies=[Depends(require_public_links_enabled)],
    description=(
        "Exchange a protected link's passphrase for a short-lived grant the client "
        "replays on subsequent calls (so the passphrase never travels again)."
    ),
)
async def unlock_public_checklist(
    token: str,
    body: UnlockRequest,
    public_share_crud: CheckListPublicShareCRUD = Depends(
        CheckListPublicShareCRUD.get_crud
    ),
) -> UnlockResult:
    """Verify a passphrase and mint a grant.

    A wrong passphrase returns the **same 404** as a missing/disabled/expired link,
    so a guesser cannot use this endpoint as an oracle to confirm a link exists.
    The plaintext is read only here (POST body) and is never logged.
    """
    not_found = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="This public link is not available.",
    )
    link = await public_share_crud.get_by_token(token)
    if not link_is_resolvable(link):
        raise not_found
    if link.password_hash is None:
        # Nothing to unlock. Distinguishable from the 404 paths, but only to a
        # caller who already holds a working unprotected token (which already
        # grants full access), so this leaks nothing new.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This public link is not password protected.",
        )
    if not verify_share_password(body.password, link.password_hash):
        raise not_found
    return UnlockResult(
        grant=make_share_grant(token, link.password_hash),
        expires_in=GRANT_TTL_SECONDS,
    )


@fast_api_checklist_public_router.post(
    "/public/checklist/{token}/join",
    response_model=CheckListApiWithSubObj,
    dependencies=[Depends(require_public_links_enabled)],
    description=(
        "Add a publicly shared card to your own deck as a real collaborator at the "
        "link's level (the same live card — not a copy). Requires being logged in."
    ),
)
async def join_public_checklist(
    token: str,
    current_user: User = Depends(get_current_user),
    share_grant: Optional[str] = Query(default=None),
    x_share_grant: Optional[str] = Header(default=None),
    public_share_crud: CheckListPublicShareCRUD = Depends(
        CheckListPublicShareCRUD.get_crud
    ),
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    checklist_collaborator_crud: CheckListCollaboratorCRUD = Depends(
        CheckListCollaboratorCRUD.get_crud
    ),
    checklist_position_crud: CheckListPositionCRUD = Depends(
        CheckListPositionCRUD.get_crud
    ),
    checklist_label_crud: ChecklistLabelCRUD = Depends(ChecklistLabelCRUD.get_crud),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
) -> CheckListApiWithSubObj:
    """Self-service collaborator join via a public link.

    This is the one *authenticated* route on the otherwise-anonymous public
    surface: the ``token`` authorizes the read, but you need an account to own a
    deck slot, so ``get_current_user`` is required (logged out → 401). The token
    is resolved here (not via ``resolve_public_checklist_access``, which yields an
    anonymous principal) so the joining user's identity drives the new
    collaborator row.

    A passphrase-protected link must still be unlocked first: joining without a
    valid grant 404s exactly like resolving would, so the passphrase cannot be
    bypassed by a logged-in user who merely holds the URL.
    """
    not_found = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="This public link is not available.",
    )
    link = await public_share_crud.get_by_token(token)
    if not link_is_resolvable(link):
        raise not_found
    if link.password_hash is not None and not verify_share_grant(
        x_share_grant or share_grant, token, link.password_hash
    ):
        raise not_found

    checklist_id = link.checklist_id
    checklist = await checklist_crud.get(id_=checklist_id)
    if checklist is None:
        raise not_found

    # Idempotent: the owner already has the card, and an existing collaborator is
    # left untouched — joining a lower-level link must never downgrade them.
    is_owner = checklist.owner_id == current_user.id
    existing = await checklist_collaborator_crud.get_one(
        checklist_id=checklist_id, user_id=current_user.id
    )
    if not is_owner and existing is None:
        await checklist_collaborator_crud.upsert(
            checklist_id=checklist_id,
            user_id=current_user.id,
            permission=link.permission,
        )
        await _ensure_position(
            checklist_id, current_user.id, checklist_position_crud
        )
        # owner + collaborators (now including the joiner) see the share set grow.
        await sync_crud.create(
            SyncNotification(cl_id=checklist_id, upd_prop="share_added")
        )

    # Return the card scoped to the joining user (their own position + labels).
    user_position = await checklist_position_crud.get(
        checklist_id=checklist_id, user_id=current_user.id
    )
    if user_position is not None:
        checklist.position = user_position
    checklist.labels = await checklist_label_crud.list_labels_for_user(
        checklist_id=checklist_id, user_id=current_user.id
    )
    # The joining user now holds the card as a real principal: "owner" if it is
    # their own card, their existing level if they were already a collaborator
    # (join never downgrades), otherwise the link's level they just joined at.
    if is_owner:
        my_level = ChecklistAccessLevel.owner
    elif existing is not None:
        my_level = existing.permission
    else:
        my_level = link.permission
    attach_my_permission(checklist, my_level)
    return checklist


@fast_api_checklist_public_router.get(
    "/public/checklist/{token}/item",
    response_model=PaginatedResponse[CheckListItemRead],
    description="List the items of a publicly shared checklist (anonymous, view).",
)
async def list_public_checklist_items(
    checklist_access: UserChecklistAccess = Security(
        require_public_checklist_permission(ChecklistAccessLevel.view)
    ),
    checklist_item_crud: CheckListItemCRUD = Depends(CheckListItemCRUD.get_crud),
    pagination: QueryParamsInterface = Depends(CheckListItemPublicQueryParams),
) -> PaginatedResponse[CheckListItemRead]:
    checklist_id = checklist_access.checklist.id
    result_items = await checklist_item_crud.list(
        checklist_id=checklist_id,
        pagination=pagination,
    )
    return PaginatedResponse(
        total_count=await checklist_item_crud.count(checklist_id=checklist_id),
        offset=pagination.offset,
        count=len(result_items),
        items=result_items,
    )


@fast_api_checklist_public_router.patch(
    "/public/checklist/{token}/item/{checklist_item_id}/state",
    response_model=CheckListItemStateWithoutChecklistID,
    dependencies=[Depends(verify_item_belongs_to_public_checklist)],
    description="Toggle an item's checked state on a publicly shared checklist (anonymous, check).",
)
async def set_public_checklist_item_state(
    val: CheckListItemStateUpdate,
    checklist_item_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(
        require_public_checklist_permission(ChecklistAccessLevel.check)
    ),
    checklist_item_state_crud: CheckListItemStateCRUD = Depends(
        CheckListItemStateCRUD.get_crud
    ),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
) -> CheckListItemStateWithoutChecklistID:
    result = await checklist_item_state_crud.update(
        update_obj=val, id_=checklist_item_id
    )
    await sync_crud.create(
        SyncNotification(
            cl_id=checklist_access.checklist.id,
            cli_id=checklist_item_id,
            upd_prop="item_state",
        )
    )
    return result


@fast_api_checklist_public_router.post(
    "/public/checklist/{token}/item",
    response_model=CheckListItemRead,
    description="Create an item on a publicly shared checklist (anonymous, edit).",
)
async def create_public_checklist_item(
    checklist_item_create: CheckListItemCreateAPI,
    checklist_access: UserChecklistAccess = Security(
        require_public_checklist_permission(ChecklistAccessLevel.edit)
    ),
    checklist_item_crud: CheckListItemCRUD = Depends(CheckListItemCRUD.get_crud),
    checklist_item_pos_crud: CheckListItemPositionCRUD = Depends(
        CheckListItemPositionCRUD.get_crud
    ),
    checklist_item_state_crud: CheckListItemStateCRUD = Depends(
        CheckListItemStateCRUD.get_crud
    ),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
) -> CheckListItemRead:
    new_checklist_item_id = uuid.uuid4()
    checklist_id = checklist_access.checklist.id
    if checklist_item_create.position is None:
        checklist_item_create.position = CheckListItemPositionApiCreate()
    if checklist_item_create.position.index is None:
        last_checklist_item_pos = await checklist_item_pos_crud.get_last(checklist_id)
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
        **checklist_item_create.model_dump(exclude=["position", "state"]),
    )

    await checklist_item_crud.create(checklist_item)
    await checklist_item_pos_crud.create(checklist_item_position)
    await checklist_item_state_crud.create(checklist_item_state)
    await sync_crud.create(
        SyncNotification(
            cl_id=checklist_id, cli_id=new_checklist_item_id, upd_prop="item_created"
        )
    )
    return await checklist_item_crud.get(new_checklist_item_id)


@fast_api_checklist_public_router.patch(
    "/public/checklist/{token}/item/{checklist_item_id}",
    response_model=CheckListItemRead,
    dependencies=[Depends(verify_item_belongs_to_public_checklist)],
    description="Edit an item's text on a publicly shared checklist (anonymous, edit).",
)
async def update_public_checklist_item(
    checklist_item_id: uuid.UUID,
    checklist_item_update: CheckListItemUpdate,
    checklist_access: UserChecklistAccess = Security(
        require_public_checklist_permission(ChecklistAccessLevel.edit)
    ),
    checklist_item_crud: CheckListItemCRUD = Depends(CheckListItemCRUD.get_crud),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
) -> CheckListItemRead:
    checklist_id = checklist_access.checklist.id
    db_item = await checklist_item_crud.update(
        checklist_item_update,
        id_=checklist_item_id,
        raise_exception_if_not_exists=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item with id {checklist_item_id} does not exist.",
        ),
    )
    await sync_crud.create(
        SyncNotification(
            cl_id=checklist_id, cli_id=checklist_item_id, upd_prop="item_text"
        )
    )
    return db_item


@fast_api_checklist_public_router.delete(
    "/public/checklist/{token}/item/{checklist_item_id}",
    response_model=bool,
    dependencies=[Depends(verify_item_belongs_to_public_checklist)],
    description="Delete an item from a publicly shared checklist (anonymous, edit).",
)
async def delete_public_checklist_item(
    checklist_item_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(
        require_public_checklist_permission(ChecklistAccessLevel.edit)
    ),
    checklist_item_crud: CheckListItemCRUD = Depends(CheckListItemCRUD.get_crud),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
) -> bool:
    checklist_id = checklist_access.checklist.id
    await checklist_item_crud.delete(id_=checklist_item_id)
    await sync_crud.create(
        SyncNotification(
            cl_id=checklist_id, cli_id=checklist_item_id, upd_prop="item_deleted"
        )
    )
    return True
