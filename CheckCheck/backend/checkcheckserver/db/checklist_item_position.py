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
from sqlmodel import Field, select, delete, Column, JSON, SQLModel, func, col, desc, asc

import uuid
from uuid import UUID


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist_item import (
    CheckListItem,
    CheckListItemCreate,
    CheckListItemUpdate,
)
from checkcheckserver.model.checklist_item_position import (
    CheckListItemPosition,
    CheckListItemPositionUpdate,
    CheckListItemPositionCreate,
)
from checkcheckserver.model.checklist_item_state import CheckListItemState


from checkcheckserver.model.checklist_position import CheckListPosition

from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface


log = get_logger()
config = Config()


class CheckListItemPositionCRUD(
    create_crud_base(
        table_model=CheckListItemPosition,
        read_model=CheckListItemPosition,
        create_model=CheckListItemPositionCreate,
        update_model=CheckListItemPositionUpdate,
    )
):

    async def list(
        self,
        checklist_ids: List[uuid.UUID],
        checked: Optional[bool] = None,
        pagination: QueryParamsInterface = None,
    ) -> Dict[uuid.UUID, CheckListItemPosition]:
        if not checklist_ids:
            return []
        query = select(CheckListPosition.checklist_id, CheckListItemPosition).join(
            CheckListPosition
        )
        if checked is not None:
            query = query.join(CheckListItemState).where(
                CheckListItemState.checked == checked
            )
        query = query.where(
            col(CheckListItem.checklist_id).in_(checklist_ids)
        ).order_by(CheckListItemPosition.index)
        if pagination:
            query = pagination.append_to_query(query)
        results = await self.session.exec(statement=query)
        result_obj: Dict[uuid.UUID, CheckListItemPosition] = {}
        for checklist_id, checklist_item_index in results.all():
            result_obj[checklist_id, checklist_item_index]
        return result_obj

    async def get(
        self, checklist_item_id: uuid.UUID, raise_exception_if_none: Exception = None
    ) -> CheckListItemPosition | None:
        query = select(CheckListItemPosition).where(
            CheckListItemPosition.checklist_item_id == checklist_item_id
        )
        result = await self.session.exec(query)
        obj = result.one_or_none()
        if obj is None and raise_exception_if_none:
            raise raise_exception_if_none
        return obj

    async def get_prev(
        self,
        checklist_id: uuid.UUID,
        current_checklist_item_id: uuid.UUID,
    ) -> CheckListItemPosition | None:
        """
        Return the previous CheckListItemPosition with a lower index value
        within the same checklist. In other words, return the item
        that comes before the current item (if all items are in a list).

        Args:
            checklist_id (uuid.UUID): ID of the checklist to which the items belong.
            current_item_id (uuid.UUID): ID of the current checklist item.

        Returns:
            Optional[CheckListItemPosition]: The next item with a higher index, or None if it's the last one.
        """
        subquery_current_item_index = (
            select(CheckListItemPosition.index)
            .where(CheckListItemPosition.checklist_item_id == current_checklist_item_id)
            .scalar_subquery()
        )
        query = (
            select(CheckListItemPosition)
            .join(CheckListItem)
            .where(CheckListItem.checklist_id == checklist_id)
            .where(CheckListItemPosition.index < subquery_current_item_index)
            .order_by(desc(CheckListItemPosition.index))
            .limit(1)
        )
        result = await self.session.exec(query)
        return result.one_or_none()

    async def get_next(
        self,
        checklist_id: uuid.UUID,
        current_checklist_item_id: uuid.UUID,
    ) -> CheckListItemPosition | None:
        """
        Return the next CheckListItemPosition with a higher index value
        within the same checklist. In other words, return the item
        that comes directly after the current item (if all items are in a list).

        Args:
            checklist_id (uuid.UUID): ID of the checklist to which the items belong.
            current_item_id (uuid.UUID): ID of the current checklist item.

        Returns:
            Optional[CheckListItemPosition]: The next item with a higher index, or None if it's the last one.
        """
        subquery_current_item_index = (
            select(CheckListItemPosition.index)
            .where(CheckListItemPosition.checklist_item_id == current_checklist_item_id)
            .scalar_subquery()
        )
        query = (
            select(CheckListItemPosition)
            .join(CheckListItem)
            .where(CheckListItem.checklist_id == checklist_id)
            .where(CheckListItemPosition.index > subquery_current_item_index)
            .order_by(asc(CheckListItemPosition.index))
            .limit(1)
        )
        result = await self.session.exec(query)
        return result.one_or_none()

    async def get_last(
        self,
        checklist_id: uuid.UUID,
    ) -> CheckListItemPosition | None:
        """Return the CheckListItemPosition with the highest index value for a given check list

        Args:
            checklist_id (uuid.UUID): _description_

        Returns:
            CheckListItemPosition | None: _description_
        """
        query = (
            select(CheckListItemPosition)
            .join(CheckListItem)
            .where(CheckListItem.checklist_id == checklist_id)
            .order_by(desc(CheckListItemPosition.index))
            .limit(1)
        )
        result = await self.session.exec(query)
        return result.one_or_none()

    async def get_first(
        self,
        checklist_id: uuid.UUID,
    ) -> CheckListItemPosition | None:
        """Return the CheckListItemPosition with the lowest index value for a given check list

        Args:
            checklist_id (uuid.UUID): _description_

        Returns:
            CheckListItemPosition | None: _description_
        """
        query = (
            select(CheckListItemPosition)
            .join(CheckListItem)
            .where(CheckListItem.checklist_id == checklist_id)
            .order_by(asc(CheckListItemPosition.index))
            .limit(1)
        )
        result = await self.session.exec(query)
        return result.one_or_none()

    async def update(
        self,
        checklist_item_position_update: CheckListItemPositionUpdate,
        raise_exception_if_none: Exception = None,
    ) -> CheckListItemPosition:

        query = select(CheckListItemPosition).where(
            CheckListItemPosition.checklist_item_id
            == checklist_item_position_update.checklist_item_id
        )
        result = await self.session.exec(query)
        obj = result.one_or_none()

        if obj is None and raise_exception_if_none:
            raise raise_exception_if_none
        for key, val in checklist_item_position_update.model_dump(
            exclude_unset=True
        ).items():
            setattr(obj, key, val)
        self.session.add(obj)
        await self.session.commit()
        return obj

    async def reorder(self, checklist_id: uuid.UUID) -> List[CheckListItemPosition]:
        """Rewrite all index values. This can be used if the index values are to scattered due to many reorderings.
        This should be a last resort weapon as it rewrites the whole list and need to be pushed to all clients.
        It should only be used, when atomic reordering make no sense.

        Args:
            ids (List[uuid.UUID]): _description_

        Returns:
            List[CheckListItem]: _description_
        """
        log.warning(("reorder_checklists ids:", checklist_id))
        query = (
            select(CheckListItemPosition)
            .join(CheckListItem)
            .where(CheckListItem.checklist_id)
            == checklist_id
        )

        log.debug(query)
        sequence = 1
        query_result = await self.session.exec(statement=query)
        checklistitem_pos_objs: List[CheckListItemPosition] = query_result.all()
        for checklistitem_pos in checklistitem_pos_objs:
            checklistitem_pos.index = sequence
            sequence += 4
        # save/commit new CheckList order
        self.session.add_all(checklistitem_pos_objs)
        await self.session.commit()
        return checklistitem_pos_objs
