"""The outbox: enqueue, drain, prune.

Producers call :func:`enqueue` and are done. The dispatcher
(``notify/dispatcher.py``) calls :func:`drain_once` on a tick, which claims every
row that has come due, hands it to the transport and records the outcome.
:func:`prune_once` keeps the table from growing forever.

**How a row is claimed.** A drain first selects the due rows (with
``FOR UPDATE SKIP LOCKED`` on Postgres, which SQLite does not have), then claims
each one with a conditional update:

    UPDATE notification_outbox
       SET attempts = attempts + 1, not_before = <now + backoff>
     WHERE id = ? AND status = 'pending' AND attempts = ?

The row version in the ``WHERE`` clause is what actually prevents a double send:
exactly one caller can turn ``attempts`` from *n* to *n+1*, on both database
backends, and a second drain looking at the same row gets zero rows updated and
skips it. ``SKIP LOCKED`` only keeps concurrent drains from picking the same
candidates in the first place, which is cheaper but is not the guarantee.

Claiming also schedules the *next* attempt straight away, before the message is
handed to the transport. That way a process killed mid-send leaves behind a row
that retries later, rather than one stuck in a "sending" state that nothing ever
clears. The transport call itself happens outside any transaction, so a slow
mail server never holds a database lock.

Retry policy (section 4.2 of the plan): a transient failure leaves the row
``pending`` with a growing backoff until ``NOTIFY_MAX_ATTEMPTS`` is reached, then
it becomes ``failed``. A permanent failure becomes ``failed`` immediately.
Anything unexpected counts as transient, because retrying a message a few times
is cheaper than dropping it.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

from sqlmodel import col, delete, func, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from checkcheckserver.config import Config, DbBackend
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import naive_utc_now
from checkcheckserver.model.notification import Notification
from checkcheckserver.model.notification_outbox import (
    NotificationChannel,
    NotificationOutbox,
    NotificationOutboxStatus,
)
from checkcheckserver.notify.transports import (
    EmailTransport,
    OutgoingEmail,
    PermanentEmailError,
    TransientEmailError,
    get_email_transport,
    stable_message_id,
)

log = get_logger()


# Backoff between attempts: 1 minute, doubling, capped at an hour. Deliberately
# minutes rather than seconds: the failures this retries are "the mail server is
# down" and "greylisted, come back later", neither of which is fixed by hammering.
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 3600

# How many rows one drain takes on. Keeps a backlog from turning a single tick
# into a minutes-long loop; the dispatcher simply drains again.
DRAIN_BATCH_SIZE = 50


class OutboxPayloadError(ValueError):
    """The producer queued something the transport cannot possibly send."""


@dataclass
class DrainResult:
    """What one :func:`drain_once` pass did. Returned for logging and tests."""

    claimed: int = 0
    sent: int = 0
    retried: int = 0
    failed: int = 0
    cancelled: int = 0
    ids_sent: List[uuid.UUID] = field(default_factory=list)

    @property
    def handled(self) -> int:
        return self.sent + self.retried + self.failed + self.cancelled


def retry_delay_seconds(attempts: int) -> int:
    """Seconds to wait before attempt number *attempts* + 1.

    Doubling from :data:`RETRY_BASE_SECONDS`, capped at :data:`RETRY_MAX_SECONDS`.
    """
    if attempts < 1:
        return RETRY_BASE_SECONDS
    return min(RETRY_BASE_SECONDS * (2 ** (attempts - 1)), RETRY_MAX_SECONDS)


async def enqueue(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    channel: NotificationChannel,
    payload: dict,
    notification_id: Optional[uuid.UUID] = None,
    not_before: Optional[datetime.datetime] = None,
    dedupe_key: Optional[str] = None,
    commit: bool = True,
) -> NotificationOutbox:
    """Queue one delivery and return the row.

    *payload* is the snapshot the transport works from. For the email channel it
    must carry ``to``, ``subject`` and ``text_body``; ``html_body`` and
    ``headers`` are optional. A missing address is a programming error here, not
    a runtime outcome: a user without an address must produce no row at all
    (section 4.1.4 of the plan), which the *caller* decides.

    Pass ``commit=False`` to enlist the row in the caller's transaction, so a
    notification and its queued mail commit together or not at all.
    """
    if channel == NotificationChannel.email:
        _validate_email_payload(payload)

    row = NotificationOutbox(
        user_id=user_id,
        notification_id=notification_id,
        channel=channel,
        status=NotificationOutboxStatus.pending,
        not_before=not_before or naive_utc_now(),
        payload=payload,
        dedupe_key=dedupe_key,
    )
    session.add(row)
    if commit:
        await session.commit()
        await session.refresh(row)
    else:
        await session.flush()
    log.debug(
        "[notify] queued %s delivery %s for user %s (due %s)",
        row.channel,
        row.id,
        row.user_id,
        row.not_before,
    )
    return row


def _validate_email_payload(payload: dict) -> None:
    missing = [key for key in ("to", "subject", "text_body") if not payload.get(key)]
    if missing:
        raise OutboxPayloadError(
            f"Email payload is missing required key(s): {', '.join(missing)}"
        )


async def drain_once(
    session: AsyncSession,
    *,
    config: Optional[Config] = None,
    now: Optional[datetime.datetime] = None,
    limit: int = DRAIN_BATCH_SIZE,
    transport: Optional[EmailTransport] = None,
) -> DrainResult:
    """Deliver every row that is due, once.

    A plain async function taking a session on purpose: tests call it directly
    and never start the background loop, and moving delivery into a separate
    worker process later needs no change here (see decision 2 of the plan).

    *now* exists so tests can look at the queue from the future instead of
    sleeping through a backoff.
    """
    config = config or Config()
    now = now or naive_utc_now()
    transport = transport or get_email_transport(config)
    result = DrainResult()

    for row in await _due_rows(session, now=now, limit=limit, config=config):
        claimed = await _claim(session, row, now=now)
        if claimed is None:
            # Another drain got there first, or the row was cancelled meanwhile.
            continue
        result.claimed += 1
        await _deliver_claimed(
            session, claimed, config=config, now=now, transport=transport, result=result
        )
    return result


async def _due_rows(
    session: AsyncSession,
    *,
    now: datetime.datetime,
    limit: int,
    config: Config,
) -> List[NotificationOutbox]:
    """The rows that have come due, oldest first.

    ``FOR UPDATE SKIP LOCKED`` on Postgres so two drains (a second replica, or
    an overlapping tick) pick disjoint candidates instead of fighting over the
    same ones. SQLite has no such clause and no concurrent server anyway.
    """
    query = (
        select(NotificationOutbox)
        .where(NotificationOutbox.status == NotificationOutboxStatus.pending.value)
        .where(col(NotificationOutbox.not_before) <= now)
        .order_by(col(NotificationOutbox.not_before))
        .limit(limit)
    )
    if config.db_backend == DbBackend.POSTGRES:
        query = query.with_for_update(skip_locked=True)
    rows = list((await session.exec(query)).all())
    # End the read transaction before claiming: the claims are independent
    # single-row updates, and on SQLite holding a read lock while upgrading to a
    # write lock is exactly how two drains deadlock each other.
    await session.commit()
    return rows


async def _claim(
    session: AsyncSession,
    row: NotificationOutbox,
    *,
    now: datetime.datetime,
) -> Optional[NotificationOutbox]:
    """Take ownership of *row* for one attempt, or return None if someone else did.

    Compare-and-swap on ``(status, attempts)``. The same statement schedules the
    next attempt, so a crash between here and the transport call costs a delay,
    not a lost message.
    """
    attempts = row.attempts
    next_try = now + datetime.timedelta(seconds=retry_delay_seconds(attempts + 1))
    statement = (
        update(NotificationOutbox)
        .where(col(NotificationOutbox.id) == row.id)
        .where(col(NotificationOutbox.status) == NotificationOutboxStatus.pending.value)
        .where(col(NotificationOutbox.attempts) == attempts)
        .values(attempts=attempts + 1, not_before=next_try, updated_at=now)
    )
    claimed = (await session.exec(statement)).rowcount
    await session.commit()
    if not claimed:
        return None
    # Mirror the update onto the in-memory row instead of re-reading it: a core
    # UPDATE bypasses the ORM (which is what keeps this table out of the sync
    # sequence), so the loaded instance is otherwise stale.
    row.attempts = attempts + 1
    row.not_before = next_try
    row.updated_at = now
    return row


async def _deliver_claimed(
    session: AsyncSession,
    row: NotificationOutbox,
    *,
    config: Config,
    now: datetime.datetime,
    transport: EmailTransport,
    result: DrainResult,
) -> None:
    """Send one claimed row and write down what happened."""
    if await _is_already_read(session, row):
        # Suppression rule 1 (section 4.1): the recipient saw it in the app
        # before the mail became due, so sending it now would only be noise.
        await _finish(session, row, NotificationOutboxStatus.cancelled, now=now)
        result.cancelled += 1
        log.debug("[notify] %s cancelled, notification already read", row.id)
        return

    if row.channel != NotificationChannel.email.value:
        # The webhook channel arrives in chunk E6. Until then a row for it is a
        # bug in a producer, not something to retry forever.
        await _finish(
            session,
            row,
            NotificationOutboxStatus.failed,
            now=now,
            error=f"Channel '{row.channel}' is not implemented yet.",
        )
        result.failed += 1
        return

    try:
        await transport.send(_email_from_row(row, config))
    except PermanentEmailError as exc:
        await _finish(
            session, row, NotificationOutboxStatus.failed, now=now, error=str(exc)
        )
        result.failed += 1
        log.warning("[notify] %s permanently failed: %s", row.id, exc)
        return
    except OutboxPayloadError as exc:
        # An unsendable payload cannot become sendable on a retry.
        await _finish(
            session, row, NotificationOutboxStatus.failed, now=now, error=str(exc)
        )
        result.failed += 1
        log.warning("[notify] %s has an unusable payload: %s", row.id, exc)
        return
    except Exception as exc:  # includes TransientEmailError
        if not isinstance(exc, TransientEmailError):
            # Never let an unexpected error from a transport kill the dispatcher
            # loop, and never drop the message over it either.
            log.exception("[notify] unexpected error delivering %s", row.id)
        await _record_transient_failure(session, row, config=config, now=now, error=exc)
        if row.attempts >= config.NOTIFY_MAX_ATTEMPTS:
            result.failed += 1
        else:
            result.retried += 1
        return

    await _finish(session, row, NotificationOutboxStatus.sent, now=now)
    result.sent += 1
    result.ids_sent.append(row.id)
    log.debug("[notify] delivered %s to user %s", row.id, row.user_id)


async def _is_already_read(session: AsyncSession, row: NotificationOutbox) -> bool:
    if row.notification_id is None:
        return False
    # populate_existing: the answer has to come from the database, not from a
    # copy this session loaded before the user opened the app.
    notification = await session.get(
        Notification, row.notification_id, populate_existing=True
    )
    return notification is not None and notification.read_at is not None


def _email_from_row(row: NotificationOutbox, config: Config) -> OutgoingEmail:
    """Rebuild the message from the snapshot taken at enqueue time.

    The ``Message-ID`` is derived from the row id, so a message that is retried
    keeps the identity it had on the first attempt and a mail client recognises
    the duplicate instead of showing it twice.
    """
    payload = row.payload or {}
    _validate_email_payload(payload)
    return OutgoingEmail(
        to=payload["to"],
        subject=payload["subject"],
        text_body=payload["text_body"],
        html_body=payload.get("html_body"),
        message_id=stable_message_id(str(row.id), config),
        headers=dict(payload.get("headers") or {}),
    )


async def _record_transient_failure(
    session: AsyncSession,
    row: NotificationOutbox,
    *,
    config: Config,
    now: datetime.datetime,
    error: Exception,
) -> None:
    """Leave the row for another attempt, or give up once the budget is spent."""
    message = str(error) or error.__class__.__name__
    if row.attempts >= config.NOTIFY_MAX_ATTEMPTS:
        await _finish(
            session,
            row,
            NotificationOutboxStatus.failed,
            now=now,
            error=f"Gave up after {row.attempts} attempts. Last error: {message}",
        )
        log.warning(
            "[notify] %s failed after %s attempts: %s", row.id, row.attempts, message
        )
        return
    # `not_before` already carries the next attempt's backoff (set when the row
    # was claimed), so only the error note has to be written.
    await session.exec(
        update(NotificationOutbox)
        .where(col(NotificationOutbox.id) == row.id)
        .values(last_error=message[:1000], updated_at=now)
    )
    await session.commit()
    row.last_error = message[:1000]
    log.info(
        "[notify] %s attempt %s failed, retrying at %s: %s",
        row.id,
        row.attempts,
        row.not_before,
        message,
    )


async def _finish(
    session: AsyncSession,
    row: NotificationOutbox,
    status: NotificationOutboxStatus,
    *,
    now: datetime.datetime,
    error: Optional[str] = None,
) -> None:
    values = {"status": status.value, "updated_at": now}
    if error is not None:
        values["last_error"] = error[:1000]
    await session.exec(
        update(NotificationOutbox)
        .where(col(NotificationOutbox.id) == row.id)
        .values(**values)
    )
    await session.commit()
    row.status = status
    row.updated_at = now
    if error is not None:
        row.last_error = error[:1000]


async def prune_once(
    session: AsyncSession,
    *,
    config: Optional[Config] = None,
    now: Optional[datetime.datetime] = None,
) -> int:
    """Delete finished rows that are past their retention. Returns the row count.

    ``sent`` and ``cancelled`` rows are noise once they are old enough.
    ``failed`` rows are deliberately kept: they are the only record an operator
    has of mail that never arrived. ``NOTIFY_OUTBOX_RETENTION_DAYS = 0`` keeps
    everything.
    """
    config = config or Config()
    if config.NOTIFY_OUTBOX_RETENTION_DAYS <= 0:
        return 0
    now = now or naive_utc_now()
    cutoff = now - datetime.timedelta(days=config.NOTIFY_OUTBOX_RETENTION_DAYS)
    statement = (
        delete(NotificationOutbox)
        .where(
            col(NotificationOutbox.status).in_(
                [
                    NotificationOutboxStatus.sent.value,
                    NotificationOutboxStatus.cancelled.value,
                ]
            )
        )
        .where(col(NotificationOutbox.updated_at) < cutoff)
    )
    removed = (await session.exec(statement)).rowcount
    await session.commit()
    if removed:
        log.debug("[notify] pruned %s finished outbox rows", removed)
    return removed


async def count_recent(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    dedupe_key: str,
    since: datetime.datetime,
) -> int:
    """How many rows a user queued under *dedupe_key* since *since*.

    The rate limit of a producer that a user can trigger directly (the test
    mail) is enforced against this, so the limit lives in the database and
    survives a restart instead of in per-process memory.
    """
    query = (
        select(func.count())
        .select_from(NotificationOutbox)
        .where(NotificationOutbox.user_id == user_id)
        .where(NotificationOutbox.dedupe_key == dedupe_key)
        .where(col(NotificationOutbox.created_at) >= since)
    )
    return (await session.exec(query)).one()
