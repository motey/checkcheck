import datetime
import uuid
import enum
from typing import List, Literal, Annotated, Optional
from fastapi import HTTPException, status, Security, Depends, Path, Query, Header

# internal imports
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

from checkcheckserver.db.user import User
from checkcheckserver.api.auth.security import (
    user_is_admin,
    user_is_usermanager,
    get_current_user,
)
from checkcheckserver.config import Config
from checkcheckserver.model.checklist_collaborator import (
    CheckListCollaborator,
    ShareStatus,
)
from checkcheckserver.db.checklist_collaborator import CheckListCollaboratorCRUD
from checkcheckserver.model.checklist import CheckList
from checkcheckserver.db.checklist import CheckListCRUD
from checkcheckserver.model.checklist_public_share import CheckListPublicShare
from checkcheckserver.db.checklist_public_share import CheckListPublicShareCRUD
from checkcheckserver.api.share_password import verify_share_grant
from checkcheckserver.db.sync_notification import SyncNotifiationCRUD
from checkcheckserver.db.notification import NotificationCRUD, emit_notification
from checkcheckserver.model.notification import NotificationType

from checkcheckserver.model.checklist_item import CheckListItem
from checkcheckserver.db.checklist_item import CheckListItemCRUD


config = Config()

from checkcheckserver.log import get_logger

log = get_logger()


class ChecklistAccessLevel(str, enum.Enum):
    """The full authorization ladder for a checklist, ordered weakest -> strongest.

    Mirrors the grantable ``SharePermission`` levels (view/check/edit) and adds
    ``owner`` on top. ``owner`` is not a stored collaborator value — it is derived
    from ``checklist.owner_id`` — but lives on the same ladder so a single
    comparison covers owner-only operations.
    """

    view = "view"
    check = "check"
    edit = "edit"
    owner = "owner"


# weakest -> strongest, by declaration order
_PERMISSION_RANK: dict[ChecklistAccessLevel, int] = {
    level: rank for rank, level in enumerate(ChecklistAccessLevel)
}


def attach_my_permission(checklist: CheckList, level) -> CheckList:
    """Attach the caller's effective permission (P0.1) onto a checklist ORM object
    so it serialises as ``CheckListApiWithSubObj.my_permission``.

    ``my_permission`` is per-caller, not a stored column, so every route returning
    a ``CheckListApiWithSubObj`` sets it for the current caller via this helper.
    ``level`` may be a ``ChecklistAccessLevel``, a ``SharePermission``, or their
    plain string value — all are normalised to the ladder's string value.

    Written straight into the instance ``__dict__``: ``my_permission`` is not a
    mapped column, and SQLModel's Pydantic ``__setattr__`` rejects assigning a
    non-field attribute. The response serialiser reads it back via ``getattr``
    (``from_attributes``), and SQLAlchemy's unit of work ignores the unmapped key."""
    checklist.__dict__["my_permission"] = ChecklistAccessLevel(level).value
    return checklist


def permission_at_least(have, want) -> bool:
    """Compare two permission levels on the shared view<check<edit<owner ladder.

    Accepts ``SharePermission`` or ``ChecklistAccessLevel`` values (the former's
    values are a subset of the latter's). Returns True when ``have`` is at least
    as strong as ``want`` — used by bulk group sharing to skip members who already
    hold an equal-or-higher grant (never downgrade)."""
    return (
        _PERMISSION_RANK[ChecklistAccessLevel(have)]
        >= _PERMISSION_RANK[ChecklistAccessLevel(want)]
    )


class AnonymousPrincipal:
    """Stand-in 'user' for a visitor who arrived via a public share link. It has
    no DB ``id`` (it is not a ``User``); its authority comes entirely from the
    capability token it carries, captured here for sync routing. Using it as the
    ``UserChecklistAccess.user`` lets the existing permission guards run unchanged
    — an id-based owner/collaborator comparison simply never matches."""

    id = None  # never equals a real owner_id / collaborator user_id

    def __init__(self, token: str):
        self.token = token


