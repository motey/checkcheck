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
from sqlmodel import Field, select, delete, Column, JSON, SQLModel, func, col

import uuid
from uuid import UUID


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist import CheckList
from checkcheckserver.model.checklist_item import CheckListItem
from checkcheckserver.model.checklist_item_state import (
    CheckListItemState,
    CheckListItemStateCreate,
    CheckListItemStateUpdate,
)

from checkcheckserver.model.checklist_position import CheckListPosition

from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface


log = get_logger()
config = Config()


class CheckListItemStateCRUD(
    create_crud_base(
        table_model=CheckListItemState,
        read_model=CheckListItemState,
        create_model=CheckListItemStateCreate,
        update_model=CheckListItemStateUpdate,
    )
):

    async def list_and_create_missing_states(
        self,
        checklist_ids: list[uuid.UUID],
        pagination: QueryParamsInterface = None,
    ) -> Dict[uuid.UUID, Dict[uuid.UUID, CheckListItemState]]:
        if not checklist_ids:
            return []
        query = (
            select(CheckListItem, CheckListItemState)
            .select_from(CheckListItem)
            .join(CheckListItemState)
            .where(col(CheckListItem.checklist_id).in_(checklist_ids))
        )
        if pagination:
            pagination.append_to_query(query)
        result = await self.session.exec(query)
        result_obj: Dict[uuid.UUID, Dict[uuid.UUID, CheckListItemState]] = {}
        new_states: List[CheckListItemState] = []
        for item, state in result.all():
            if state is None:
                state = CheckListItemState(checklist_item_id=item.id, value=False)
                new_states.append(state)
            item_state_dict = result_obj.get(item.checklist_id, default=dict())
            item_state_dict[item.id] = state
            result_obj[item.checklist_id] = item_state_dict
        if new_states:
            self.session.add_all(new_states)
            await self.session.commit()
        return result_obj

    async def list_from_checklist_and_create_missing_states(
        self,
        checklist_id: uuid.UUID,
        pagination: QueryParamsInterface = None,
    ) -> Dict[uuid.UUID, CheckListItemState]:
        res = await self.list_and_create_missing_states(
            [checklist_id], pagination=pagination
        )
        return res[checklist_id]
        query = (
            select(CheckListItem, CheckListItemState)
            .select_from(CheckListItem)
            .join(CheckListItemState)
            .where(CheckListItem.checklist_id == checklist_id)
        )
        if pagination:
            pagination.append_to_query(query)

        result = await self.session.exec(query)
        all_states: Dict[uuid.UUID, CheckListItemState] = {}
        new_states: List[CheckListItemState] = []
        for item, state in result.all():
            if state is None:
                state = CheckListItemState(checklist_item_id=item.id, value=False)
                new_states.append(state)
            all_states[item.id] = state
        if new_states:
            self.session.add_all(new_states)
            await self.session.commit()
        return all_states

    async def get(
        self, checklist_item_id: uuid.UUID, raise_exception_if_none: Exception
    ) -> CheckListItemState:
        result = await self.session.exec(
            select(CheckListItemState).where(
                CheckListItemState.checklist_item_id == checklist_item_id
            )
        )
        checklist_item_state = result.one_or_none()
        if checklist_item_state is not None:
            return checklist_item_state
        if raise_exception_if_none:
            raise raise_exception_if_none
        return None

    async def set_checklist_item_state(
        self,
        checklist_item_id: uuid.UUID,
        checked: bool = True,
        raise_if_checklist_item_does_not_exists: Exception = None,
    ):
        query = (
            select(CheckListItemState, CheckListItem)
            .select_from(CheckListItem)
            .join(CheckListItemState)
            .where(CheckListItem.id == checklist_item_id)
        )
        result = await self.session.exec(query)
        item_state, item = result.one_or_none()
        if item is None and raise_if_checklist_item_does_not_exists:
            raise raise_if_checklist_item_does_not_exists
        if item_state:
            if item_state.checked == checked:
                return item_state
            item_state.checked = checked
            self.session.add(item_state)
        else:
            item_state = CheckListItemState(
                checklist_item_id=checklist_item_id, checked=checked
            )
            self.session.add(item_state)
        await self.session.commit()
        return item_state

    async def get_checklist_item_state(
        self,
        checklist_item_id: uuid.UUID,
        raise_if_checklist_item_does_not_exists: Exception = None,
    ):
        query = (
            select(CheckListItemState, CheckListItem)
            .select_from(CheckListItem)
            .join(CheckListItemState)
            .where(CheckListItem.id == checklist_item_id)
        )
        result = await self.session.exec(query)
        item_state, item = result.one_or_none()
        if item is None and raise_if_checklist_item_does_not_exists:
            raise raise_if_checklist_item_does_not_exists
        if item_state:
            return item_state
        else:
            item_state = await self.create(
                checklist_item_id=checklist_item_id,
                create_obj=CheckListItemStateCreate(value=False),
            )
            return item_state

    # remove?
    async def create2(
        self,
        checklist_item_id: uuid.UUID,
        create_obj: CheckListItemStateCreate,
        exists_ok: bool = False,
        raise_exception_if_item_not_exists: Exception = None,
    ):
        if raise_exception_if_item_not_exists:
            res = await self.session.exec(
                select(CheckListItem).where(CheckListItem.id == checklist_item_id)
            )
            if res.one_or_none() is None:
                raise raise_exception_if_item_not_exists
        obj = CheckListItemState(
            checklist_item_id=checklist_item_id,
            **create_obj.model_dump(exclude_unset=True)
        )
        self.session.add(obj)
        await self.session.commit()
        return obj
