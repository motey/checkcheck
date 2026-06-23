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
)

import uuid
from uuid import UUID
from sqlmodel.sql import expression as sqlEpression
from sqlalchemy.orm import selectinload, with_loader_criteria
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist import (
    CheckList,
    CheckListUpdate,
    CheckListCreate,
    SharedFilter,
)
from checkcheckserver.model.checklist_collaborator import (
    CheckListCollaborator,
    CheckListCollaboratorCreate,
    ShareStatus,
)
from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface
from checkcheckserver.model.checklist_position import CheckListPosition
from checkcheckserver.model.checklist import CheckListApi, CheckListApiWithSubObj
from checkcheckserver.model.label import Label
from checkcheckserver.model.checklist_label import CheckListLabel
from checkcheckserver.model.checklist_item import CheckListItem

log = get_logger()
config = Config()


class CheckListCRUD(
    create_crud_base(
        table_model=CheckList,
        read_model=CheckList,
        create_model=CheckListCreate,
        update_model=CheckListUpdate,
    )
):

    def _add_user_has_access_query(
        self,
        query: sqlEpression.Select,
        user_id: uuid.UUID,
        join_CheckListPosition: bool = True,
    ):
        # Access = owner OR a collaborator row for this user. Expressed as an
        # EXISTS rather than an (outer) join so a card with several collaborators
        # does not multiply into one result row per collaborator (which would
        # duplicate owned-and-shared cards in list() and overcount in count()).
        # Status is intentionally NOT filtered here — accepted-only gating comes
        # from the CheckListPosition inner-join below (pending invites have no
        # position row), so this predicate keeps the historical access semantics.
        is_collaborator = (
            select(CheckListCollaborator.checklist_id)
            .where(
                and_(
                    CheckListCollaborator.checklist_id == CheckList.id,
                    CheckListCollaborator.user_id == user_id,
                )
            )
            .exists()
        )
        query = query.where(
            or_(CheckList.owner_id == user_id, is_collaborator)
        )
        if join_CheckListPosition:
            query = query.join(
                CheckListPosition,
                onclause=and_(
                    CheckListPosition.user_id == user_id,
                    CheckListPosition.checklist_id == CheckList.id,
                ),
            )
        return query

    def _add_shared_filter(
        self,
        query: sqlEpression.Select,
        user_id: uuid.UUID,
        shared: Optional[SharedFilter],
    ):
        """Narrow ``query`` to a sharing relationship. Must be applied on top of
        ``_add_user_has_access_query`` (which already restricts non-owned rows to
        the caller's accepted-collaborator rows via the position inner-join)."""
        if shared is None:
            return query
        if shared == SharedFilter.with_me:
            # Cards owned by someone else; the access query guarantees the caller
            # is an accepted collaborator on every non-owned row it returns.
            return query.where(CheckList.owner_id != user_id)
        # shared == by_me: cards the caller owns that have >=1 accepted
        # collaborator. EXISTS subquery avoids row multiplication from the join.
        has_collaborator = (
            select(CheckListCollaborator.checklist_id)
            .where(
                and_(
                    CheckListCollaborator.checklist_id == CheckList.id,
                    CheckListCollaborator.status == ShareStatus.accepted,
                )
            )
            .exists()
        )
        return query.where(
            and_(CheckList.owner_id == user_id, has_collaborator)
        )

    async def count(
        self,
        user_id: uuid.UUID,
        archived: Optional[bool] = None,
        label_id: Optional[uuid.UUID] = None,
        search: Optional[str] = None,
        shared: Optional[SharedFilter] = None,
    ) -> int:
        query = select(func.count()).select_from(CheckList)

        if archived is not None:
            query = query.where(CheckListPosition.archived == archived)
        query = self._add_user_has_access_query(query, user_id)
        query = self._add_shared_filter(query, user_id, shared)
        if label_id is not None:
            query = query.join(CheckListLabel).where(
                CheckListLabel.label_id == label_id
            )
        if search is not None:
            needle = f"%{search}%"
            item_match = (
                select(CheckListItem.id)
                .where(
                    and_(
                        CheckListItem.checklist_id == CheckList.id,
                        CheckListItem.text.ilike(needle),
                    )
                )
                .exists()
            )
            query = query.where(
                or_(
                    col(CheckList.name).ilike(needle),
                    col(CheckList.text).ilike(needle),
                    item_match,
                )
            )

        results = await self.session.exec(statement=query)
        return results.first()

    async def set_owner(
        self,
        checklist_id: uuid.UUID,
        new_owner_id: uuid.UUID,
        raise_exception_if_none: Exception = None,
    ) -> CheckList:
        """Transfer ownership. ``owner_id`` is intentionally not part of
        ``CheckListUpdate`` (clients must not reassign owners via PATCH), so this
        is a dedicated method."""
        checklist = await self.get(
            id_=checklist_id, raise_exception_if_none=raise_exception_if_none
        )
        checklist.owner_id = new_owner_id
        self.session.add(checklist)
        await self.session.commit()
        await self.session.refresh(checklist)
        return checklist

    async def list_access_ids(
        self,
        user_id: uuid.UUID,
    ) -> List[uuid.UUID]:
        """List all IDs of CheckLists the user has access to, due to ownership or collaboration permissions

        Args:
            user_id (uuid.UUID): _description_

        Returns:
            List[uuid.UUID]: _description_
        """
        query = select(CheckList.id)
        query = self._add_user_has_access_query(query, user_id)
        results = await self.session.exec(statement=query)
        return results.all()

    async def list(
        self,
        user_id: uuid.UUID,
        include_sub_obj: bool = False,
        archived: Optional[bool] = None,
        label_id: Optional[uuid.UUID] = None,
        search: Optional[str] = None,
        shared: Optional[SharedFilter] = None,
        pagination: QueryParamsInterface = None,
    ) -> List[CheckList | CheckListApiWithSubObj]:
        query = select(CheckList)
        query = self._add_user_has_access_query(
            query, user_id, join_CheckListPosition=True
        )
        query = self._add_shared_filter(query, user_id, shared)

        if archived is not None:
            query = query.where(CheckListPosition.archived == archived)
        if label_id is not None:
            query = query.join(CheckListLabel).where(
                CheckListLabel.label_id == label_id
            )
        if search is not None:
            needle = f"%{search}%"
            item_match = (
                select(CheckListItem.id)
                .where(
                    and_(
                        CheckListItem.checklist_id == CheckList.id,
                        CheckListItem.text.ilike(needle),
                    )
                )
                .exists()
            )
            query = query.where(
                or_(
                    col(CheckList.name).ilike(needle),
                    col(CheckList.text).ilike(needle),
                    item_match,
                )
            )
        # Pinned checklists must come first across pagination so they all reach
        # the top group in the client. coalesce guards legacy NULL `pinned` rows
        # (Postgres would otherwise sort NULLs first under desc()).
        query = query.order_by(
            desc(func.coalesce(CheckListPosition.pinned, False)),
            desc(CheckListPosition.index),
        )
        if pagination:
            query = pagination.append_to_query(query)
        if include_sub_obj:
            query = query.options(selectinload(CheckList.position))
            query = query.options(selectinload(CheckList.color))
            # Eager-load labels so the per-user reassignment in the route does
            # not trigger an async lazy-load. They are loaded UNSCOPED here (the
            # CheckList.labels relationship spans the link table without its
            # per-user dimension, so it cannot be user-scoped at load time —
            # with_loader_criteria does not reach a m2m secondary). The caller
            # (list_checklists route) replaces them with the per-user set via
            # ChecklistLabelCRUD.list_labels_for_user_by_checklist.
            query = query.options(selectinload(CheckList.labels))
        # log.debug(f"list.checklist.query: {query}")
        results = await self.session.exec(statement=query)
        objs = results.all()
        return objs
