"""Per-recipient share primitives, shared by the share routes and the living
group-share reconciler.

These were extracted from ``routes_checklist_share.py`` so that
``group_share_reconcile.py`` can reuse the exact same grant/remove mechanics
(invite gate, grid position, notifications, the hard-delete seq-advance) without a
circular import back into the routes module. The routes still own the HTTP-facing
authorization and the request/response schemas; this module owns the DB-facing
side effects of a single user gaining or losing access to one card.
"""

import decimal
import uuid

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

from checkcheckserver.model.checklist_collaborator import ShareStatus
from checkcheckserver.db.checklist_collaborator import CheckListCollaboratorCRUD
from checkcheckserver.db.checklist_position import (
    CheckListPositionCRUD,
    CheckListPositionCreate,
)
from checkcheckserver.db.sync_notification import SyncNotifiationCRUD
from checkcheckserver.model.sync_notifications import SyncNotification
from checkcheckserver.db.notification import NotificationCRUD, emit_notification
from checkcheckserver.model.notification import NotificationType

config = Config()
log = get_logger()


async def ensure_position(
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


async def grant_share_to_user(
    *,
    checklist_id: uuid.UUID,
    target_user_id: uuid.UUID,
    permission,
    notification_payload: dict,
    already_accepted: bool,
    checklist_collaborator_crud: CheckListCollaboratorCRUD,
    checklist_position_crud: CheckListPositionCRUD,
    sync_crud: SyncNotifiationCRUD,
    notification_crud: NotificationCRUD,
    via_group: "str | None" = None,
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

    ``via_group`` is the living-group-share provenance marker written onto the
    collaborator row: ``None`` for an explicit individual share, the group name for
    a materialized group grant (see ``CheckListCollaboratorCreate.via_group``).

    Returns ``True`` when this took the instant-add path (so the caller knows a
    broadcast ``share_added`` is warranted), ``False`` for an invite.
    """
    if config.SHARING_REQUIRE_INVITE_ACCEPT and not already_accepted:
        await checklist_collaborator_crud.upsert(
            checklist_id=checklist_id,
            user_id=target_user_id,
            permission=permission,
            status=ShareStatus.pending,
            via_group=via_group,
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
        via_group=via_group,
    )
    await ensure_position(checklist_id, target_user_id, checklist_position_crud)
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


async def remove_user_access(
    *,
    checklist_id: uuid.UUID,
    user_id: uuid.UUID,
    owner_id: uuid.UUID,
    checklist_collaborator_crud: CheckListCollaboratorCRUD,
    checklist_position_crud: CheckListPositionCRUD,
    sync_crud: SyncNotifiationCRUD,
    emit_removed_broadcast: bool = True,
) -> None:
    """Hard-remove one user's access to a card: drop their collaborator + position
    rows, advance the global seq so offline clients actually pull the removal, and
    pin a ``checklist_deleted`` poke to the removed user while telling the rest of
    the share set the set changed.

    Shared by ``delete_share`` (the owner/collaborator revoke route) and the group
    reconciler (a member who left a shared group / a revoked group share). The
    seq-advance-via-owner-position ``touch`` mirrors the reasoning documented in
    ``delete_share``: a hard delete leaves no ``server_seq`` trace, so the poke
    below would otherwise not be ahead of a local-first client's cursor and the
    card would linger on their board until a manual reload.

    ``emit_removed_broadcast`` controls the *broadcast* ``share_removed`` poke that
    fans out to the whole remaining share set. It stays ``True`` for a single
    removal (the delete route) so the set is told once. A bulk group revoke removes
    N members from the *same* card, so it passes ``False`` here and emits a single
    card-scoped ``share_removed`` itself — one poke instead of N. The per-user
    pinned ``checklist_deleted`` is always emitted; those must stay targeted.
    """
    await checklist_collaborator_crud.delete(
        checklist_id=checklist_id, user_id=user_id
    )
    await checklist_position_crud.delete(checklist_id=checklist_id, user_id=user_id)
    await checklist_position_crud.touch(checklist_id=checklist_id, user_id=owner_id)
    await sync_crud.create(
        SyncNotification(cl_id=checklist_id, upd_prop="checklist_deleted"),
        target_user_ids=[user_id],
    )
    if emit_removed_broadcast:
        await sync_crud.create(
            SyncNotification(cl_id=checklist_id, upd_prop="share_removed")
        )
