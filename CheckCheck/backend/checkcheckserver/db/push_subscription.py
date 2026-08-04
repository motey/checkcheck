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


# How many devices one account may keep subscribed at once. A module constant in
# the ``scheduled_notification.MAX_PENDING_PER_USER`` style rather than a setting:
# it exists so one account cannot fill the table and stall the dispatcher loop
# everybody's mail also goes through (finding 4 of the notification review), not
# so an operator can tune it. Generous on purpose, a user with a desktop, a
# laptop, a phone and a tablet, each with two browsers, is still well under it.
MAX_SUBSCRIPTIONS_PER_USER = 15


class EndpointOwnedByAnotherUser(Exception):
    """Raised by ``upsert`` when *endpoint* already belongs to a different account.

    Possession of an endpoint is not authorisation to take it over: re-binding it
    would silently stop the other account's device receiving push, and from then
    on encrypt for the wrong keys (finding 3). The route turns this into a 409 and
    the browser resubscribes to get a fresh endpoint.
    """


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

    Raises ``EndpointOwnedByAnotherUser`` if the endpoint is already somebody
    else's. ``user_id`` is never re-assigned on an existing row, in either the
    plain path or the race path below: an endpoint belongs to the account that
    first registered it until that account (or a 404/410 from the push service)
    lets it go.
    """
    existing = await get_by_endpoint(session, endpoint)
    if existing is not None:
        if existing.user_id != user_id:
            raise EndpointOwnedByAnotherUser()
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
        if winner.user_id != user_id:
            # The same rule as above, and the easy one to miss: losing the insert
            # race to another account is exactly the takeover the check exists
            # for, it just arrived a millisecond later.
            raise EndpointOwnedByAnotherUser()
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


async def delete_by_id(session: AsyncSession, subscription_id: uuid.UUID) -> None:
    """Drop a subscription the push service reported as gone (404/410).

    By primary key rather than by endpoint (finding 8): the caller is holding the
    row it just failed to reach, and the endpoint is only a second way of naming
    the same thing that stops being the same thing the moment the row changes
    underneath the drain.
    """
    await session.exec(
        delete(PushSubscription).where(col(PushSubscription.id) == subscription_id)
    )
    await session.commit()


async def delete_oldest_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> Optional[PushSubscription]:
    """Drop *user_id*'s least recently seen device, and return it for logging.

    Scoped to the one user, so making room for a new device can never take one
    away from somebody else. Returns ``None`` when the user has no device left,
    which only happens if a concurrent delete got there first.
    """
    query = (
        select(PushSubscription)
        .where(PushSubscription.user_id == user_id)
        .order_by(col(PushSubscription.last_seen_at))
        .limit(1)
    )
    oldest = (await session.exec(query)).one_or_none()
    if oldest is None:
        return None
    await session.delete(oldest)
    await session.commit()
    return oldest


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
