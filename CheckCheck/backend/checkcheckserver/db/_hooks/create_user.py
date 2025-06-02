import uuid

from checkcheckserver.model.label import Label
from checkcheckserver.db.label import LabelCRUD
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from sqlmodel.ext.asyncio.session import AsyncSession

log = get_logger()
config = Config()


async def create_new_user_default_labels(
    session: AsyncSession,
    user_id: uuid.UUID,
):
    async with LabelCRUD.crud_context(session) as label_crud:

        labels = []
        i = 10
        for label_name in config.NEW_USER_DEFAULT_LABELS:
            labels.append(
                Label(display_name=label_name, sort_order=i, owner_id=user_id)
            )
            i = i + 10
        await label_crud.create_bulk(labels)
