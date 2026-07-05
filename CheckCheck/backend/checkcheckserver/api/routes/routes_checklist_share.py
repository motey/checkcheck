"""Share-management API for checklists (Phase 3 of card sharing).

Lets an owner grant/revoke per-user access at a permission level, lets a
collaborator remove themselves ("leave list"), and lets an owner transfer
ownership. Public/anonymous URL sharing is a separate, deferred effort (see
docs/archive/CARD_SHARING_PLAN.md, "Phase 5 design (deferred)").
"""

import datetime
import uuid
import decimal
from typing import List, Optional, Annotated

from fastapi import APIRouter, Depends, Security, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

from checkcheckserver.db.user import User, UserCRUD
from checkcheckserver.db.user_auth import UserAuth
from checkcheckserver.api.auth.security import (
    get_current_user,
    get_current_user_auth,
    caller_restricted_to_own_groups,
)
from checkcheckserver.api.access import (
    user_has_checklist_access,
    require_checklist_permission,
    permission_at_least,
    attach_my_permission,
    ChecklistAccessLevel,
    UserChecklistAccess,
)
from checkcheckserver.model.checklist_collaborator import (
    CheckListCollaborator,
    SharePermission,
    ShareStatus,
)
from checkcheckserver.db.checklist_collaborator import CheckListCollaboratorCRUD
from checkcheckserver.db.checklist import CheckListCRUD
from checkcheckserver.db.checklist_label import ChecklistLabelCRUD
from checkcheckserver.model.checklist import CheckListApiWithSubObj
from checkcheckserver.db.checklist_position import (
    CheckListPositionCRUD,
    CheckListPositionCreate,
)
from checkcheckserver.model.checklist_public_share import (
    CheckListPublicShare,
    CheckListPublicShareCreate,
)
from checkcheckserver.db.checklist_public_share import CheckListPublicShareCRUD
from checkcheckserver.api.share_password import hash_share_password
from checkcheckserver.db.sync_notification import SyncNotifiationCRUD
from checkcheckserver.model.sync_notifications import SyncNotification
from checkcheckserver.db.notification import NotificationCRUD, emit_notification
from checkcheckserver.model.notification import NotificationType

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


async def require_public_links_enabled() -> None:
    """Disable the public-link endpoints when either the master sharing switch or
    the public-links switch is off."""
    if not (config.SHARING_ENABLED and config.SHARING_PUBLIC_LINKS_ENABLED):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Public share links are disabled on this server.",
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
    status: ShareStatus = Field(
        default=ShareStatus.accepted,
        description="Whether the share is live ('accepted'), an unaccepted invite ('pending'), or declined.",
    )


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


