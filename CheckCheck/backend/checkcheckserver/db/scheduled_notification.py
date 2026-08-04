"""Reading and writing reminders (chunk R1 of the date-reminder plan).

Plain functions taking a session, like ``notify/outbox.py`` and
``db/user_notification_settings.py`` rather than a ``create_crud_base``
subclass. Two callers with very different shapes have to share this module: the
API routes (chunk R3), which hold a request session, and the dispatcher's scan
(chunk R2), which runs on a background session with no request and no user. A
free function is the only thing both can call without pretending to be the other.

Two rules the rest of the module is built around:

**Every user-facing read and write is ownership-scoped.** A reminder belongs to
the user who set it (plan decision 1), so the functions the API calls take a
``user_id`` and filter on it rather than checking afterwards. That way a route
cannot forget, and "somebody else's reminder" comes back as ``None``, which the
route turns into a 404 rather than a 403: the existence of another user's
reminder is not something a caller may learn.

**Nothing here checks whether the card is still visible.** That check belongs at
fire time and only at fire time (plan decision 2), where it is one query against
the access predicate that already exists. A CRUD that also enforced access would
be a second, quietly divergent copy of it.
"""

from __future__ import annotations

import datetime
import uuid
from typing import List, Optional

from sqlmodel import col, delete, func, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from checkcheckserver.config import Config, DbBackend
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import naive_utc_now
from checkcheckserver.model.scheduled_notification import (
    ReminderRecurrence,
    ReminderStatus,
    ScheduledNotification,
)
from checkcheckserver.notify.recurrence import anchor_day_for


log = get_logger()


# Caps, as module constants rather than settings (the `schedule.DAILY_DIGEST_HOUR`
# precedent). They exist so one account cannot fill the table, not so an operator
# can tune them; if somebody ever needs them tuned they become real settings then.
MAX_PENDING_PER_CARD = 10
MAX_PENDING_PER_USER = 200

# How long a finished reminder is kept. The notification it produced is the
# durable record of what happened; the schedule row is scaffolding, and keeping it
# around only so a user can see a reminder they already received is worth a month,
# not forever.
REMINDER_RETENTION_DAYS = 30


async def create_reminder(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    cl_id: uuid.UUID,
    remind_at: datetime.datetime,
    timezone: Optional[str] = None,
    recurrence: ReminderRecurrence = ReminderRecurrence.none,
    note: Optional[str] = None,
    commit: bool = True,
) -> ScheduledNotification:
    """Store one reminder. *remind_at* is naive UTC.

    The monthly anchor is derived here rather than by the caller, so every path
    that creates a reminder gets it right: a reminder set for the 31st has to
    remember that it means the 31st even after firing in February.
    """
    row = ScheduledNotification(
        user_id=user_id,
        cl_id=cl_id,
        remind_at=remind_at,
        timezone=timezone,
        recurrence=recurrence,
        note=note,
        status=ReminderStatus.pending,
        anchor_day=anchor_day_for(
            remind_at, recurrence=recurrence, timezone_name=timezone
        ),
    )
    session.add(row)
    if commit:
        await session.commit()
        await session.refresh(row)
    return row


async def get_reminder(
    session: AsyncSession, reminder_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[ScheduledNotification]:
    """One reminder, but only if it is the caller's. None otherwise."""
    result = await session.exec(
        select(ScheduledNotification)
        .where(ScheduledNotification.id == reminder_id)
        .where(ScheduledNotification.user_id == user_id)
    )
    return result.one_or_none()


async def list_for_card(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    cl_id: uuid.UUID,
    include_finished: bool = False,
) -> List[ScheduledNotification]:
    """The caller's own reminders on one card, soonest first.

    Finished ones are left out by default: what the card editor asks is "what is
    still going to happen", and a list that also carries last month's fired
    reminders answers a different question.
    """
    query = (
        select(ScheduledNotification)
        .where(ScheduledNotification.user_id == user_id)
        .where(ScheduledNotification.cl_id == cl_id)
    )
    if not include_finished:
        query = query.where(
            ScheduledNotification.status == ReminderStatus.pending.value
        )
    query = query.order_by(col(ScheduledNotification.remind_at))
    return list((await session.exec(query)).all())


async def list_upcoming_for_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int = 100,
) -> List[ScheduledNotification]:
    """Everything still pending for one user, across cards, soonest first."""
    query = (
        select(ScheduledNotification)
        .where(ScheduledNotification.user_id == user_id)
        .where(ScheduledNotification.status == ReminderStatus.pending.value)
        .order_by(col(ScheduledNotification.remind_at))
        .limit(limit)
    )
    return list((await session.exec(query)).all())


