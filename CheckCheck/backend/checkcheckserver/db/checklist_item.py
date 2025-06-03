from typing import (
    AsyncGenerator,
    List,
    Optional,
    Literal,
    Sequence,
    Annotated,
    Tuple,
    Dict,
)
from pydantic import validate_email, validator, StringConstraints, PositiveInt
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
    desc,
    case,
)
from sqlalchemy.orm import joinedload, selectinload
import uuid
from uuid import UUID


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist_item import (
    CheckListItem,
    CheckListItemCreate,
    CheckListItemUpdate,
)
from checkcheckserver.model.checklist_item_state import CheckListItemState
from checkcheckserver.model.checklist_item_position import CheckListItemPosition

from checkcheckserver.model.checklist import CheckList
from checkcheckserver.model.checklist_position import CheckListPosition

from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface


log = get_logger()
config = Config()


class CheckListItemCRUD(
    create_crud_base(
        table_model=CheckListItem,
        read_model=CheckListItem,
        create_model=CheckListItemCreate,
        update_model=CheckListItemUpdate,
    )
):

    async def count(
        self,
        checklist_id: Optional[uuid.UUID] = None,
        checked: Optional[bool] = None,
    ) -> int:
        query = select(func.count()).select_from(CheckListItem)
        if checklist_id is not None:
            query = query.where(CheckListItem.checklist_id == checklist_id)
        if checked is not None:
            query = query.join(CheckListItemState).where(
                CheckListItemState.checked == checked
            )

        results = await self.session.exec(statement=query)
        return results.first()

    async def list(
        self,
        checklist_id: uuid.UUID,
        checked: Optional[bool] = None,
        pagination: QueryParamsInterface = None,
    ) -> List[CheckListItem]:
        query = (
            select(CheckListItem)
            .options(
                joinedload(CheckListItem.position), joinedload(CheckListItem.state)
            )
            .join(CheckListItemPosition)
            .where(CheckListItem.checklist_id == checklist_id)
            .order_by(CheckListItemPosition.index)
        )
        if checked is not None:
            if checked is not None:
                query.join(CheckListItemState).where(
                    CheckListItemState.checked == checked
                )
        if pagination:
            query = pagination.append_to_query(query)
        results = await self.session.exec(statement=query)
        return results.all()

    async def get(
        self, id_: uuid.UUID, raise_exception_if_none: Exception = None
    ) -> CheckListItem:
        query = (
            select(CheckListItem)
            .options(
                joinedload(CheckListItem.state),
                joinedload(CheckListItem.position),
            )
            .where(CheckListItem.id == id_)
        )
        results = await self.session.exec(statement=query)
        obj = results.one_or_none()
        if raise_exception_if_none and obj is None:
            raise raise_exception_if_none
        return obj

    async def list_multiple_checklist_items(
        self,
        checklist_ids: List[uuid.UUID],
        limit_per_checklist: Optional[PositiveInt] = 12,
        checked: Optional[bool] = None,
    ) -> Dict[uuid.UUID, List[CheckListItem]]:
        # ToDo: this multiquery approach can propably be optimisted with a one-catch-it-all query...
        checklist_pos_query = select(CheckListPosition).where(
            col(CheckListPosition.checklist_id).in_(checklist_ids)
        )
        checklist_pos_query_result = await self.session.exec(
            statement=checklist_pos_query
        )

        result: Dict[uuid.UUID, List[CheckListItem]] = {}
        for checklist_pos in checklist_pos_query_result.unique().all():
            query = (
                select(CheckListItem)
                .join(CheckListItemState)
                .join(CheckListItemPosition)
            )
            if checked is not None:
                query = query.where(CheckListItemState.checked == checked)
            query = query.where(
                CheckListItem.checklist_id == checklist_pos.checklist_id
            )
            if checklist_pos.checked_items_seperated:
                query = query.order_by(CheckListItemState.checked)
            query = query.order_by(CheckListItemPosition.index)
            if limit_per_checklist is not None:
                query = query.limit(limit_per_checklist)
            query_result = await self.session.exec(statement=query)
            result[checklist_pos.checklist_id] = query_result.all()
        return result
