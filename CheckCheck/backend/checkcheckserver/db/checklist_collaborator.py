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
    ShareStatus,
)
from checkcheckserver.model.checklist import CheckList
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

    async def permissions_for_user_by_checklist(
        self,
        checklist_ids: List[uuid.UUID],
        user_id: uuid.UUID,
    ) -> dict[uuid.UUID, str]:
        """Map each checklist id to the user's *accepted* collaborator permission.

        Used to populate ``my_permission`` (P0.1) for the whole grid in a single
        query. Cards the user owns are absent (the owner is not a collaborator —
        the caller fills in ``"owner"`` for those); pending/declined invites grant
        no access and are excluded."""
        if not checklist_ids:
            return {}
        query = select(
            CheckListCollaborator.checklist_id, CheckListCollaborator.permission
        ).where(
            CheckListCollaborator.user_id == user_id,
            col(CheckListCollaborator.checklist_id).in_(checklist_ids),
            CheckListCollaborator.status == ShareStatus.accepted.value,
        )
        results = await self.session.exec(statement=query)
        return {checklist_id: permission for checklist_id, permission in results.all()}

    async def upsert(
        self,
        checklist_id: uuid.UUID,
        user_id: uuid.UUID,
        permission,
        status: ShareStatus = ShareStatus.accepted,
    ) -> CheckListCollaborator:
        """Create the collaborator or, if it already exists, update its permission
        and status.

        ``status`` defaults to ``accepted`` so every existing caller (instant-add
        sharing, public-link join, ownership-transfer demotion) keeps granting
        access immediately. The invite flow passes ``pending`` explicitly to arm an
        unaccepted invite (and to re-arm a previously ``declined``/``pending`` row)."""
        existing = await self.get_one(checklist_id=checklist_id, user_id=user_id)
        if existing is not None:
            existing.permission = permission
            existing.status = status
            self.session.add(existing)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        return await self.create(
            CheckListCollaboratorCreate(
                checklist_id=checklist_id,
                user_id=user_id,
                permission=permission,
                status=status,
            )
        )

    async def set_status(
        self,
        checklist_id: uuid.UUID,
        user_id: uuid.UUID,
        status: ShareStatus,
    ) -> Optional[CheckListCollaborator]:
        """Flip an existing collaborator row's status (accept / decline an invite).
        Returns None if no such row exists."""
        existing = await self.get_one(checklist_id=checklist_id, user_id=user_id)
        if existing is None:
            return None
        existing.status = status
        self.session.add(existing)
        await self.session.commit()
        await self.session.refresh(existing)
        return existing

    async def list_pending_for_user(
        self,
        user_id: uuid.UUID,
    ) -> List[Tuple[CheckListCollaborator, CheckList]]:
        """The user's pending invites joined with the card they were invited to,
        newest first — enough to render an invite inbox. The card's owner is the
        inviter (only an owner can share)."""
        query = (
            select(CheckListCollaborator, CheckList)
            .join(CheckList, CheckList.id == CheckListCollaborator.checklist_id)
            .where(
                CheckListCollaborator.user_id == user_id,
                CheckListCollaborator.status == ShareStatus.pending.value,
                # A pending invite to a tombstoned card (WI-2) — its owner deleted
                # it before the invitee acted — must drop out of the inbox. The
                # collaborator row is left in place by the parent tombstone, so
                # filter on the card here.
                col(CheckList.deleted_at).is_(None),
            )
            .order_by(col(CheckListCollaborator.created_at).desc())
        )
        results = await self.session.exec(statement=query)
        # CheckList eager-loads collection relationships (labels), so the joined
        # result rows must be de-duplicated with unique() before materialising.
        return list(results.unique().all())

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
