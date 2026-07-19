"""Living group-share reconciliation.

A ``CheckListGroupShare`` is the source of truth for "card C is shared with OIDC
group G at level L". Because the grid, counts, delta feed and access checks all
key off per-user ``CheckListCollaborator`` + ``CheckListPosition`` rows (see
``_add_user_has_access_query``), group access is realized by **materializing**
those rows for current members and **reconciling** them whenever membership can
change:

* on **login** — ``reconcile_user`` — the one moment a user's OIDC group set is
  re-read (``routes_auth`` rewrites ``user.oidc_groups``); a user who joined a
  shared group gains the card, one who left loses it;
* on group-share **create/update** — ``reconcile_group_share`` — the owner's
  action takes effect immediately for the group's current members;
* on group-share **revoke** — ``reconcile_group_share(remove_group=…)`` — members
  who only had access via that group lose it; individual (explicit) shares stay.

Provenance is tracked by ``CheckListCollaborator.via_group``: ``None`` = an
explicit individual share (authoritative — never touched here); a group name =
a row this reconciler owns. **Explicit wins**: a member holding an explicit row
is left exactly as the owner set it (no group upgrade or downgrade).

The per-recipient mechanics (invite gate, grid position, notifications, the
hard-delete seq-advance) are reused from ``api/share_ops.py`` so group access
behaves identically to individual sharing. See
``docs/plans/GROUP_SHARE_LIVING_MEMBERSHIP.md``.
"""

from typing import Optional, Sequence

from checkcheckserver.log import get_logger

from checkcheckserver.api.access import permission_at_least
from checkcheckserver.model.checklist import CheckList
from checkcheckserver.model.checklist_collaborator import SharePermission, ShareStatus
from checkcheckserver.model.checklist_group_share import CheckListGroupShare
from checkcheckserver.db.user import User, UserCRUD
from checkcheckserver.db.checklist import CheckListCRUD
from checkcheckserver.db.checklist_collaborator import CheckListCollaboratorCRUD
from checkcheckserver.db.checklist_group_share import CheckListGroupShareCRUD
from checkcheckserver.db.checklist_position import CheckListPositionCRUD
from checkcheckserver.db.sync_notification import SyncNotifiationCRUD
from checkcheckserver.model.sync_notifications import SyncNotification
from checkcheckserver.db.notification import NotificationCRUD
from checkcheckserver.api.share_ops import grant_share_to_user, remove_user_access

log = get_logger()


def _max_permission(perms: Sequence[SharePermission]) -> Optional[SharePermission]:
    """Highest level in ``perms`` on the view<check<edit ladder, or None if empty."""
    best: Optional[SharePermission] = None
    for p in perms:
        if best is None or permission_at_least(p, best):
            best = p
    return best


def _effective_group_level(
    user: User,
    shares_on_checklist: Sequence[CheckListGroupShare],
) -> Optional[SharePermission]:
    """The level a user would get purely from group shares on one checklist: the
    max over every share whose group is in the user's current OIDC groups. None if
    no group share applies to them."""
    user_groups = set(user.oidc_groups or [])
    matching = [s for s in shares_on_checklist if s.group in user_groups]
    if not matching:
        return None
    return _max_permission([s.permission for s in matching])


def _contributing_group(
    user: User,
    shares_on_checklist: Sequence[CheckListGroupShare],
    level: SharePermission,
) -> Optional[str]:
    """A group in the user's set whose share grants exactly ``level`` — stored on
    the collaborator row as the ``via_group`` marker (informational; the reconciler
    always recomputes from all shares, so which one is picked does not matter)."""
    user_groups = set(user.oidc_groups or [])
    for s in shares_on_checklist:
        if s.group in user_groups and s.permission == level:
            return s.group
    return None


async def _load_owner(
    checklist: CheckList,
    user_crud: UserCRUD,
) -> Optional[User]:
    """The card's owner as a User (for notification attribution), or None."""
    return await user_crud.get(checklist.owner_id, show_deactivated=True)


def _owner_notification_payload(checklist: CheckList, owner: Optional[User]) -> dict:
    """Attribute a group-materialized grant to the card owner (the only one who can
    share). Mirrors the payload shape ``upsert_share`` builds."""
    return {
        "actor_id": str(owner.id) if owner else None,
        "actor_user_name": owner.user_name if owner else None,
        "actor_display_name": owner.display_name if owner else None,
        "checklist_name": checklist.name,
    }


