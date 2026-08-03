"""Reading and writing push subscriptions (chunk P1 of the system-notifications
plan).

Plain functions, like ``db/user_notification_settings.py``: there is no admin
view and the only operations anybody needs are "list mine", "add or refresh
mine" and "remove mine".
"""

import uuid
from typing import List, Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import naive_utc_now
from checkcheckserver.model.push_subscription import PushSubscription


log = get_logger()


async def list_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> List[PushSubscription]:
    query = (
        select(PushSubscription)
        .where(PushSubscription.user_id == user_id)
        .order_by(col(PushSubscription.created_at))
    )
    return list((await session.exec(query)).all())


async def get_by_endpoint(
    session: AsyncSession, endpoint: str
) -> Optional[PushSubscription]:
    query = select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    return (await session.exec(query)).one_or_none()


async def upsert(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: Optional[str],
) -> PushSubscription:
    """Register *endpoint* for *user_id*, or refresh it if it already exists.

    ``endpoint`` is globally unique, so re-subscribing the same browser (the
    common case: a page that calls ``subscribe`` again on every load to check
    permission) updates the existing row in place rather than accumulating
    duplicates that would all push to the same, now-redundant, target.

    A concurrent first subscribe from two tabs can lose the insert race; the
    loser reads the winner's row back and updates it, the same pattern
    ``get_or_create_settings`` uses.
    """
    existing = await get_by_endpoint(session, endpoint)
    if existing is not None:
        existing.user_id = user_id
        existing.p256dh = p256dh
        existing.auth = auth
        existing.user_agent = user_agent
        existing.last_seen_at = naive_utc_now()
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        return existing

    row = PushSubscription(
        user_id=user_id,
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        user_agent=user_agent,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await get_by_endpoint(session, endpoint)
        if winner is None:
            raise
        winner.user_id = user_id
        winner.p256dh = p256dh
        winner.auth = auth
        winner.user_agent = user_agent
        winner.last_seen_at = naive_utc_now()
        session.add(winner)
        await session.commit()
        await session.refresh(winner)
        return winner
    await session.refresh(row)
    return row


async def delete_for_user(
    session: AsyncSession, *, subscription_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Remove one subscription, but only if it belongs to *user_id*.

    Returns whether a row was actually removed, so the endpoint can tell "not
    yours" and "already gone" apart from "gone now".
    """
    statement = delete(PushSubscription).where(
        col(PushSubscription.id) == subscription_id,
        col(PushSubscription.user_id) == user_id,
    )
    removed = (await session.exec(statement)).rowcount
    await session.commit()
    return bool(removed)


async def delete_by_endpoint(session: AsyncSession, endpoint: str) -> None:
    """Drop a subscription the push service reported as gone (404/410)."""
    await session.exec(delete(PushSubscription).where(PushSubscription.endpoint == endpoint))
    await session.commit()


async def touch(session: AsyncSession, subscription_id: uuid.UUID) -> None:
    """Bump ``last_seen_at`` after a successful delivery."""
    from sqlmodel import update

    await session.exec(
        update(PushSubscription)
        .where(col(PushSubscription.id) == subscription_id)
        .values(last_seen_at=naive_utc_now())
    )
    await session.commit()


async def count_for_user(session: AsyncSession, user_id: uuid.UUID) -> int:
    from sqlmodel import func

    query = (
        select(func.count())
        .select_from(PushSubscription)
        .where(PushSubscription.user_id == user_id)
    )
    return (await session.exec(query)).one()