async def _grant_share_to_user(
    *,
    checklist_id: uuid.UUID,
    target_user_id: uuid.UUID,
    permission: SharePermission,
    notification_payload: dict,
    already_accepted: bool,
    checklist_collaborator_crud: CheckListCollaboratorCRUD,
    checklist_position_crud: CheckListPositionCRUD,
    sync_crud: SyncNotifiationCRUD,
    notification_crud: NotificationCRUD,
) -> bool:
    """Grant (or raise) one user's share, honouring ``SHARING_REQUIRE_INVITE_ACCEPT``.

    Does only the **per-recipient** side effects: the collaborator upsert, the
    grid position (instant-add only), the pinned invite nudge, and the in-app
    notification. The broadcast ``share_added`` SSE (which fans out to the whole
    share set) is intentionally left to the caller so a bulk group-share can emit
    it exactly once.

    ``already_accepted`` is the caller's pre-read of whether the target is already
    a live collaborator — when so, the invite gate is bypassed (re-arming an invite
    must never revoke live access) and no fresh ``card_shared`` notification fires
    for a mere level change.

    Returns ``True`` when this took the instant-add path (so the caller knows a
    broadcast ``share_added`` is warranted), ``False`` for an invite.
    """
    if config.SHARING_REQUIRE_INVITE_ACCEPT and not already_accepted:
        await checklist_collaborator_crud.upsert(
            checklist_id=checklist_id,
            user_id=target_user_id,
            permission=permission,
            status=ShareStatus.pending,
        )
        await sync_crud.create(
            SyncNotification(cl_id=checklist_id, upd_prop="share_invited"),
            target_user_ids=[target_user_id],
        )
        await emit_notification(
            notification_crud,
            sync_crud,
            user_id=target_user_id,
            type=NotificationType.card_invited,
            cl_id=checklist_id,
            payload=notification_payload,
        )
        return False

    await checklist_collaborator_crud.upsert(
        checklist_id=checklist_id,
        user_id=target_user_id,
        permission=permission,
        status=ShareStatus.accepted,
    )
    await _ensure_position(checklist_id, target_user_id, checklist_position_crud)
    # Don't re-notify on a no-op level change of an already-accepted collaborator;
    # only a genuinely new grant produces a 'card_shared' notification.
    if not already_accepted:
        await emit_notification(
            notification_crud,
            sync_crud,
            user_id=target_user_id,
            type=NotificationType.card_shared,
            cl_id=checklist_id,
            payload=notification_payload,
        )
    return True


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
                status=collab.status,
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
    notification_crud: NotificationCRUD = Depends(NotificationCRUD.get_crud),
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

    existing = await checklist_collaborator_crud.get_one(
        checklist_id=checklist_id, user_id=user_id
    )
    already_accepted = existing is not None and existing.status == ShareStatus.accepted

    # Actor context for the in-app notification — the owner doing the sharing. No
    # secrets here; just enough to render "X shared 'Card' with you".
    actor = checklist_access.user
    notification_payload = {
        "actor_id": str(actor.id),
        "actor_user_name": actor.user_name,
        "actor_display_name": actor.display_name,
        "checklist_name": checklist_access.checklist.name,
    }

    # Invite mode: a *new* share (or one still pending / previously declined) goes
    # out as a pending invite (no grid position until accepted); an already-accepted
    # collaborator just has their level updated. Instant-add mode grants access
    # immediately. The per-recipient work lives in the shared helper; the broadcast
    # 'share_added' below is emitted only when access actually changed live.
    instant_added = await _grant_share_to_user(
        checklist_id=checklist_id,
        target_user_id=user_id,
        permission=body.permission,
        notification_payload=notification_payload,
        already_accepted=already_accepted,
        checklist_collaborator_crud=checklist_collaborator_crud,
        checklist_position_crud=checklist_position_crud,
        sync_crud=sync_crud,
        notification_crud=notification_crud,
    )
    if instant_added:
        await sync_crud.create(
            SyncNotification(cl_id=checklist_id, upd_prop="share_added")
        )

    collab = await checklist_collaborator_crud.get_one(
        checklist_id=checklist_id, user_id=user_id
    )
    return ShareRead(
        user_id=collab.user_id,
        user_name=target.user_name,
        display_name=target.display_name,
        permission=collab.permission,
        status=collab.status,
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
    # The removed user must drop the card from their view. Their collaborator row
    # is already gone, so pin them explicitly — dynamic resolution would skip the
    # one person who most needs to hear about it.
    await sync_crud.create(
        SyncNotification(cl_id=checklist_id, upd_prop="checklist_deleted"),
        target_user_ids=[user_id],
    )
    # Owner + remaining collaborators just see the share set change.
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


# ── Group (OIDC group) sharing — Phase 10 ────────────────────────────────────
#
# Share a card with everyone in an OIDC group in one call. Membership is
# *snapshotted* at share time: the group is expanded to its current members and an
# ordinary CheckListCollaborator row is created per member at the chosen level
# (reusing all of the per-user machinery, so the invite gate applies automatically
# when SHARING_REQUIRE_INVITE_ACCEPT is on). New members who join the group later
# do NOT auto-gain access — re-run the share. Local users have no groups.


class GroupShareRequest(BaseModel):
    permission: SharePermission = Field(
        description="Permission level to grant every member of the group.",
    )


class GroupShareResult(BaseModel):
    """Summary of a group share — how many members were (re)granted vs skipped."""

    group: str
    permission: SharePermission
    total_members: int = Field(
        description="Group members resolved for this share, excluding the card owner.",
    )
    added: int = Field(
        description="Members newly shared/invited or raised to the chosen level.",
    )
    skipped: int = Field(
        description="Members skipped because they already held an equal or higher level.",
    )


@fast_api_checklist_share_router.get(
    "/user/me/groups",
    response_model=List[str],
    dependencies=[Depends(require_sharing_enabled)],
    description="List the current user's own OIDC groups (for a group-share picker). Empty for local users.",
)
async def list_my_groups(
    current_user: User = Security(get_current_user),
) -> List[str]:
    return current_user.oidc_groups or []


@fast_api_checklist_share_router.put(
    "/checklist/{checklist_id}/shares/group/{group}",
    response_model=GroupShareResult,
    dependencies=[Depends(require_sharing_enabled)],
    description=(
        "Share the checklist with every member of an OIDC group at the given "
        "permission. Snapshots membership now (later joiners are not auto-added). "
        "Skips the owner and anyone already at an equal or higher level (never "
        "downgrades). Honours the invite gate. Owner only."
    ),
)
async def share_with_group(
    group: str,
    body: GroupShareRequest,
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.owner)
    ),
    current_user_auth: UserAuth = Depends(get_current_user_auth),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
    checklist_collaborator_crud: CheckListCollaboratorCRUD = Depends(
        CheckListCollaboratorCRUD.get_crud
    ),
    checklist_position_crud: CheckListPositionCRUD = Depends(
        CheckListPositionCRUD.get_crud
    ),
    sync_crud: SyncNotifiationCRUD = Depends(SyncNotifiationCRUD.get_crud),
    notification_crud: NotificationCRUD = Depends(NotificationCRUD.get_crud),
) -> GroupShareResult:
    checklist_id = checklist_access.checklist.id
    owner_id = checklist_access.checklist.owner_id

    # Group scoping: a caller from an OIDC provider configured to restrict search
    # to own groups may only target a group they themselves belong to (the same
    # rule user-search applies). Local / unrestricted callers may target any group.
    if caller_restricted_to_own_groups(current_user_auth):
        if group not in (checklist_access.user.oidc_groups or []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only share with groups you belong to.",
            )

    members = await user_crud.find_by_oidc_group(group)

    actor = checklist_access.user
    notification_payload = {
        "actor_id": str(actor.id),
        "actor_user_name": actor.user_name,
        "actor_display_name": actor.display_name,
        "checklist_name": checklist_access.checklist.name,
    }

    total = 0
    added = 0
    skipped = 0
    any_instant_add = False
    for member in members:
        if member.id == owner_id:
            # The owner already has full access — never a collaborator.
            continue
        total += 1
        existing = await checklist_collaborator_crud.get_one(
            checklist_id=checklist_id, user_id=member.id
        )
        already_accepted = (
            existing is not None and existing.status == ShareStatus.accepted
        )
        # Idempotent / never-downgrade: a live collaborator already at the chosen
        # level or higher is left untouched (mirrors the public-link join).
        if already_accepted and permission_at_least(
            existing.permission, body.permission
        ):
            skipped += 1
            continue
        instant = await _grant_share_to_user(
            checklist_id=checklist_id,
            target_user_id=member.id,
            permission=body.permission,
            notification_payload=notification_payload,
            already_accepted=already_accepted,
            checklist_collaborator_crud=checklist_collaborator_crud,
            checklist_position_crud=checklist_position_crud,
            sync_crud=sync_crud,
            notification_crud=notification_crud,
        )
        any_instant_add = any_instant_add or instant
        added += 1

    # One broadcast for the whole batch (the share set changed). In invite mode no
    # access changed live, so the only nudges are the per-invitee pinned ones.
    if any_instant_add:
        await sync_crud.create(
            SyncNotification(cl_id=checklist_id, upd_prop="share_added")
        )

    return GroupShareResult(
        group=group,
        permission=body.permission,
        total_members=total,
        added=added,
        skipped=skipped,
    )