class UserChecklistAccess:

    def __init__(
        self,
        user: User | AnonymousPrincipal,
        checklist: CheckList,
        collaborators: List[CheckListCollaborator] = [],
        public_level: "ChecklistAccessLevel | None" = None,
    ):
        self.user = user
        self.checklist = checklist
        self.collaborators = collaborators
        # When set, access is granted by a public link at this level (capped at
        # 'edit' — never 'owner'); it overrides id-based resolution below.
        self.public_level = public_level

    def user_has_access(
        self,
    ):
        return self.permission_level() is not None

    def user_is_owner(self) -> bool:
        # An anonymous principal (id is None) can never be the owner.
        return self.user.id is not None and self.user.id == self.checklist.owner_id

    def user_is_collaborator(self) -> bool:
        # Only an *accepted* collaborator counts as having access. A pending or
        # declined invite grants nothing (see ShareStatus / Phase 8).
        return self.user.id is not None and self.user.id in [
            collab.user_id
            for collab in self.collaborators
            if collab.status == ShareStatus.accepted
        ]

    def permission_level(self) -> ChecklistAccessLevel | None:
        """The effective permission of ``self.user`` on this checklist, or None
        if the user has no access at all."""
        if self.public_level is not None:
            return self.public_level
        if self.user_is_owner():
            return ChecklistAccessLevel.owner
        for collab in self.collaborators:
            # A pending/declined invite grants no access (Phase 8): skip it so the
            # invitee is treated exactly like an unrelated user until they accept.
            if (
                collab.user_id == self.user.id
                and collab.status == ShareStatus.accepted
            ):
                # SharePermission values are a subset of ChecklistAccessLevel values
                return ChecklistAccessLevel(collab.permission)
        return None

    def has_at_least(self, level: ChecklistAccessLevel) -> bool:
        current = self.permission_level()
        if current is None:
            return False
        return _PERMISSION_RANK[current] >= _PERMISSION_RANK[level]

    def can_view(self) -> bool:
        return self.has_at_least(ChecklistAccessLevel.view)

    def can_check(self) -> bool:
        return self.has_at_least(ChecklistAccessLevel.check)

    def can_edit(self) -> bool:
        return self.has_at_least(ChecklistAccessLevel.edit)


async def user_has_checklist_access(
    checklist_id: uuid.UUID,
    user: Annotated[User, Security(get_current_user)],
    checklist_collaborator_crud: Annotated[
        CheckListCollaboratorCRUD, Depends(CheckListCollaboratorCRUD.get_crud)
    ],
    checklist_crud: Annotated[CheckListCRUD, Depends(CheckListCRUD.get_crud)],
) -> UserChecklistAccess:

    checklist = await checklist_crud.get(
        id_=checklist_id,
        raise_exception_if_none=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
        ),
    )
    collaborators = await checklist_collaborator_crud.list(checklist_id=checklist_id)
    checklist_access = UserChecklistAccess(
        user=user, checklist=checklist, collaborators=collaborators
    )
    if not checklist_access.user_has_access():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"No access to checklist {checklist.id}.",
        )
    return checklist_access


def require_checklist_permission(min_level: ChecklistAccessLevel):
    """Dependency factory: yields the ``UserChecklistAccess`` only if the current
    user has at least ``min_level`` on the checklist, else raises 403.

    Builds on ``user_has_checklist_access`` (which already raises 404 for a
    missing checklist and 401 when the user has no access at all).

    Note: per-user data such as the user's own ``CheckListPosition`` (ordering,
    archived, collapse) and ``CheckListLabel`` are the *viewer's* layout, not the
    card's content, so routes touching only those should stay at ``"view"``.
    """

    async def _require(
        checklist_access: Annotated[
            UserChecklistAccess, Security(user_has_checklist_access)
        ],
    ) -> UserChecklistAccess:
        if not checklist_access.has_at_least(min_level):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This action requires '{min_level}' permission on checklist "
                    f"{checklist_access.checklist.id}; you have "
                    f"'{checklist_access.permission_level()}'."
                ),
            )
        return checklist_access

    return _require


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def link_is_resolvable(link: CheckListPublicShare | None) -> bool:
    """A link can resolve when it exists, is enabled, and is not past its expiry.
    (Passphrase protection, if any, is a *separate* gate — see the resolver.)"""
    if link is None or not link.enabled:
        return False
    if link.expires_at is not None and link.expires_at <= _utcnow():
        return False
    return True