async def _reconcile_pair(
    *,
    checklist: CheckList,
    user: User,
    shares_on_checklist: Sequence[CheckListGroupShare],
    notification_payload: dict,
    checklist_collaborator_crud: CheckListCollaboratorCRUD,
    checklist_position_crud: CheckListPositionCRUD,
    sync_crud: SyncNotifiationCRUD,
    notification_crud: NotificationCRUD,
) -> str:
    """Make one user's *group-derived* access to one card match the current group
    shares. Returns one of ``"instant"``, ``"invited"``, ``"removed"``,
    ``"unchanged"`` (the caller aggregates a single ``share_added`` broadcast from
    the instant/​invited results)."""
    # The owner is never a collaborator.
    if user.id == checklist.owner_id:
        return "unchanged"

    existing = await checklist_collaborator_crud.get_one(
        checklist_id=checklist.id, user_id=user.id
    )
    # Explicit individual shares are authoritative — leave them exactly as set.
    if existing is not None and existing.via_group is None:
        return "unchanged"

    level = _effective_group_level(user, shares_on_checklist)

    if level is None:
        # No group grants this user access any more. Remove a stale group-derived
        # row (existing here is necessarily group-derived — explicit was handled).
        if existing is not None:
            await remove_user_access(
                checklist_id=checklist.id,
                user_id=user.id,
                owner_id=checklist.owner_id,
                checklist_collaborator_crud=checklist_collaborator_crud,
                checklist_position_crud=checklist_position_crud,
                sync_crud=sync_crud,
            )
            return "removed"
        return "unchanged"

    # A group grants access at `level`. Group-derived rows track the level exactly
    # (up or down); only an explicit row would be left alone (handled above).
    if existing is not None and existing.permission == level:
        return "unchanged"

    already_accepted = existing is not None and existing.status == ShareStatus.accepted
    instant = await grant_share_to_user(
        checklist_id=checklist.id,
        target_user_id=user.id,
        permission=level,
        notification_payload=notification_payload,
        already_accepted=already_accepted,
        checklist_collaborator_crud=checklist_collaborator_crud,
        checklist_position_crud=checklist_position_crud,
        sync_crud=sync_crud,
        notification_crud=notification_crud,
        via_group=_contributing_group(user, shares_on_checklist, level),
    )
    return "instant" if instant else "invited"


async def reconcile_group_share(
    *,
    checklist: CheckList,
    group: str,
    user_crud: UserCRUD,
    checklist_group_share_crud: CheckListGroupShareCRUD,
    checklist_collaborator_crud: CheckListCollaboratorCRUD,
    checklist_position_crud: CheckListPositionCRUD,
    sync_crud: SyncNotifiationCRUD,
    notification_crud: NotificationCRUD,
    owner: Optional[User] = None,
) -> dict:
    """Reconcile every current member of ``group`` against the card's group shares,
    after an owner created/updated/revoked the share for ``group``. Call this
    *after* the ``CheckListGroupShare`` row has been written (or deleted): it reads
    the card's remaining group shares to recompute each member's level.

    Returns ``{total, added, skipped}`` for the endpoint's summary toast (``added``
    = members newly granted/invited or re-levelled by this call; ``skipped`` =
    members left unchanged, e.g. an explicit share or an already-correct level)."""
    shares_on_checklist = await checklist_group_share_crud.list_for_checklist(
        checklist_id=checklist.id
    )
    members = await user_crud.find_by_oidc_group(group)
    payload = _owner_notification_payload(checklist, owner)

    total = 0
    added = 0
    skipped = 0
    any_instant = False
    for member in members:
        if member.id == checklist.owner_id:
            continue
        total += 1
        outcome = await _reconcile_pair(
            checklist=checklist,
            user=member,
            shares_on_checklist=shares_on_checklist,
            notification_payload=payload,
            checklist_collaborator_crud=checklist_collaborator_crud,
            checklist_position_crud=checklist_position_crud,
            sync_crud=sync_crud,
            notification_crud=notification_crud,
        )
        if outcome in ("instant", "invited"):
            added += 1
            any_instant = any_instant or outcome == "instant"
        else:
            skipped += 1

    if any_instant:
        await sync_crud.create(
            SyncNotification(cl_id=checklist.id, upd_prop="share_added")
        )
    return {"total": total, "added": added, "skipped": skipped}


async def reconcile_user(
    *,
    user: User,
    checklist_crud: CheckListCRUD,
    checklist_group_share_crud: CheckListGroupShareCRUD,
    checklist_collaborator_crud: CheckListCollaboratorCRUD,
    checklist_position_crud: CheckListPositionCRUD,
    sync_crud: SyncNotifiationCRUD,
    notification_crud: NotificationCRUD,
    user_crud: UserCRUD,
) -> None:
    """Reconcile one user's group-derived access across all cards — called on login
    once their OIDC group set has been (re)written. Grants cards shared with groups
    they are now in (that they don't already hold explicitly) and removes
    group-derived rows for groups they have left.

    Pre-existing snapshot shares from before living membership are plain
    collaborator rows with ``via_group IS NULL`` → treated as explicit → never
    touched here, so this is a safe no-op for already-shared users.
    """
    user_groups = list(user.oidc_groups or [])
    # Group shares targeting any group the user is currently in.
    desired_shares = await checklist_group_share_crud.list_for_groups(user_groups)
    # Cards where the user currently holds a group-derived row (may now be stale).
    stale_rows = await checklist_collaborator_crud.list_group_derived_for_user(
        user_id=user.id
    )

    checklist_ids = {s.checklist_id for s in desired_shares} | {
        r.checklist_id for r in stale_rows
    }
    if not checklist_ids:
        return

    for checklist_id in checklist_ids:
        checklist = await checklist_crud.get(id_=checklist_id, include_deleted=True)
        if checklist is None or checklist.deleted_at is not None:
            continue
        shares_on_checklist = [
            s for s in desired_shares if s.checklist_id == checklist_id
        ]
        owner = await _load_owner(checklist, user_crud)
        await _reconcile_pair(
            checklist=checklist,
            user=user,
            shares_on_checklist=shares_on_checklist,
            notification_payload=_owner_notification_payload(checklist, owner),
            checklist_collaborator_crud=checklist_collaborator_crud,
            checklist_position_crud=checklist_position_crud,
            sync_crud=sync_crud,
            notification_crud=notification_crud,
        )
        # No batch broadcast needed: each grant already inserts a position +
        # collaborator row (advancing the global seq) and emits a per-recipient
        # 'card_shared' poke to this user, and each removal pins its own
        # 'checklist_deleted'. The user's next pull picks all of it up.
