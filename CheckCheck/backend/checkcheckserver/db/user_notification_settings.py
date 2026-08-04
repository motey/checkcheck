"""Reading and writing one user's notification settings row (chunk E3).

Plain functions rather than a ``create_crud_base`` subclass: the table has no
surrogate ``id`` (the user is the key), there is no list endpoint and no admin
view, and the only two operations anybody needs are "read mine" and "write mine".

**Rows are created lazily, on write only.** A user who never touched their
settings has no row, and :func:`get_settings` returns ``None`` for them, which
the resolver reads as "everything inherits the instance default". Deliberately
not created on read either: ``GET`` is called by any client that opens the
settings dialog, and a read endpoint that writes a row is both a surprise in the
logs and a write amplifier. The plan allows either; this is the quieter half.
"""

import uuid
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from checkcheckserver.log import get_logger
from checkcheckserver.model.user_notification_settings import UserNotificationSettings


log = get_logger()


async def get_settings(
    session: AsyncSession, user_id: uuid.UUID
) -> Optional[UserNotificationSettings]:
    """The user's settings row, or None when they have never saved any."""
    result = await session.exec(
        select(UserNotificationSettings).where(
            UserNotificationSettings.user_id == user_id
        )
    )
    return result.one_or_none()


async def get_or_create_settings(
    session: AsyncSession, user_id: uuid.UUID
) -> UserNotificationSettings:
    """The user's settings row, created (with a fresh unsubscribe secret) if new.

    The insert can lose a race with a concurrent request for the same user (two
    browser tabs saving at once), in which case the primary key rejects it and
    the winner's row is read back instead. Cheaper and more honest than a lock:
    the loser has nothing to redo, since the caller writes its own fields
    afterwards.
    """
    existing = await get_settings(session, user_id)
    if existing is not None:
        return existing

    row = UserNotificationSettings(user_id=user_id)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await get_settings(session, user_id)
        if existing is None:
            raise
        log.debug(
            "[notify] settings row for user %s was created concurrently", user_id
        )
        return existing
    await session.refresh(row)
    return row
