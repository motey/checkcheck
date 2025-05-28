import uuid
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
        if self.user.id == self.checklist.owner_id:
            return True
        if self.user.id in [collab.user_id for collab in self.collaborators]:
            return True
        return False

    def user_is_owner(self) -> bool:
        return self.user.id == self.checklist.owner_id

    def user_is_collaborator(self) -> bool:
        return self.user.id in [collab.user_id for collab in self.collaborators]


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


async def checklist_ids_with_access(
    user: Annotated[User, Security(get_current_user)],
    checklist_crud: Annotated[CheckListCRUD, Depends(CheckListCRUD.get_crud)],
) -> List[uuid.UUID]:
    checklist_ids_with_user_access = await checklist_crud.list_access_ids(
        user_id=user.id
    )
    return checklist_ids_with_user_access
