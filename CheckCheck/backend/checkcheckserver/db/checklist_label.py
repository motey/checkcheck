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
from sqlalchemy import and_

import uuid
from uuid import UUID


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.checklist_color_scheme import (
    ChecklistColorScheme,
)

from checkcheckserver.model.checklist_label import CheckListLabel, CheckListLabelCreate
from checkcheckserver.model.label import Label

from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface


log = get_logger()
config = Config()


class ChecklistLabelCRUD(
    create_crud_base(
        table_model=CheckListLabel,
        read_model=CheckListLabel,
        create_model=CheckListLabelCreate,
        update_model=CheckListLabel,
    )
):

    @staticmethod
    def _label_order():
        """Deterministic label order shared by every listing query.

        Without an explicit ORDER BY the database returns rows in an unspecified
        order that can change between fetches, so a card's label chips would
        reshuffle on any refetch (e.g. toggling "Separate checked items"). We
        sort by the per-user ``sort_order`` first — descending, to match the
        sidebar/label-editor order (``LabelCRUD.list`` uses ``desc(sort_order)``)
        so chips on a card read in the same order the user arranged them there.
        Labels the user hasn't ordered (NULL) go last, then fall back to name and
        id so the order is fully stable even for ties.
        """
        return (
            col(Label.sort_order).desc().nulls_last(),
            col(Label.display_name).asc(),
            col(Label.id).asc(),
        )

    async def list_labels_for_user(
        self,
        checklist_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> List[Label]:
        """Labels a single user has attached to a checklist.

        Labels are a per-user organisational layer: each ``CheckListLabel`` row
        carries the ``user_id`` of whoever attached it. The ``CheckList.labels``
        ORM relationship goes through the link table without that user dimension,
        so reading it on a shared card returns *every* collaborator's labels.
        Callers that render a card for one viewer must use this instead to avoid
        leaking other users' private labels.
        """
        query = (
            select(Label)
            .join(CheckListLabel, CheckListLabel.label_id == Label.id)
            .where(
                and_(
                    CheckListLabel.checklist_id == checklist_id,
                    CheckListLabel.user_id == user_id,
                    # Mask chips whose label was tombstoned (WI-2). The link row is
                    # a hard-delete-on-remove per-user association and is left in
                    # place when the label itself is soft-deleted, so filter here.
                    col(Label.deleted_at).is_(None),
                )
            )
            .order_by(*self._label_order())
        )
        results = await self.session.exec(query)
        return results.unique().all()

    async def list_labels_for_user_by_checklist(
        self,
        checklist_ids: List[uuid.UUID],
        user_id: uuid.UUID,
    ) -> Dict[uuid.UUID, List[Label]]:
        """Same as ``list_labels_for_user`` but for many checklists at once,
        grouped by checklist id — for the grid listing."""
        if not checklist_ids:
            return {}
        query = (
            select(CheckListLabel.checklist_id, Label)
            .join(CheckListLabel, CheckListLabel.label_id == Label.id)
            .where(
                and_(
                    col(CheckListLabel.checklist_id).in_(checklist_ids),
                    CheckListLabel.user_id == user_id,
                    col(Label.deleted_at).is_(None),
                )
            )
            .order_by(*self._label_order())
        )
        results = await self.session.exec(query)
        grouped: Dict[uuid.UUID, List[Label]] = {}
        for checklist_id, label in results.unique().all():
            grouped.setdefault(checklist_id, []).append(label)
        return grouped

    async def delete(
        self,
        label_id: uuid.UUID,
        user_id: uuid.UUID,
        checklist_id: uuid.UUID,
        raise_exception_if_not_exists: Exception = None,
    ):
        if raise_exception_if_not_exists:
            query = select(CheckListLabel).where(
                and_(
                    CheckListLabel.checklist_id == checklist_id,
                    CheckListLabel.user_id == user_id,
                    CheckListLabel.label_id == label_id,
                )
            )
            query_result = await self.session.exec(query)
            if query_result.one_or_none() is None:
                raise raise_exception_if_not_exists

        await self.session.exec(
            delete(CheckListLabel).where(
                and_(
                    CheckListLabel.checklist_id == checklist_id,
                    CheckListLabel.user_id == user_id,
                    CheckListLabel.label_id == label_id,
                )
            )
        )
