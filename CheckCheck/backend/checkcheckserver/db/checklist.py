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
        # Tombstoned cards (WI-2) never appear in any access-scoped listing. This
        # is the single choke point for list()/count()/label_counts()/
        # list_access_ids(), so masking here covers the whole grid + counts.
        query = query.where(col(CheckList.deleted_at).is_(None))
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

    async def label_counts(
        self,
        user_id: uuid.UUID,
    ) -> dict[uuid.UUID, int]:
        """Per-label count of the caller's non-archived accessible cards, as one
        grouped query (avoids an N+1 count() per label for the sidebar badges).

        Label assignments are per-user (``CheckListLabel`` carries ``user_id``),
        so the link join is scoped to the caller — a collaborator's labels on a
        shared card never leak into the caller's counts. Access + accepted-only
        gating come from ``_add_user_has_access_query`` (owner-or-collaborator +
        the CheckListPosition inner-join), matching the board's visibility.
        """
        query = select(CheckListLabel.label_id, func.count()).select_from(CheckList)
        query = query.where(CheckListPosition.archived == False)
        query = self._add_user_has_access_query(query, user_id)
        query = query.join(
            CheckListLabel,
            onclause=and_(
                CheckListLabel.checklist_id == CheckList.id,
                CheckListLabel.user_id == user_id,
            ),
        )
        query = query.group_by(CheckListLabel.label_id)
        results = await self.session.exec(statement=query)
        return {label_id: count for label_id, count in results.all()}

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

    async def list_changed_checklist_ids_for_user(
        self,
        user_id: uuid.UUID,
        since: int,
    ) -> List[uuid.UUID]:
        """Ids of the caller's *accessible* cards whose card-level state changed
        after ``since`` (WI-4 delta feed).

        "Card-level" means any of: the ``checklist`` row itself (name/color/text),
        the caller's own ``checklist_position`` (pin/archive/index), or one of the
        caller's ``checklist_label`` links — all three are what the client's
        checklist store renders per card. Item changes are handled separately (an
        item edit does not re-emit its card). Reuses ``_add_user_has_access_query``
        so tombstoned cards and cards the caller can't see are already excluded."""
        label_changed = (
            select(CheckListLabel.checklist_id)
            .where(
                and_(
                    CheckListLabel.checklist_id == CheckList.id,
                    CheckListLabel.user_id == user_id,
                    col(CheckListLabel.server_seq) > since,
                )
            )
            .exists()
        )
        query = select(CheckList.id)
        query = self._add_user_has_access_query(query, user_id)
        query = query.where(
            or_(
                col(CheckList.server_seq) > since,
                col(CheckListPosition.server_seq) > since,
                label_changed,
            )
        )
        results = await self.session.exec(statement=query)
        return list(results.all())

    async def list_tombstoned_checklist_ids_for_user(
        self,
        user_id: uuid.UUID,
        since: int,
    ) -> List[uuid.UUID]:
        """Ids of cards tombstoned after ``since`` that the caller could see
        (owner or accepted collaborator). The parent tombstone leaves collaborator
        rows in place (WI-2 cascade rule), so membership is still resolvable here;
        this cannot reuse ``_add_user_has_access_query`` because that masks
        ``deleted_at IS NOT NULL`` rows out."""
        is_collaborator = (
            select(CheckListCollaborator.checklist_id)
            .where(
                and_(
                    CheckListCollaborator.checklist_id == CheckList.id,
                    CheckListCollaborator.user_id == user_id,
                    CheckListCollaborator.status == ShareStatus.accepted.value,
                )
            )
            .exists()
        )
        query = select(CheckList.id).where(
            and_(
                col(CheckList.deleted_at).is_not(None),
                col(CheckList.server_seq) > since,
                or_(CheckList.owner_id == user_id, is_collaborator),
            )
        )
        results = await self.session.exec(statement=query)
        return list(results.all())

    async def list_full_by_ids_for_user(
        self,
        checklist_ids: List[uuid.UUID],
        user_id: uuid.UUID,
    ) -> List[CheckList]:
        """Load live cards by id with the caller's own position/color/labels eager
        loaded, for the delta feed's changed-card payload. Mirrors the per-user
        eager-load in ``list()`` (position scoped to the caller so a shared card
        never reports another user's pin/archive/index); the route replaces the
        unscoped labels with the caller's set and attaches ``my_permission``."""
        if not checklist_ids:
            return []
        query = (
            select(CheckList)
            .where(col(CheckList.id).in_(checklist_ids))
            .where(col(CheckList.deleted_at).is_(None))
            .options(
                selectinload(CheckList.position),
                with_loader_criteria(
                    CheckListPosition, CheckListPosition.user_id == user_id
                ),
                selectinload(CheckList.color),
                selectinload(CheckList.labels),
            )
        )
        results = await self.session.exec(statement=query)
        return list(results.all())

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
            # CheckListPosition is per-user: a shared card has N position rows
            # (one per collaborator + owner), but CheckList.position is a scalar
            # (uselist=False) relationship. An unscoped eager-load pulls every
            # user's row into the single slot, so SQLAlchemy warns and picks one
            # arbitrarily — a shared card could then report another user's
            # pinned/archived/index. Scope the load to the caller (mirroring the
            # already user-scoped access-query join) so each viewer sees their
            # own position.
            query = query.options(
                selectinload(CheckList.position),
                with_loader_criteria(
                    CheckListPosition, CheckListPosition.user_id == user_id
                ),
            )
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
