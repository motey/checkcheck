from typing import AsyncGenerator, List, Optional, Literal, Sequence, Annotated, Tuple
from pydantic import validate_email, validator, StringConstraints
from pydantic_core import PydanticCustomError
from fastapi import Depends
import contextlib
from typing import Optional
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import (
    Field,
    select,
    delete,
    Column,
    JSON,
    SQLModel,
    func,
    col,
    and_,
    or_,
    desc,
    asc,
)

import uuid
from uuid import UUID

from sqlmodel.sql import expression as sqlEpression
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist_position import (
    CheckListPosition,
    CheckListPositionCreate,
    CheckListPositionUpdate,
)
from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface
from checkcheckserver.model.checklist_collaborator import CheckListCollaborator

log = get_logger()
config = Config()


class CheckListPositionCRUD(
    create_crud_base(
        table_model=CheckListPosition,
        read_model=CheckListPosition,
        create_model=CheckListPositionCreate,
        update_model=CheckListPositionUpdate,
    )
):

    async def list(
        self,
        filter_checklist_id: Optional[uuid.UUID] = None,
        filter_user_id: Optional[uuid.UUID] = None,
        archived: Optional[bool] = None,
        pagination: QueryParamsInterface = None,
    ) -> List[CheckListPosition]:
        query = select(CheckListPosition)
        if filter_checklist_id is not None:
            query = query.where(CheckListPosition.checklist_id == filter_checklist_id)
        if filter_user_id is not None:
            query = query.where(CheckListPosition.user_id == filter_user_id)
        if archived is not None:
            query = query.where(CheckListPosition.archived == archived)
        if pagination:
            query = pagination.append_to_query(query)
        results = await self.session.exec(statement=query)
        return results.all()

    async def count(
        self,
        filter_checklist_id: Optional[uuid.UUID] = None,
        filter_user_id: Optional[uuid.UUID] = None,
        archived: Optional[bool] = None,
    ) -> List[CheckListPosition]:
        query = select(func.count()).select_from(CheckListPosition)
        if filter_checklist_id is not None:
            query = query.where(CheckListPosition.checklist_id == filter_checklist_id)
        if filter_user_id is not None:
            query = query.where(CheckListPosition.user_id == filter_user_id)
        if archived is not None:
            query = query.where(CheckListPosition.archived == archived)
        results = await self.session.exec(statement=query)
        return results.one()

    async def get(
        self,
        checklist_id: uuid.UUID,
        user_id: uuid.UUID,
        raise_exception_if_none: Exception = None,
    ) -> Optional[CheckListPosition]:
        query = select(CheckListPosition).where(
            CheckListPosition.checklist_id == checklist_id
            and CheckListPosition.user_id == user_id
        )
        results = await self.session.exec(statement=query)
        result_item = results.one_or_none()

        if result_item is None and raise_exception_if_none:
            raise raise_exception_if_none
        return result_item

    async def get_next(
        self, checklist_id: uuid.UUID, user_id: uuid.UUID
    ) -> CheckListPosition | None:
        """
        Return the previous CheckListPosition with a lower index value.
        In other words, return the check list
        that comes before the current item (if all items are in a list).

        Args:
            checklist_id (uuid.UUID): ID of the checklist to which the items belong.
            current_item_id (uuid.UUID): ID of the current checklist item.

        Returns:
            Optional[CheckListItemPosition]: The next item with a higher index, or None if it's the last one.
        """
        subquery_current_checklist_index = (
            select(CheckListPosition.index)
            .where(CheckListPosition.checklist_id == checklist_id)
            .where(CheckListPosition.user_id == user_id)
            .scalar_subquery()
        )
        query = (
            select(CheckListPosition)
            .where(CheckListPosition.user_id == user_id)
            .where(CheckListPosition.index > subquery_current_checklist_index)
            .order_by(asc(CheckListPosition.index))
            .limit(1)
        )
        result = await self.session.exec(query)
        return result.one_or_none()

    async def get_prev(
        self, checklist_id: uuid.UUID, user_id: uuid.UUID
    ) -> CheckListPosition | None:
        """
        Return the previous CheckListPosition with a lower index value.
        In other words, return the check list
        that comes before the current item (if all items are in a list).

        Args:
            checklist_id (uuid.UUID): ID of the checklist to which the items belong.
            current_item_id (uuid.UUID): ID of the current checklist item.

        Returns:
            Optional[CheckListItemPosition]: The next item with a higher index, or None if it's the last one.
        """
        subquery_current_checklist_index = (
            select(CheckListPosition.index)
            .where(CheckListPosition.checklist_id == checklist_id)
            .where(CheckListPosition.user_id == user_id)
            .scalar_subquery()
        )
        query = (
            select(CheckListPosition)
            .where(CheckListPosition.user_id == user_id)
            .where(CheckListPosition.index < subquery_current_checklist_index)
            .order_by(desc(CheckListPosition.index))
            .limit(1)
        )
        result = await self.session.exec(query)
        return result.one_or_none()

    async def update(
        self,
        update_obj: CheckListPosition | CheckListPositionUpdate,
        checklist_id: uuid.UUID,
        user_id: uuid.UUID,
        raise_exception_if_not_exists=None,
    ) -> CheckListPosition:
        log.debug(("checklist_id", type(checklist_id), checklist_id))
        log.debug(("user_id", type(user_id), user_id))
        query = select(CheckListPosition).where(
            and_(
                CheckListPosition.checklist_id == checklist_id,
                CheckListPosition.user_id == user_id,
            )
        )
        results = await self.session.exec(statement=query)
        existing_obj = results.unique().one_or_none()
        if existing_obj is None and raise_exception_if_not_exists:
            raise raise_exception_if_not_exists
        elif existing_obj is None:
            return
        for k, v in update_obj.model_dump(exclude_unset=True).items():
            if k in CheckListPositionUpdate.model_fields.keys():
                setattr(existing_obj, k, v)
        self.session.add(existing_obj)
        await self.session.commit()
        await self.session.refresh(existing_obj)
        return existing_obj

    async def delete(
        self,
        checklist_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
    ):
        del_statement = delete(CheckListPosition).where(
            CheckListPosition.checklist_id == checklist_id
        )
        if user_id is not None:
            del_statement = del_statement.where(CheckListPosition.user_id == user_id)

        await self.session.exec(del_statement)
        await self.session.commit()
        return

    async def get_first(self, user_id: uuid.UUID) -> CheckListPosition | None:
        current_lowest_index_query = (
            select(CheckListPosition)
            .where(CheckListPosition.user_id == user_id)
            .order_by(asc(CheckListPosition.index))
            .limit(1)
        )
        current_highest_pos_result = await self.session.exec(current_lowest_index_query)
        return current_highest_pos_result.one_or_none()

    async def get_last(self, user_id: uuid.UUID) -> CheckListPosition | None:
        current_highest_index_query = (
            select(CheckListPosition)
            .where(CheckListPosition.user_id == user_id)
            .order_by(desc(CheckListPosition.index))
            .limit(1)
        )
        current_highest_pos_result = await self.session.exec(
            current_highest_index_query
        )
        return current_highest_pos_result.unique().one_or_none()
