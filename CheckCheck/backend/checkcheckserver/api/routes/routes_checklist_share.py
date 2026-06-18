"""Share-management API for checklists (Phase 3 of card sharing).

Lets an owner grant/revoke per-user access at a permission level, lets a
collaborator remove themselves ("leave list"), and lets an owner transfer
ownership. Public/anonymous URL sharing is a separate, deferred effort (see
CARD_SHARING_PLAN.md, "Phase 5 design (deferred)").
"""

import uuid
import decimal
from typing import List, Optional, Annotated

from fastapi import APIRouter, Depends, Security, HTTPException, status
from pydantic import BaseModel, Field

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

from checkcheckserver.db.user import User, UserCRUD
from checkcheckserver.api.auth.security import get_current_user
from checkcheckserver.api.access import (
    user_has_checklist_access,
    require_checklist_permission,
    ChecklistAccessLevel,
    UserChecklistAccess,
)
from checkcheckserver.model.checklist_collaborator import (
    CheckListCollaborator,
    SharePermission,
)
from checkcheckserver.db.checklist_collaborator import CheckListCollaboratorCRUD
from checkcheckserver.db.checklist import CheckListCRUD
from checkcheckserver.db.checklist_position import (
    CheckListPositionCRUD,
    CheckListPositionCreate,
)
from checkcheckserver.db.sync_notification import SyncNotifiationCRUD
from checkcheckserver.model.sync_notifications import SyncNotification

config = Config()
log = get_logger()

fast_api_checklist_share_router: APIRouter = APIRouter()


# ── Gate ──────────────────────────────────────────────────────────────────────


async def require_sharing_enabled() -> None:
    """Disable the whole share API when sharing is switched off server-side."""
    if not config.SHARING_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Card sharing is disabled on this server.",
        )


# ── Schemas ─────────────────────────────────────────────────────────────────


class ShareUpsertRequest(BaseModel):
    permission: SharePermission = Field(
        description="Permission level to grant the target user.",
    )


class ShareRead(BaseModel):
    """A single collaborator entry with enough user info to render a share list."""

    user_id: uuid.UUID
    user_name: Optional[str] = None
    display_name: Optional[str] = None
    permission: SharePermission


class TransferOwnershipRequest(BaseModel):
    new_owner_id: uuid.UUID = Field(
        description="The user that should become the new owner. The previous owner is demoted to an 'edit' collaborator.",
    )


class TransferOwnershipResult(BaseModel):
    checklist_id: uuid.UUID
    new_owner_id: uuid.UUID
    previous_owner_id: uuid.UUID = Field(
        description="The previous owner, now demoted to an 'edit' collaborator.",
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _ensure_position(
    checklist_id: uuid.UUID,
    user_id: uuid.UUID,
    checklist_position_crud: CheckListPositionCRUD,
) -> None:
    """Make sure the user has a CheckListPosition for this checklist, so the card
    shows up in their grid (the checklist-list query joins on the per-user
    position). Places it at the top of their grid, mirroring create_checklist."""
    existing = await checklist_position_crud.get(
        checklist_id=checklist_id, user_id=user_id
    )
    if existing is not None:
        return
    last = await checklist_position_crud.get_last(user_id=user_id)
    new_index = (
        float(decimal.Decimal(str(last.index)) + decimal.Decimal("0.4"))
        if last is not None
        else 0
    )
    await checklist_position_crud.create(
        CheckListPositionCreate(
            checklist_id=checklist_id, user_id=user_id, index=new_index
        )
    )


# ── Endpoints ───────────────────────────────────────────────────────────────


@fast_api_checklist_share_router.get(
    "/checklist/{checklist_id}/shares",
    response_model=List[ShareRead],
    dependencies=[Depends(require_sharing_enabled)],
    description="List all collaborators of a checklist and their permission levels. Owner only.",
)
async def list_shares(
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.owner)
    ),
    checklist_collaborator_crud: CheckListCollaboratorCRUD = Depends(
        CheckListCollaboratorCRUD.get_crud
    ),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
) -> List[ShareRead]:
    collaborators = await checklist_collaborator_crud.list(
        checklist_id=checklist_access.checklist.id
    )
    result: List[ShareRead] = []
    for collab in collaborators:
        user = await user_crud.get(collab.user_id, show_deactivated=True)
        result.append(
            ShareRead(
                user_id=collab.user_id,
                user_name=user.user_name if user else None,
                display_name=user.display_name if user else None,
                permission=collab.permission,
            )
        )
    return result


