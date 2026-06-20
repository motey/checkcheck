"""CRUD for public URL shares (Phase 5 of card sharing).

See ``checkcheckserver.model.checklist_public_share`` for the capability model.
``list_active_tokens`` is the one used by the sync fan-out to address anonymous
viewers (enabled + not expired).
"""

import datetime
import uuid
from typing import List, Optional

from sqlmodel import select, delete, and_, or_, col

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.model.checklist_public_share import (
    CheckListPublicShare,
    CheckListPublicShareCreate,
)


log = get_logger()
config = Config()


def _utcnow() -> datetime.datetime:
    # Naive UTC to match the timestamps stored on the model (see TimestampedModel).
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class CheckListPublicShareCRUD(
    create_crud_base(
        table_model=CheckListPublicShare,
        read_model=CheckListPublicShare,
        create_model=CheckListPublicShareCreate,
        update_model=CheckListPublicShare,
    )
):

    async def get_by_token(self, token: str) -> Optional[CheckListPublicShare]:
        query = select(CheckListPublicShare).where(
            CheckListPublicShare.token == token
        )
        results = await self.session.exec(query)
        return results.one_or_none()

    async def list_for_checklist(
        self, checklist_id: uuid.UUID
    ) -> List[CheckListPublicShare]:
        query = select(CheckListPublicShare).where(
            CheckListPublicShare.checklist_id == checklist_id
        )
        results = await self.session.exec(query)
        return results.all()

    async def list_active_tokens(self, checklist_id: uuid.UUID) -> List[str]:
        """Tokens that can currently resolve (enabled + not expired). Used by the
        sync fan-out to deliver live updates to connected anonymous viewers."""
        now = _utcnow()
        query = select(CheckListPublicShare.token).where(
            and_(
                CheckListPublicShare.checklist_id == checklist_id,
                CheckListPublicShare.enabled == True,  # noqa: E712 (SQL boolean)
                or_(
                    col(CheckListPublicShare.expires_at).is_(None),
                    CheckListPublicShare.expires_at > now,
                ),
            )
        )
        results = await self.session.exec(query)
        return list(results.all())

    async def delete_for_checklist(self, checklist_id: uuid.UUID) -> None:
        await self.session.exec(
            delete(CheckListPublicShare).where(
                CheckListPublicShare.checklist_id == checklist_id
            )
        )
        await self.session.commit()
