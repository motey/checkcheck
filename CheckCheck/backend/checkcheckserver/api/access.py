import uuid
import enum
from typing import List, Literal, Annotated
from fastapi import HTTPException, status, Security, Depends, Path

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
from checkcheckserver.model.checklist_collaborator import CheckListCollaborator
from checkcheckserver.db.checklist_collaborator import CheckListCollaboratorCRUD
from checkcheckserver.model.checklist import CheckList
from checkcheckserver.db.checklist import CheckListCRUD

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


class UserChecklistAccess:

    def __init__(
        self,
        user: User,
        checklist: CheckList,
        collaborators: List[CheckListCollaborator] = [],
    ):
        self.user = user
        self.checklist = checklist
        self.collaborators = collaborators

    def user_has_access(
        self,
    ):
        return self.permission_level() is not None

    def user_is_owner(self) -> bool:
        return self.user.id == self.checklist.owner_id

    def user_is_collaborator(self) -> bool:
        return self.user.id in [collab.user_id for collab in self.collaborators]

    def permission_level(self) -> ChecklistAccessLevel | None:
        """The effective permission of ``self.user`` on this checklist, or None
        if the user has no access at all."""
        if self.user_is_owner():
            return ChecklistAccessLevel.owner
        for collab in self.collaborators:
            if collab.user_id == self.user.id:
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


async def checklist_ids_with_access(
    user: Annotated[User, Security(get_current_user)],
    checklist_crud: Annotated[CheckListCRUD, Depends(CheckListCRUD.get_crud)],
) -> List[uuid.UUID]:
    checklist_ids_with_user_access = await checklist_crud.list_access_ids(
        user_id=user.id
    )
    return checklist_ids_with_user_access