# ── Invite inbox & actions (Phase 8) ─────────────────────────────────────────
#
# Only relevant when SHARING_REQUIRE_INVITE_ACCEPT is on (else shares are
# instant-add and no pending rows ever exist — the inbox is simply always empty).
# These are authenticated as the *invitee* (any logged-in user acting on their own
# invites), not the owner.


class InviteRead(BaseModel):
    """A pending invite as seen by the invitee — the card plus who invited them
    (the card's owner), enough to render an inbox row."""

    checklist_id: uuid.UUID
    checklist_name: Optional[str] = None
    permission: SharePermission
    inviter_id: uuid.UUID
    inviter_user_name: Optional[str] = None
    inviter_display_name: Optional[str] = None
    created_at: datetime.datetime


@fast_api_checklist_share_router.get(
    "/user/me/invites",
    response_model=List[InviteRead],
    dependencies=[Depends(require_sharing_enabled)],
    description="List the current user's pending share invites (cards shared with them awaiting accept/decline).",
)
async def list_my_invites(
    current_user: User = Security(get_current_user),
    checklist_collaborator_crud: CheckListCollaboratorCRUD = Depends(
        CheckListCollaboratorCRUD.get_crud
    ),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
) -> List[InviteRead]:
    pending = await checklist_collaborator_crud.list_pending_for_user(
        user_id=current_user.id
    )
    result: List[InviteRead] = []
    for collab, checklist in pending:
        inviter = await user_crud.get(checklist.owner_id, show_deactivated=True)
        result.append(
            InviteRead(
                checklist_id=checklist.id,
                checklist_name=checklist.name,
                permission=collab.permission,
                inviter_id=checklist.owner_id,
                inviter_user_name=inviter.user_name if inviter else None,
                inviter_display_name=inviter.display_name if inviter else None,
                created_at=collab.created_at,
            )
        )
    return result