async def resolve_public_checklist_access(
    token: Annotated[str, Path()],
    checklist_public_share_crud: Annotated[
        CheckListPublicShareCRUD, Depends(CheckListPublicShareCRUD.get_crud)
    ],
    checklist_collaborator_crud: Annotated[
        CheckListCollaboratorCRUD, Depends(CheckListCollaboratorCRUD.get_crud)
    ],
    checklist_crud: Annotated[CheckListCRUD, Depends(CheckListCRUD.get_crud)],
    notification_crud: Annotated[
        NotificationCRUD, Depends(NotificationCRUD.get_crud)
    ],
    sync_crud: Annotated[
        SyncNotifiationCRUD, Depends(SyncNotifiationCRUD.get_crud)
    ],
    share_grant: Annotated[Optional[str], Query()] = None,
    x_share_grant: Annotated[Optional[str], Header()] = None,
) -> UserChecklistAccess:
    """Resolve a public-share ``token`` into a ``UserChecklistAccess`` for an
    anonymous visitor.

    Every failure path returns **404** (never 401/403) so the response cannot be
    used to tell "no such link" apart from "link disabled/expired" apart from "card
    exists" apart from "wrong passphrase" — i.e. it never leaks the existence of a
    card or a token. The token is a capability, so it is never logged here.

    For a passphrase-protected link the caller must also carry a valid grant
    (``X-Share-Grant`` header or ``?share_grant=`` query), obtained from
    ``POST /public/checklist/{token}/unlock``. A missing/expired/mismatched grant
    is the *same* 404 as a missing link, so it never confirms the link exists.
    """
    not_found = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="This public link is not available.",
    )
    if not (config.SHARING_ENABLED and config.SHARING_PUBLIC_LINKS_ENABLED):
        raise not_found

    link = await checklist_public_share_crud.get_by_token(token)
    if not link_is_resolvable(link):
        raise not_found
    if link.password_hash is not None:
        grant = x_share_grant or share_grant
        if not verify_share_grant(grant, token, link.password_hash):
            raise not_found

    checklist = await checklist_crud.get(id_=link.checklist_id)
    if checklist is None:
        raise not_found

    # First successful anonymous resolve of this link → notify its creator, once.
    # mark_first_opened flips null→set atomically and returns True only for the
    # very first caller, so subsequent opens emit nothing (idempotent).
    if await checklist_public_share_crud.mark_first_opened(link.id):
        await emit_notification(
            notification_crud,
            sync_crud,
            user_id=link.created_by,
            type=NotificationType.public_link_opened,
            cl_id=checklist.id,
            payload={"checklist_name": checklist.name},
        )

    collaborators = await checklist_collaborator_crud.list(
        checklist_id=link.checklist_id
    )
    return UserChecklistAccess(
        user=AnonymousPrincipal(token=token),
        checklist=checklist,
        collaborators=collaborators,
        # SharePermission values are a subset of ChecklistAccessLevel values; a
        # public link can never grant 'owner'.
        public_level=ChecklistAccessLevel(link.permission),
    )


def require_public_checklist_permission(min_level: ChecklistAccessLevel):
    """Public-surface twin of ``require_checklist_permission``: yields the
    anonymous ``UserChecklistAccess`` only if the link grants at least
    ``min_level``, else 403. ``min_level`` should never be ``owner`` (public links
    top out at ``edit``)."""

    async def _require(
        checklist_access: Annotated[
            UserChecklistAccess, Depends(resolve_public_checklist_access)
        ],
    ) -> UserChecklistAccess:
        if not checklist_access.has_at_least(min_level):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This public link does not grant '{min_level}' permission."
                ),
            )
        return checklist_access

    return _require


async def verify_item_belongs_to_checklist(
    checklist_id: uuid.UUID,
    checklist_item_id: uuid.UUID,
    checklist_item_crud: Annotated[
        CheckListItemCRUD, Depends(CheckListItemCRUD.get_crud)
    ],
) -> CheckListItem:
    """Ensure the item addressed in the path actually belongs to the checklist in
    the path.

    ``require_checklist_permission`` authorizes the *checklist* named in the path,
    but item / item-state / item-position routes address the item by its own id.
    Without this check a user with access to checklist A could read or mutate an
    item that lives in checklist B (which they have no access to) by calling
    ``/checklist/{A}/item/{item_in_B}/...`` — a cross-checklist IDOR. Returns 404
    (rather than 400) so the existence of the foreign item is not revealed.
    """
    item = await checklist_item_crud.get(id_=checklist_item_id)
    if item is None or item.checklist_id != checklist_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Item '{checklist_item_id}' does not exist in checklist "
                f"'{checklist_id}'."
            ),
        )
    return item


async def verify_item_belongs_to_public_checklist(
    checklist_item_id: uuid.UUID,
    checklist_access: Annotated[
        UserChecklistAccess, Depends(resolve_public_checklist_access)
    ],
    checklist_item_crud: Annotated[
        CheckListItemCRUD, Depends(CheckListItemCRUD.get_crud)
    ],
) -> CheckListItem:
    """Public-surface twin of ``verify_item_belongs_to_checklist``. The checklist
    is identified by the share *token* (resolved into ``checklist_access``), not a
    path ``checklist_id``, so the cross-card IDOR guard compares the item's
    ``checklist_id`` against the token's checklist. 404 to avoid revealing the
    foreign item."""
    item = await checklist_item_crud.get(id_=checklist_item_id)
    if item is None or item.checklist_id != checklist_access.checklist.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{checklist_item_id}' is not part of this shared card.",
        )
    return item


async def checklist_ids_with_access(
    user: Annotated[User, Security(get_current_user)],
    checklist_crud: Annotated[CheckListCRUD, Depends(CheckListCRUD.get_crud)],
) -> List[uuid.UUID]:
    checklist_ids_with_user_access = await checklist_crud.list_access_ids(
        user_id=user.id
    )
    return checklist_ids_with_user_access
