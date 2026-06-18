from typing import AsyncGenerator, List, Optional, Literal, Sequence, Annotated, Tuple
from pydantic import validate_email, validator, StringConstraints
from pydantic_core import PydanticCustomError
from fastapi import Depends
import contextlib
from typing import Optional
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import Field, select, delete, Column, JSON, SQLModel, func, col

import uuid
from uuid import UUID


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist_collaborator import (
    CheckListCollaborator,
    CheckListCollaboratorCreate,
)
from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface


log = get_logger()
config = Config()


class CheckListCollaboratorCRUD(
    create_crud_base(
        table_model=CheckListCollaborator,
        read_model=CheckListCollaborator,
        create_model=CheckListCollaboratorCreate,
        update_model=CheckListCollaborator,
    )
):

    async def list(
        self,
        checklist_id: uuid.UUID,
        pagination: QueryParamsInterface = None,
    ) -> List[CheckListCollaborator]:
        query = (
            select(CheckListCollaborator)
            .select_from(CheckListCollaborator)
            .where(CheckListCollaborator.checklist_id == checklist_id)
        )
        if pagination:
            query = pagination.append_to_query(query)
        results = await self.session.exec(statement=query)
        return results.all()

    async def get_one(
        self,
        checklist_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Optional[CheckListCollaborator]:
        query = select(CheckListCollaborator).where(
            CheckListCollaborator.checklist_id == checklist_id,
            CheckListCollaborator.user_id == user_id,
        )
        results = await self.session.exec(statement=query)
        return results.one_or_none()

    async def upsert(
        self,
        checklist_id: uuid.UUID,
        user_id: uuid.UUID,
        permission,
    ) -> CheckListCollaborator:
        """Create the collaborator or, if it already exists, update its permission."""
        existing = await self.get_one(checklist_id=checklist_id, user_id=user_id)
        if existing is not None:
            existing.permission = permission
            self.session.add(existing)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        return await self.create(
            CheckListCollaboratorCreate(
                checklist_id=checklist_id,
                user_id=user_id,
                permission=permission,
            )
        )

    async def delete(
        self,
        checklist_id: UUID,
        user_id: Optional[UUID] = None,
    ):
        del_statement = delete(CheckListCollaborator).where(
            CheckListCollaborator.checklist_id == checklist_id
        )
        if user_id is not None:
            del_statement = del_statement.where(
                CheckListCollaborator.user_id == user_id
            )

        await self.session.exec(del_statement)
        await self.session.commit()
        return