async def _get_own_pending_invite(
    checklist_id: uuid.UUID,
    user_id: uuid.UUID,
    checklist_collaborator_crud: CheckListCollaboratorCRUD,
) -> CheckListCollaborator:
    """Load the caller's pending invite for this checklist, or 404. Returns 404
    (not 403) for a non-pending / missing row so it never reveals whether the card
    exists to someone who was never invited."""
    collab = await checklist_collaborator_crud.get_one(
        checklist_id=checklist_id, user_id=user_id
    )
    if collab is None or collab.status != ShareStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You have no pending invite for this checklist.",
        )
    return collab


@fast_api_checklist_share_router.post(
    "/checklist/{checklist_id}/invites/accept",
    response_model=CheckListApiWithSubObj,
    dependencies=[Depends(require_sharing_enabled)],
    description="Accept a pending invite: gain access, the card appears in your grid. Returns the card scoped to you.",
)
async def accept_invite(
    checklist_id: uuid.UUID,
    current_user: User = Security(get_current_user),
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
    invite = await _get_own_pending_invite(
        checklist_id, current_user.id, checklist_collaborator_crud
    )
    await checklist_collaborator_crud.set_status(
        checklist_id=checklist_id,
        user_id=current_user.id,
        status=ShareStatus.accepted,
    )
    await _ensure_position(checklist_id, current_user.id, checklist_position_crud)
    # Now an accepted collaborator: owner + collaborators (incl. the new joiner)
    # see the share set change, exactly like an instant-add share.
    await sync_crud.create(
        SyncNotification(cl_id=checklist_id, upd_prop="share_added")
    )

    checklist = await checklist_crud.get(id_=checklist_id)
    user_position = await checklist_position_crud.get(
        checklist_id=checklist_id, user_id=current_user.id
    )
    if user_position is not None:
        checklist.position = user_position
    checklist.labels = await checklist_label_crud.list_labels_for_user(
        checklist_id=checklist_id, user_id=current_user.id
    )
    # Just accepted -> the caller now holds the invite's granted level.
    attach_my_permission(checklist, invite.permission)
    return checklist


@fast_api_checklist_share_router.post(
    "/checklist/{checklist_id}/invites/decline",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_sharing_enabled)],
    description="Decline a pending invite. No access is granted; the declined row is kept (the owner can re-invite).",
)
async def decline_invite(
    checklist_id: uuid.UUID,
    current_user: User = Security(get_current_user),
    checklist_collaborator_crud: CheckListCollaboratorCRUD = Depends(
        CheckListCollaboratorCRUD.get_crud
    ),
):
    await _get_own_pending_invite(
        checklist_id, current_user.id, checklist_collaborator_crud
    )
    # Keep the row as 'declined' (rather than deleting): it lets the UI show "you
    # previously declined" and lets the owner re-invite (which re-arms it to
    # pending via upsert_share). No position is created.
    await checklist_collaborator_crud.set_status(
        checklist_id=checklist_id,
        user_id=current_user.id,
        status=ShareStatus.declined,
    )