async def count_pending_for_card(
    session: AsyncSession, *, user_id: uuid.UUID, cl_id: uuid.UUID
) -> int:
    """How many pending reminders the caller has on one card (the per-card cap)."""
    result = await session.exec(
        select(func.count())
        .select_from(ScheduledNotification)
        .where(ScheduledNotification.user_id == user_id)
        .where(ScheduledNotification.cl_id == cl_id)
        .where(ScheduledNotification.status == ReminderStatus.pending.value)
    )
    return result.one()


async def count_pending_for_user(session: AsyncSession, *, user_id: uuid.UUID) -> int:
    """How many pending reminders the caller has in total (the per-user cap)."""
    result = await session.exec(
        select(func.count())
        .select_from(ScheduledNotification)
        .where(ScheduledNotification.user_id == user_id)
        .where(ScheduledNotification.status == ReminderStatus.pending.value)
    )
    return result.one()


async def delete_reminder(
    session: AsyncSession, reminder_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Remove one of the caller's reminders. True if there was one to remove.

    A real ``DELETE``: this table is not syncable, so there is no offline client
    that could resurrect the row and nothing a tombstone would buy.
    """
    statement = (
        delete(ScheduledNotification)
        .where(col(ScheduledNotification.id) == reminder_id)
        .where(col(ScheduledNotification.user_id) == user_id)
    )
    removed = (await session.exec(statement)).rowcount
    await session.commit()
    return bool(removed)


async def due_rows(
    session: AsyncSession,
    *,
    now: Optional[datetime.datetime] = None,
    limit: int = 100,
    config: Optional[Config] = None,
) -> List[ScheduledNotification]:
    """Pending reminders whose time has come, oldest first.

    ``FOR UPDATE SKIP LOCKED`` on Postgres so two scans (a second replica, or an
    overlapping tick) pick disjoint candidates. The read transaction is closed
    before the caller starts claiming, for the same reason the outbox does it:
    on SQLite, holding a read lock while upgrading to a write lock is how two
    scans deadlock each other.
    """
    config = config or Config()
    now = now or naive_utc_now()
    query = (
        select(ScheduledNotification)
        .where(ScheduledNotification.status == ReminderStatus.pending.value)
        .where(col(ScheduledNotification.remind_at) <= now)
        .order_by(col(ScheduledNotification.remind_at))
        .limit(limit)
    )
    if config.db_backend == DbBackend.POSTGRES:
        query = query.with_for_update(skip_locked=True)
    rows = list((await session.exec(query)).all())
    await session.commit()
    return rows


async def claim_due(
    session: AsyncSession,
    row: ScheduledNotification,
    *,
    next_remind_at: Optional[datetime.datetime],
    now: Optional[datetime.datetime] = None,
) -> bool:
    """Take ownership of one due occurrence, or return False if somebody else did.

    Compare-and-swap on ``(status, remind_at)``, the outbox's claim pattern.
    **The claim is also the roll forward**: a recurring row leaves this call
    already pointing at *next_remind_at* and still ``pending``, a one-off
    (*next_remind_at* is ``None``) leaves it ``done``. That ordering is the whole
    point. Claiming into some provisional state instead would mean a crash
    between here and the emit leaves a recurring reminder parked in that state
    forever, which is a far worse failure than the one it prevents: this way the
    cost of a crash is one missed occurrence, and the reminder keeps working.

    The caller emits afterwards and then calls :func:`mark_fired`, or
    :func:`cancel` if the fire-time check said the card is gone.
    """
    now = now or naive_utc_now()
    values: dict = {"updated_at": now}
    if next_remind_at is not None:
        values["remind_at"] = next_remind_at
    else:
        values["status"] = ReminderStatus.done.value

    statement = (
        update(ScheduledNotification)
        .where(col(ScheduledNotification.id) == row.id)
        .where(col(ScheduledNotification.status) == ReminderStatus.pending.value)
        .where(col(ScheduledNotification.remind_at) == row.remind_at)
        .values(**values)
    )
    claimed = (await session.exec(statement)).rowcount
    await session.commit()
    if not claimed:
        return False
    # Mirror the update onto the in-memory row rather than re-reading it: a core
    # UPDATE bypasses the ORM, so the loaded instance is otherwise stale.
    if next_remind_at is not None:
        row.remind_at = next_remind_at
    else:
        row.status = ReminderStatus.done
    row.updated_at = now
    return True


async def mark_fired(
    session: AsyncSession,
    row: ScheduledNotification,
    *,
    now: Optional[datetime.datetime] = None,
) -> ScheduledNotification:
    """Record that a claimed occurrence was delivered. Counters only.

    Separate from the claim because a claimed occurrence does not always fire:
    the access check between the two can cancel it, and a ``fire_count`` that
    counted those would be a lie in the one place somebody would look to find out
    whether their reminder ever reached them.
    """
    now = now or naive_utc_now()
    # Computed once and then assigned, never recomputed from the instance: an
    # ORM-enabled UPDATE run through the session synchronises the objects it
    # already holds, so ``row.fire_count`` is *already* the new value by the time
    # the mirroring below runs. Reading it again there would count the fire twice.
    fire_count = row.fire_count + 1
    await session.exec(
        update(ScheduledNotification)
        .where(col(ScheduledNotification.id) == row.id)
        .values(last_fired_at=now, fire_count=fire_count, updated_at=now)
    )
    await session.commit()
    row.last_fired_at = now
    row.fire_count = fire_count
    row.updated_at = now
    return row


async def cancel(
    session: AsyncSession,
    row: ScheduledNotification,
    *,
    now: Optional[datetime.datetime] = None,
) -> ScheduledNotification:
    """Stop a reminder for good: the card is gone, or the user's access is.

    Applies to a recurring reminder as much as a one-off. A card somebody can no
    longer open will not become openable again by waiting a week, and if access
    is granted back, setting a new reminder is one click.
    """
    now = now or naive_utc_now()
    await session.exec(
        update(ScheduledNotification)
        .where(col(ScheduledNotification.id) == row.id)
        .values(status=ReminderStatus.cancelled.value, updated_at=now)
    )
    await session.commit()
    row.status = ReminderStatus.cancelled
    row.updated_at = now
    return row


async def prune_reminders_once(
    session: AsyncSession,
    *,
    now: Optional[datetime.datetime] = None,
    retention_days: int = REMINDER_RETENTION_DAYS,
) -> int:
    """Delete finished reminders past their retention. Returns the row count.

    Only ``done`` and ``cancelled`` rows, which is every row that can never fire
    again. A pending one is somebody's future and is never touched here, however
    old it is: a reminder set two years out is not stale, it is patient.
    """
    if retention_days <= 0:
        return 0
    now = now or naive_utc_now()
    cutoff = now - datetime.timedelta(days=retention_days)
    statement = (
        delete(ScheduledNotification)
        .where(
            col(ScheduledNotification.status).in_(
                [ReminderStatus.done.value, ReminderStatus.cancelled.value]
            )
        )
        .where(col(ScheduledNotification.updated_at) < cutoff)
    )
    removed = (await session.exec(statement)).rowcount
    await session.commit()
    if removed:
        log.debug("[reminders] pruned %s finished reminders", removed)
    return removed
