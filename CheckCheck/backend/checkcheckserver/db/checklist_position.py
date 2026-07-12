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
from checkcheckserver.model._base_model import naive_utc_now
from checkcheckserver.api.paginator import QueryParamsInterface
from checkcheckserver.model.checklist_collaborator import CheckListCollaborator
from checkcheckserver.model.checklist import CheckList

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

    @staticmethod
    def _exclude_tombstoned_checklists(query):
        """Drop position rows whose checklist was tombstoned (WI-2).

        A soft-deleted checklist leaves its per-user position rows in place
        (cascade rule — children are masked, not deleted), so every board-wide
        position scan must exclude them or the card would still count/order as if
        present. A correlated EXISTS is used rather than a join so CheckList's
        eager-joined relationships (labels) don't multiply the position rows.
        """
        alive = (
            select(CheckList.id)
            .where(
                CheckList.id == CheckListPosition.checklist_id,
                col(CheckList.deleted_at).is_(None),
            )
            .exists()
        )
        return query.where(alive)

    async def list(
        self,
        filter_checklist_id: Optional[uuid.UUID] = None,
        filter_user_id: Optional[uuid.UUID] = None,
        archived: Optional[bool] = None,
        pagination: QueryParamsInterface = None,
    ) -> List[CheckListPosition]:
        query = select(CheckListPosition)
        query = self._exclude_tombstoned_checklists(query)
        if filter_checklist_id is not None:
            query = query.where(CheckListPosition.checklist_id == filter_checklist_id)
        if filter_user_id is not None:
            query = query.where(CheckListPosition.user_id == filter_user_id)
        if archived is not None:
            query = query.where(CheckListPosition.archived == archived)
        if pagination:
            query = pagination.append_to_query(query)
        results = await self.session.exec(statement=query)
        return results.unique().all()

    async def count(
        self,
        filter_checklist_id: Optional[uuid.UUID] = None,
        filter_user_id: Optional[uuid.UUID] = None,
        archived: Optional[bool] = None,
    ) -> List[CheckListPosition]:
        query = select(func.count()).select_from(CheckListPosition)
        query = self._exclude_tombstoned_checklists(query)
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
            and_(
                CheckListPosition.checklist_id == checklist_id,
                CheckListPosition.user_id == user_id,
            )
        )
        results = await self.session.exec(statement=query)
        result_item = results.unique().one_or_none()

        if result_item is None and raise_exception_if_none:
            raise raise_exception_if_none
        return result_item

    async def touch(
        self,
        checklist_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        """Re-stamp the caller's position row so the delta feed re-emits the card
        for THIS user (WI-4 grouping: card-level = row / caller's position /
        caller's labels).

        Needed for per-user card-level changes that leave no ``server_seq`` trace
        of their own — currently label *detach*, which HARD-deletes the link row
        (WI-2 kept ``checklist_label`` untombstoned). Dirtying ``updated_at``
        makes the flush emit an UPDATE, so the ``before_update`` mapper event
        allocates a fresh ``server_seq``; the position's fields are unchanged, so
        LWW-wise this is a no-op for other devices. Returns False when the caller
        has no position row (should not happen for owner/accepted collaborator).
        """
        existing = await self.get(checklist_id=checklist_id, user_id=user_id)
        if existing is None:
            return False
        existing.updated_at = naive_utc_now()
        self.session.add(existing)
        await self.session.commit()
        return True

    async def list_gained_access_checklist_ids(
        self,
        user_id: uuid.UUID,
        since: int,
    ) -> List[uuid.UUID]:
        """Checklist ids where THIS user's position row was *created* after
        ``since`` — i.e. access was granted since the client's cursor (WI-4 /
        Phase 1+2 review finding 1).

        A position row exists for exactly the users who can see the card, and every
        grant path inserts it at grant time (create, instant share, invite accept,
        public-link join, ownership transfer), so keying gain off ``granted_seq``
        covers all of them uniformly — including ownership transfer to a
        non-collaborator, which leaves no fresh accepted-collaborator seq. The
        whole card tree predates the grant, so the delta feed ships it in full for
        these cards. ``granted_seq`` is stamped once on insert and never bumped, so
        a later reorder/pin/touch of the same position is not mistaken for a grant.
        """
        query = select(CheckListPosition.checklist_id).where(
            CheckListPosition.user_id == user_id,
            col(CheckListPosition.granted_seq) > since,
        )
        results = await self.session.exec(statement=query)
        return list(results.all())

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
        query = self._exclude_tombstoned_checklists(query)
        result = await self.session.exec(query)
        return result.unique().one_or_none()

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
        query = self._exclude_tombstoned_checklists(query)
        result = await self.session.exec(query)
        return result.unique().one_or_none()

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
        current_lowest_index_query = self._exclude_tombstoned_checklists(
            current_lowest_index_query
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
        current_highest_index_query = self._exclude_tombstoned_checklists(
            current_highest_index_query
        )
        current_highest_pos_result = await self.session.exec(
            current_highest_index_query
        )
        return current_highest_pos_result.unique().one_or_none()