# ── Public URL links (Phase 5) ───────────────────────────────────────────────
#
# Owner-only management of anonymous share links. The anonymous *consumption*
# surface lives in routes_checklist_public.py. The token is a capability — it is
# returned exactly once (on create) and never echoed by the list endpoint.


def _to_naive_utc(
    value: Optional[datetime.datetime],
) -> Optional[datetime.datetime]:
    """Normalise an incoming ``expires_at`` to naive UTC.

    The model stores expiry as a naive-UTC ``TIMESTAMP WITHOUT TIME ZONE`` and the
    resolver compares it against a naive ``utcnow()``. A client (e.g. JS
    ``Date.toISOString()``) typically sends a tz-aware value like
    ``"2999-01-01T00:00:00Z"``; left as-is that both fails the asyncpg insert on
    Postgres (tz-aware into a tz-naive column → 500) and would make the resolver's
    naive/aware comparison raise. Convert to UTC and drop the tzinfo so storage
    and comparison stay consistent across backends.
    """
    if value is not None and value.tzinfo is not None:
        return value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value


class PublicLinkCreateRequest(BaseModel):
    permission: SharePermission = Field(
        default=SharePermission.view,
        description="What an anonymous visitor holding this link may do.",
    )
    expires_at: Optional[datetime.datetime] = Field(
        default=None,
        description="Optional naive-UTC expiry. Null = never expires.",
    )
    password: Optional[str] = Field(
        default=None,
        description=(
            "Optional passphrase guarding this link. When set, a visitor must "
            "unlock it before the link resolves. Null = no passphrase. Never "
            "echoed back — only 'password_protected' is exposed."
        ),
    )

    _normalize_expires_at = field_validator("expires_at")(
        staticmethod(_to_naive_utc)
    )


class PublicLinkUpdateRequest(BaseModel):
    permission: Optional[SharePermission] = None
    enabled: Optional[bool] = None
    expires_at: Optional[datetime.datetime] = Field(
        default=None,
        description="Set to null explicitly to clear the expiry.",
    )
    password: Optional[str] = Field(
        default=None,
        description=(
            "Set a string to (re)protect the link with a passphrase; send an "
            "explicit null to clear protection. Omit to leave it unchanged."
        ),
    )

    _normalize_expires_at = field_validator("expires_at")(
        staticmethod(_to_naive_utc)
    )


class PublicLinkRead(BaseModel):
    """A public link *without* its token or passphrase (safe to list)."""

    id: uuid.UUID
    checklist_id: uuid.UUID
    permission: SharePermission
    enabled: bool
    expires_at: Optional[datetime.datetime] = None
    password_protected: bool = Field(
        description="Whether a passphrase must be supplied before this link resolves.",
    )
    created_at: datetime.datetime


class PublicLinkCreateResult(PublicLinkRead):
    """Returned only by create — the one and only time the token is exposed."""

    token: str = Field(
        description="The secret capability. Shown once; store it now — it is never returned again.",
    )


def _to_public_link_read(link: CheckListPublicShare) -> PublicLinkRead:
    return PublicLinkRead(
        id=link.id,
        checklist_id=link.checklist_id,
        permission=link.permission,
        enabled=link.enabled,
        expires_at=link.expires_at,
        password_protected=link.password_hash is not None,
        created_at=link.created_at,
    )