@fast_api_checklist_share_router.put(
    "/checklist/{checklist_id}/shares/{user_id}",
    response_model=ShareRead,
    dependencies=[Depends(require_sharing_enabled)],
    description="Share the checklist with a user, or update an existing collaborator's permission. Owner only.",
)
async def upsert_share(
    user_id: uuid.UUID,
    body: ShareUpsertRequest,
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.owner)
    ),
    checklist_collaborator_crud: CheckListCollaboratorCRUD = Depends(
        CheckListCollaboratorCRUD.get_crud
    ),
    checklist_position_crud: CheckListPositionCRUD = Depends(
        CheckListPositionCRUD.get_crud
    ),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
) -> ShareRead:
    checklist_id = checklist_access.checklist.id
    if user_id == checklist_access.checklist.owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The owner already has full access and cannot be added as a collaborator.",
        )
    target = await user_crud.get(user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No user with id '{user_id}'.",
        )
    collab = await checklist_collaborator_crud.upsert(
        checklist_id=checklist_id,
        user_id=user_id,
        permission=body.permission,
    )
    await _ensure_position(checklist_id, user_id, checklist_position_crud)
    await sync_crud.create(
        SyncNotification(cl_id=checklist_id, upd_prop="share_added")
    )
    return ShareRead(
        user_id=collab.user_id,
        user_name=target.user_name,
        display_name=target.display_name,
        permission=collab.permission,
    )


@fast_api_checklist_share_router.delete(
    "/checklist/{checklist_id}/shares/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_sharing_enabled)],
    description="Revoke a user's access. The owner may revoke anyone; a collaborator may revoke themselves ('leave list').",
)
async def delete_share(
    user_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(user_has_checklist_access),
    checklist_collaborator_crud: CheckListCollaboratorCRUD = Depends(
        CheckListCollaboratorCRUD.get_crud
    ),
    checklist_position_crud: CheckListPositionCRUD = Depends(
        CheckListPositionCRUD.get_crud
    ),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
):
    checklist_id = checklist_access.checklist.id
    is_owner = checklist_access.user_is_owner()
    is_self = checklist_access.user.id == user_id
    if not (is_owner or is_self):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can revoke other users; you can only remove your own access.",
        )
    if user_id == checklist_access.checklist.owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The owner cannot be removed as a collaborator. Transfer ownership or delete the checklist instead.",
        )
    await checklist_collaborator_crud.delete(
        checklist_id=checklist_id, user_id=user_id
    )
    await checklist_position_crud.delete(checklist_id=checklist_id, user_id=user_id)
    await sync_crud.create(
        SyncNotification(cl_id=checklist_id, upd_prop="share_removed")
    )


@fast_api_checklist_share_router.post(
    "/checklist/{checklist_id}/transfer-ownership",
    response_model=TransferOwnershipResult,
    dependencies=[Depends(require_sharing_enabled)],
    description="Transfer ownership to another user. The previous owner is demoted to an 'edit' collaborator and keeps access. Owner only.",
)
async def transfer_ownership(
    body: TransferOwnershipRequest,
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.owner)
    ),
    checklist_crud: CheckListCRUD = Depends(CheckListCRUD.get_crud),
    checklist_collaborator_crud: CheckListCollaboratorCRUD = Depends(
        CheckListCollaboratorCRUD.get_crud
    ),
    checklist_position_crud: CheckListPositionCRUD = Depends(
        CheckListPositionCRUD.get_crud
    ),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
) -> TransferOwnershipResult:
    checklist_id = checklist_access.checklist.id
    old_owner_id = checklist_access.checklist.owner_id
    new_owner_id = body.new_owner_id

    if new_owner_id == old_owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That user is already the owner.",
        )
    new_owner = await user_crud.get(new_owner_id)
    if new_owner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No user with id '{new_owner_id}'.",
        )

    # Promote the new owner: they must have a position to see the card, and they
    # must no longer be listed as a collaborator (owner is not a collaborator).
    await _ensure_position(checklist_id, new_owner_id, checklist_position_crud)
    await checklist_collaborator_crud.delete(
        checklist_id=checklist_id, user_id=new_owner_id
    )
    await checklist_crud.set_owner(
        checklist_id=checklist_id, new_owner_id=new_owner_id
    )
    # Demote the previous owner to an 'edit' collaborator (keeps their access and
    # their existing position).
    await checklist_collaborator_crud.upsert(
        checklist_id=checklist_id,
        user_id=old_owner_id,
        permission=SharePermission.edit,
    )

    # A single notification reaches both parties: after the commit above the
    # target set (owner + collaborators) includes the new owner and the demoted
    # previous owner.
    await sync_crud.create(
        SyncNotification(cl_id=checklist_id, upd_prop="share_added")
    )
    return TransferOwnershipResult(
        checklist_id=checklist_id,
        new_owner_id=new_owner_id,
        previous_owner_id=old_owner_id,
    )
