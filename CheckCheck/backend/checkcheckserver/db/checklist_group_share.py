"""CRUD for ``CheckListGroupShare`` — the source-of-truth rows behind living
group shares. Access materialization (collaborator + position rows) is handled by
``api/group_share_reconcile.py``; this layer just persists the group→level intent.
"""

from typing import List, Optional
import uuid

from sqlmodel import select, delete, col

from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist_group_share import (
    CheckListGroupShare,
    CheckListGroupShareCreate,
)
from checkcheckserver.db._base_crud import create_crud_base


log = get_logger()


class CheckListGroupShareCRUD(
    create_crud_base(
        table_model=CheckListGroupShare,
        read_model=CheckListGroupShare,
        create_model=CheckListGroupShareCreate,
        update_model=CheckListGroupShare,
    )
):

    async def list_for_checklist(
        self,
        checklist_id: uuid.UUID,
    ) -> List[CheckListGroupShare]:
        query = select(CheckListGroupShare).where(
            CheckListGroupShare.checklist_id == checklist_id
        )
        results = await self.session.exec(statement=query)
        return list(results.all())

    async def list_for_groups(
        self,
        groups: List[str],
    ) -> List[CheckListGroupShare]:
        """Every group share targeting any of ``groups`` — used to reconcile one
        user (their current OIDC groups) across all cards on login."""
        if not groups:
            return []
        query = select(CheckListGroupShare).where(
            col(CheckListGroupShare.group).in_(groups)
        )
        results = await self.session.exec(statement=query)
        return list(results.all())

    async def get_one(
        self,
        checklist_id: uuid.UUID,
        group: str,
    ) -> Optional[CheckListGroupShare]:
        query = select(CheckListGroupShare).where(
            CheckListGroupShare.checklist_id == checklist_id,
            CheckListGroupShare.group == group,
        )
        results = await self.session.exec(statement=query)
        return results.one_or_none()

    async def upsert(
        self,
        checklist_id: uuid.UUID,
        group: str,
        permission,
        created_by: uuid.UUID,
    ) -> CheckListGroupShare:
        """Create the group share, or raise/lower its level if it already exists."""
        existing = await self.get_one(checklist_id=checklist_id, group=group)
        if existing is not None:
            existing.permission = permission
            self.session.add(existing)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        return await self.create(
            CheckListGroupShareCreate(
                checklist_id=checklist_id,
                group=group,
                permission=permission,
                created_by=created_by,
            )
        )

    async def delete(
        self,
        checklist_id: uuid.UUID,
        group: str,
    ) -> None:
        del_statement = delete(CheckListGroupShare).where(
            CheckListGroupShare.checklist_id == checklist_id,
            CheckListGroupShare.group == group,
        )
        await self.session.exec(del_statement)
        await self.session.commit()