async def _get_owned_link_or_404(
    link_id: uuid.UUID,
    checklist_id: uuid.UUID,
    public_share_crud: CheckListPublicShareCRUD,
) -> CheckListPublicShare:
    """Load a link and confirm it belongs to the checklist in the path. 404 if
    the link does not exist or belongs to a different card (no cross-card leak)."""
    link = await public_share_crud.get(id_=link_id)
    if link is None or link.checklist_id != checklist_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No public link '{link_id}' on this checklist.",
        )
    return link


@fast_api_checklist_share_router.post(
    "/checklist/{checklist_id}/public-links",
    response_model=PublicLinkCreateResult,
    dependencies=[Depends(require_public_links_enabled)],
    description="Create a public share link for the checklist. Returns the secret token once. Owner only.",
)
async def create_public_link(
    body: PublicLinkCreateRequest,
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.owner)
    ),
    public_share_crud: CheckListPublicShareCRUD = Depends(
        CheckListPublicShareCRUD.get_crud
    ),
) -> PublicLinkCreateResult:
    link = await public_share_crud.create(
        CheckListPublicShareCreate(
            checklist_id=checklist_access.checklist.id,
            permission=body.permission,
            expires_at=body.expires_at,
            password_hash=(
                hash_share_password(body.password)
                if body.password
                else None
            ),
            created_by=checklist_access.user.id,
        )
    )
    return PublicLinkCreateResult(
        token=link.token,
        **_to_public_link_read(link).model_dump(),
    )


@fast_api_checklist_share_router.get(
    "/checklist/{checklist_id}/public-links",
    response_model=List[PublicLinkRead],
    dependencies=[Depends(require_public_links_enabled)],
    description="List the checklist's public share links. Tokens are NOT included. Owner only.",
)
async def list_public_links(
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.owner)
    ),
    public_share_crud: CheckListPublicShareCRUD = Depends(
        CheckListPublicShareCRUD.get_crud
    ),
) -> List[PublicLinkRead]:
    links = await public_share_crud.list_for_checklist(
        checklist_id=checklist_access.checklist.id
    )
    return [_to_public_link_read(link) for link in links]


@fast_api_checklist_share_router.patch(
    "/checklist/{checklist_id}/public-links/{link_id}",
    response_model=PublicLinkRead,
    dependencies=[Depends(require_public_links_enabled)],
    description="Update a public link (enable/disable, change level or expiry). Owner only.",
)
async def update_public_link(
    link_id: uuid.UUID,
    body: PublicLinkUpdateRequest,
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.owner)
    ),
    public_share_crud: CheckListPublicShareCRUD = Depends(
        CheckListPublicShareCRUD.get_crud
    ),
) -> PublicLinkRead:
    link = await _get_owned_link_or_404(
        link_id, checklist_access.checklist.id, public_share_crud
    )
    # The base update applies model_dump(exclude_unset=True), which distinguishes
    # "not provided" from an explicit null (e.g. clearing expires_at), so only the
    # fields the client actually sent are applied. ``password`` is not a column on
    # the model, so the base update ignores it — it is handled separately below so
    # the plaintext is hashed (and never written through verbatim).
    updated = await public_share_crud.update(update_obj=body, id_=link.id)
    if "password" in body.model_fields_set:
        # Explicit string -> (re)protect; explicit null -> clear protection.
        new_hash = (
            hash_share_password(body.password) if body.password else None
        )
        updated = await public_share_crud.set_password_hash(link.id, new_hash)
    return _to_public_link_read(updated)


@fast_api_checklist_share_router.delete(
    "/checklist/{checklist_id}/public-links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_public_links_enabled)],
    description="Revoke (delete) a public link. Owner only.",
)
async def delete_public_link(
    link_id: uuid.UUID,
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.owner)
    ),
    public_share_crud: CheckListPublicShareCRUD = Depends(
        CheckListPublicShareCRUD.get_crud
    ),
):
    link = await _get_owned_link_or_404(
        link_id, checklist_access.checklist.id, public_share_crud
    )
    await public_share_crud.delete(id_=link.id)
