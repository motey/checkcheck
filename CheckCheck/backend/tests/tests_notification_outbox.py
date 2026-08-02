"""Tests for chunk E2 of the notification sub-project: outbox and dispatcher.

Most of these run **in this process**, against the same database the live server
uses: they call ``drain_once()`` directly with a capturing transport installed
and assert on the message that came out. That is the point of the seam. The test
server is booted with ``NOTIFY_DISPATCH_IN_PROCESS=False`` (see ``conftest``), so
it never drains the queue behind a test's back and mail queued over HTTP is still
there to be delivered here.

**Isolation trick.** ``drain_once()`` delivers every row that is due, including
rows other tests left behind. Each test therefore works in its own *time window*
far in the past: it queues its row with a ``not_before`` in, say, 2001 and drains
with ``now`` set to that same instant, so no row belonging to another test (whose
``not_before`` is the real now) is due yet. Assertions still filter captured mail
by a per-test subject, so nothing depends on the queue being empty.

The plan is ``docs/plans/EMAIL_NOTIFICATIONS.md`` (sections 3.2, 4, 4.2, 7 E2).
"""

import asyncio
import datetime
import uuid

import pytest

from utils import req
from statics import ADMIN_USER_EMAIL, MAIL_CAPTURE_FROM_ADDRESS


# ── helpers ───────────────────────────────────────────────────────────────────


def _run(body, *, sessions: int = 1):
    """Run *body* with fresh session(s) against the test database.

    Imports the engine lazily: the module-level ``db_engine`` binds to
    ``SQL_DATABASE_URL`` the first time it is imported, and under ``--db=postgres``
    that variable is only correct once the session fixtures have run. The engine
    is disposed afterwards because every test drives its own event loop with
    ``asyncio.run`` and asyncpg connections cannot be reused across loops.
    """
    import checkcheckserver.model._tables  # noqa: F401  (registers every table)
    from checkcheckserver.db._engine import db_engine
    from checkcheckserver.db._session import get_async_session_context

    async def _main():
        try:
            if sessions == 1:
                async with get_async_session_context() as session:
                    return await body(session)
            async with get_async_session_context() as first:
                async with get_async_session_context() as second:
                    return await body(first, second)
        finally:
            await db_engine.dispose()

    return asyncio.run(_main())


def _epoch(year: int) -> datetime.datetime:
    """A naive-UTC instant nobody else's rows live in. See the module docstring."""
    return datetime.datetime(year, 1, 1, 12, 0, 0)


def _payload(subject: str, to: str = "recipient@example.com") -> dict:
    return {
        "to": to,
        "subject": subject,
        "text_body": f"Body of {subject}.",
        "html_body": f"<p>Body of {subject}.</p>",
    }


def _subject(name: str) -> str:
    """A subject unique to one test run, so captured mail can be filtered."""
    return f"E2 {name} {uuid.uuid4().hex[:8]}"


def _captured(mail_capture, subject: str) -> list:
    return [email for email in mail_capture.sent if email.subject == subject]


async def _reload(session, row_id: uuid.UUID):
    """Re-read a row from the database, past the session's identity map.

    The outbox writes its state transitions with core UPDATEs (which is what
    keeps this table out of the sync sequence), so a loaded instance is stale
    until it is explicitly repopulated.
    """
    from sqlmodel import select

    from checkcheckserver.model.notification_outbox import NotificationOutbox

    result = await session.exec(
        select(NotificationOutbox)
        .where(NotificationOutbox.id == row_id)
        .execution_options(populate_existing=True)
    )
    return result.one_or_none()


@pytest.fixture(scope="module")
def admin_user_id() -> uuid.UUID:
    """The admin's user id. Outbox rows reference a real user (FK, CASCADE)."""
    return uuid.UUID(req("api/user/me")["id"])


# ── enqueue and deliver ───────────────────────────────────────────────────────


def test_enqueue_then_drain_sends_the_message_exactly_once(mail_capture, admin_user_id):
    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutboxStatus,
    )
    from checkcheckserver.notify.outbox import drain_once, enqueue

    subject = _subject("basic")
    now = _epoch(2001)

    async def body(session):
        row = await enqueue(
            session,
            user_id=admin_user_id,
            channel=NotificationChannel.email,
            payload=_payload(subject),
            not_before=now,
        )
        first = await drain_once(session, now=now)
        # A second pass must not send it again: the row is terminal now.
        second = await drain_once(session, now=now)
        return row.id, first, second, await _reload(session, row.id)

    row_id, first, second, row = _run(body)

    assert first.sent == 1 and row_id in first.ids_sent
    assert second.sent == 0
    assert len(_captured(mail_capture, subject)) == 1
    assert row.status == NotificationOutboxStatus.sent.value
    assert row.attempts == 1

    # The rendered message carries the instance's sender identity and a
    # Message-ID derived from the row, so a retry cannot look like a new mail.
    message = [m for m in mail_capture.messages if m["Subject"] == subject][0]
    assert message["To"] == "recipient@example.com"
    assert str(row_id) in message["Message-ID"]


def test_a_row_that_is_not_due_yet_is_left_alone(mail_capture, admin_user_id):
    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutboxStatus,
    )
    from checkcheckserver.notify.outbox import drain_once, enqueue

    subject = _subject("not-due")
    now = _epoch(2002)

    async def body(session):
        row = await enqueue(
            session,
            user_id=admin_user_id,
            channel=NotificationChannel.email,
            payload=_payload(subject),
            not_before=now + datetime.timedelta(minutes=5),
        )
        early = await drain_once(session, now=now)
        late = await drain_once(session, now=now + datetime.timedelta(minutes=6))
        return early, late, await _reload(session, row.id)

    early, late, row = _run(body)

    assert early.claimed == 0
    assert late.sent == 1
    assert len(_captured(mail_capture, subject)) == 1
    assert row.status == NotificationOutboxStatus.sent.value


def test_email_payload_without_an_address_is_refused_at_enqueue(admin_user_id):
    """A row nothing could ever send must not reach the queue in the first place."""
    from checkcheckserver.model.notification_outbox import NotificationChannel
    from checkcheckserver.notify.outbox import OutboxPayloadError, enqueue

    async def body(session):
        with pytest.raises(OutboxPayloadError):
            await enqueue(
                session,
                user_id=admin_user_id,
                channel=NotificationChannel.email,
                payload={"subject": "no recipient", "text_body": "..."},
            )

    _run(body)


# ── failure handling ──────────────────────────────────────────────────────────


def test_transient_failure_retries_with_growing_backoff(mail_capture, admin_user_id):
    """A mail server that is down must not cost the message, and must not be
    hammered either: every attempt waits longer than the one before."""
    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutboxStatus,
    )
    from checkcheckserver.notify.transports import TransientEmailError
    from checkcheckserver.notify.outbox import drain_once, enqueue

    subject = _subject("backoff")
    now = _epoch(2003)
    mail_capture.raise_on_send = TransientEmailError("connection refused")

    async def body(session):
        row = await enqueue(
            session,
            user_id=admin_user_id,
            channel=NotificationChannel.email,
            payload=_payload(subject),
            not_before=now,
        )
        waits = []
        at = now
        for _ in range(3):
            await drain_once(session, now=at, config=_config(NOTIFY_MAX_ATTEMPTS=6))
            current = await _reload(session, row.id)
            waits.append((current.not_before - at).total_seconds())
            at = current.not_before
        return row.id, waits, await _reload(session, row.id)

    row_id, waits, row = _run(body)

    assert waits == sorted(waits) and waits[0] < waits[-1], waits
    assert row.attempts == 3
    assert row.status == NotificationOutboxStatus.pending.value
    assert "connection refused" in row.last_error
    assert _captured(mail_capture, subject) == []

    # The message keeps its identity across attempts (checked once the transport
    # stops failing, since a failing send never renders).
    mail_capture.raise_on_send = None

    async def deliver(session):
        await drain_once(session, now=row.not_before)
        return await _reload(session, row_id)

    delivered = _run(deliver)
    assert delivered.status == NotificationOutboxStatus.sent.value
    assert len(_captured(mail_capture, subject)) == 1
    message = [m for m in mail_capture.messages if m["Subject"] == subject][0]
    assert str(row_id) in message["Message-ID"]


def test_giving_up_after_max_attempts(mail_capture, admin_user_id):
    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutboxStatus,
    )
    from checkcheckserver.notify.transports import TransientEmailError
    from checkcheckserver.notify.outbox import drain_once, enqueue

    subject = _subject("dead-letter")
    now = _epoch(2004)
    config = _config(NOTIFY_MAX_ATTEMPTS=3)
    mail_capture.raise_on_send = TransientEmailError("mail server unreachable")

    async def body(session):
        row = await enqueue(
            session,
            user_id=admin_user_id,
            channel=NotificationChannel.email,
            payload=_payload(subject),
            not_before=now,
        )
        at = now
        states = []
        for _ in range(4):
            result = await drain_once(session, now=at, config=config)
            current = await _reload(session, row.id)
            states.append((current.status, current.attempts, result.failed))
            at = current.not_before
        return states, await _reload(session, row.id)

    states, row = _run(body)

    pending = NotificationOutboxStatus.pending.value
    failed = NotificationOutboxStatus.failed.value
    assert [state for state, _, _ in states] == [pending, pending, failed, failed]
    # The fourth pass must find nothing left to do, not attempt a fourth send.
    assert states[-1][1] == 3
    assert row.attempts == config.NOTIFY_MAX_ATTEMPTS
    assert "Gave up after 3 attempts" in row.last_error
    assert "mail server unreachable" in row.last_error
    assert _captured(mail_capture, subject) == []


def test_a_failing_row_warns_once_then_goes_quiet(mail_capture, admin_user_id, caplog):
    """An operator must learn about a broken transport on the *first* failure.

    Before this, every retry logged at INFO and nothing at WARNING until the
    attempt budget was spent, so "mail stopped arriving an hour ago" either drowned
    in repeats or arrived far too late. The rule is one warning at the start, one
    at the end, and nothing louder than DEBUG in between, whichever channel it is.
    """
    import logging

    from checkcheckserver.model.notification_outbox import NotificationChannel
    from checkcheckserver.notify.transports import TransientEmailError
    from checkcheckserver.notify.outbox import drain_once, enqueue

    subject = _subject("log-once")
    now = _epoch(2010)
    config = _config(NOTIFY_MAX_ATTEMPTS=3)
    mail_capture.raise_on_send = TransientEmailError("mail server unreachable")

    async def body(session):
        row = await enqueue(
            session,
            user_id=admin_user_id,
            channel=NotificationChannel.email,
            payload=_payload(subject),
            not_before=now,
        )
        at = now
        for _ in range(3):
            await drain_once(session, now=at, config=config)
            current = await _reload(session, row.id)
            at = current.not_before
        return row.id

    # DEBUG, so the quiet attempts are captured too and can be asserted *absent*
    # from the warnings rather than absent from the log altogether.
    with caplog.at_level(logging.DEBUG, logger="CheckCheck"):
        row_id = _run(body)

    # Only this row's failure lines: a drain delivers whatever else is due (see
    # the module docstring), and enqueue logs a line of its own.
    mine = [
        rec
        for rec in caplog.records
        if str(row_id) in rec.getMessage() and "failed" in rec.getMessage()
    ]
    levels = [rec.levelno for rec in mine]
    assert levels == [logging.WARNING, logging.DEBUG, logging.WARNING], [
        (rec.levelname, rec.getMessage()) for rec in mine
    ]
    assert "attempt 1 failed" in mine[0].getMessage()
    assert "failed after 3 attempts" in mine[-1].getMessage()
    # The convention the whole module keeps: the row id identifies the failure,
    # the recipient address never appears.
    assert not any("recipient@example.com" in rec.getMessage() for rec in mine)


def test_permanent_failure_is_not_retried(mail_capture, admin_user_id):
    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutboxStatus,
    )
    from checkcheckserver.notify.transports import PermanentEmailError
    from checkcheckserver.notify.outbox import drain_once, enqueue

    subject = _subject("permanent")
    now = _epoch(2005)
    mail_capture.raise_on_send = PermanentEmailError("550 no such mailbox")

    async def body(session):
        row = await enqueue(
            session,
            user_id=admin_user_id,
            channel=NotificationChannel.email,
            payload=_payload(subject),
            not_before=now,
        )
        first = await drain_once(session, now=now)
        # However far in the future, a permanent failure never comes back.
        later = await drain_once(session, now=now + datetime.timedelta(days=7))
        return first, later, await _reload(session, row.id)

    first, later, row = _run(body)

    assert first.failed == 1
    assert later.claimed == 0
    assert row.status == NotificationOutboxStatus.failed.value
    assert row.attempts == 1
    assert "550 no such mailbox" in row.last_error


def test_concurrent_drains_do_not_double_send(mail_capture, admin_user_id):
    """Two drains racing over the same due row: exactly one may send it.

    On Postgres the candidates are selected FOR UPDATE SKIP LOCKED, but the
    guarantee itself is the conditional claim, which is why this test has to pass
    on SQLite too.
    """
    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutboxStatus,
    )
    from checkcheckserver.notify.outbox import drain_once, enqueue

    subject = _subject("concurrent")
    now = _epoch(2006)

    async def body(first_session, second_session):
        row = await enqueue(
            first_session,
            user_id=admin_user_id,
            channel=NotificationChannel.email,
            payload=_payload(subject),
            not_before=now,
        )
        results = await asyncio.gather(
            drain_once(first_session, now=now),
            drain_once(second_session, now=now),
        )
        return results, await _reload(first_session, row.id)

    results, row = _run(body, sessions=2)

    assert sum(result.sent for result in results) == 1
    assert sum(result.claimed for result in results) == 1
    assert len(_captured(mail_capture, subject)) == 1
    assert row.status == NotificationOutboxStatus.sent.value
    assert row.attempts == 1


# ── suppression ───────────────────────────────────────────────────────────────


def test_mail_is_cancelled_when_the_notification_was_already_read(
    mail_capture, admin_user_id
):
    """Suppression rule 1 (section 4.1): someone who saw it in the app inside the
    delay window gets no mail about it."""
    from checkcheckserver.model.notification import (
        Notification,
        NotificationType,
    )
    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutboxStatus,
    )
    from checkcheckserver.notify.outbox import drain_once, enqueue

    read_subject = _subject("read")
    unread_subject = _subject("unread")
    now = _epoch(2007)

    async def body(session):
        read = Notification(
            user_id=admin_user_id,
            type=NotificationType.card_shared,
            cl_id=uuid.uuid4(),
            read_at=now,
        )
        unread = Notification(
            user_id=admin_user_id,
            type=NotificationType.card_shared,
            cl_id=uuid.uuid4(),
        )
        session.add(read)
        session.add(unread)
        await session.commit()

        suppressed = await enqueue(
            session,
            user_id=admin_user_id,
            channel=NotificationChannel.email,
            payload=_payload(read_subject),
            notification_id=read.id,
            not_before=now,
        )
        delivered = await enqueue(
            session,
            user_id=admin_user_id,
            channel=NotificationChannel.email,
            payload=_payload(unread_subject),
            notification_id=unread.id,
            not_before=now,
        )
        result = await drain_once(session, now=now)
        return (
            result,
            await _reload(session, suppressed.id),
            await _reload(session, delivered.id),
        )

    result, suppressed, delivered = _run(body)

    assert result.cancelled == 1 and result.sent == 1
    assert suppressed.status == NotificationOutboxStatus.cancelled.value
    assert delivered.status == NotificationOutboxStatus.sent.value
    assert _captured(mail_capture, read_subject) == []
    assert len(_captured(mail_capture, unread_subject)) == 1


# ── retention ─────────────────────────────────────────────────────────────────


def test_pruning_removes_finished_rows_and_keeps_failures(admin_user_id):
    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutbox,
        NotificationOutboxStatus,
    )
    from checkcheckserver.notify.outbox import enqueue, prune_once

    now = _epoch(2008)
    config = _config(NOTIFY_OUTBOX_RETENTION_DAYS=30)
    old = now - datetime.timedelta(days=60)

    async def body(session):
        from sqlmodel import update

        ids = {}
        for name, status, updated_at in [
            ("old_sent", NotificationOutboxStatus.sent, old),
            ("old_cancelled", NotificationOutboxStatus.cancelled, old),
            ("old_failed", NotificationOutboxStatus.failed, old),
            ("old_pending", NotificationOutboxStatus.pending, old),
            ("fresh_sent", NotificationOutboxStatus.sent, now),
        ]:
            row = await enqueue(
                session,
                user_id=admin_user_id,
                channel=NotificationChannel.email,
                payload=_payload(_subject(name)),
                not_before=now + datetime.timedelta(days=3650),
            )
            await session.exec(
                update(NotificationOutbox)
                .where(NotificationOutbox.id == row.id)
                .values(status=status.value, updated_at=updated_at)
            )
            await session.commit()
            ids[name] = row.id

        removed = await prune_once(session, config=config, now=now)
        survivors = {
            name: await _reload(session, row_id) for name, row_id in ids.items()
        }
        return removed, survivors

    removed, survivors = _run(body)

    assert removed >= 2
    assert survivors["old_sent"] is None
    assert survivors["old_cancelled"] is None
    # The only record an operator has of mail that never arrived.
    assert survivors["old_failed"] is not None
    assert survivors["old_pending"] is not None
    assert survivors["fresh_sent"] is not None


def test_retention_of_zero_days_keeps_everything(admin_user_id):
    from checkcheckserver.notify.outbox import prune_once

    async def body(session):
        return await prune_once(
            session, config=_config(NOTIFY_OUTBOX_RETENTION_DAYS=0), now=_epoch(2009)
        )

    assert _run(body) == 0


# ── the dispatcher loop ───────────────────────────────────────────────────────


def test_dispatcher_loop_drains_wakes_on_a_nudge_and_cancels_cleanly():
    """The loop is a lifespan task: it must drain on start, react to a nudge
    without waiting out the tick, and stop when it is cancelled."""
    from checkcheckserver.notify import dispatcher

    async def body():
        drained = asyncio.Event()
        calls = []

        async def once():
            calls.append(object())
            drained.set()

        async def prune():
            calls.append("prune")

        # A tick far longer than the test: anything that happens must be the
        # nudge, never the timeout.
        task = asyncio.create_task(
            dispatcher.dispatcher_loop(
                _config(NOTIFY_DISPATCH_TICK_SECONDS=600), once=once, prune=prune
            )
        )
        await asyncio.wait_for(drained.wait(), timeout=5)
        first = len(calls)

        drained.clear()
        dispatcher.nudge()
        await asyncio.wait_for(drained.wait(), timeout=5)
        second = len(calls)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return first, second, calls, task.cancelled()

    first, second, calls, cancelled = asyncio.run(body())

    assert first >= 1
    assert second > first
    # Retention runs alongside the first drain, not on every tick.
    assert calls.count("prune") == 1
    assert cancelled


def test_lifespan_starts_the_dispatcher_and_stops_it_again(monkeypatch):
    """What actually runs the loop in a real boot: the router lifespan FastAPI
    merges into the app's."""
    from checkcheckserver.api.routes.routes_notification_settings import (
        fast_api_notification_settings_router as router,
    )
    from checkcheckserver.notify import dispatcher

    assert router.lifespan_context is dispatcher.lifespan

    started = asyncio.Event()

    async def fake_dispatch_once(cfg=None):
        started.set()

    async def fake_prune(cfg):
        pass

    monkeypatch.setattr(dispatcher, "config", _config(NOTIFY_DISPATCH_IN_PROCESS=True))
    monkeypatch.setattr(dispatcher, "dispatch_once", fake_dispatch_once)
    monkeypatch.setattr(dispatcher, "_prune", fake_prune)

    async def body():
        async with dispatcher.lifespan(app=None):
            await asyncio.wait_for(started.wait(), timeout=5)
            task = dispatcher._dispatcher_task
            assert task is not None and not task.done()
        return task

    task = asyncio.run(body())

    assert task.cancelled()
    assert dispatcher._dispatcher_task is None


def test_dispatcher_is_off_when_it_would_have_nothing_to_do():
    """With mail and webhooks both disabled nothing can ever reach the outbox, so
    the drain would only poll an empty table forever. Since chunk E6 the loop is
    also where feed retention runs, which is a reason to keep it alive even
    then — and since chunk R2 the due-reminder scan is another. So "nothing to
    do" now means no channel, no retention *and* no reminders."""
    from checkcheckserver.notify.dispatcher import channels_enabled, dispatch_enabled

    # (the test environment itself runs with NOTIFY_DISPATCH_IN_PROCESS off, so
    # every case that expects the loop to run has to ask for it explicitly)
    def cfg(**overrides):
        return _config(NOTIFY_DISPATCH_IN_PROCESS=True, **overrides)

    assert dispatch_enabled(cfg(EMAIL_ENABLED=True)) is True
    assert (
        dispatch_enabled(
            cfg(
                EMAIL_ENABLED=False,
                EMAIL_FROM_ADDRESS=None,
                NOTIFY_WEBHOOK_ENABLED=True,
            )
        )
        is True
    )
    # No channel, but there is still a feed to prune.
    no_channel = cfg(EMAIL_ENABLED=False, EMAIL_FROM_ADDRESS=None)
    assert channels_enabled(no_channel) is False
    assert dispatch_enabled(no_channel) is True
    # No channel and no retention, but reminders still need watching for.
    assert (
        dispatch_enabled(
            cfg(
                EMAIL_ENABLED=False,
                EMAIL_FROM_ADDRESS=None,
                NOTIFY_FEED_RETENTION_DAYS=0,
            )
        )
        is True
    )
    # Nothing to send, nothing to tidy and no reminders: the loop stays down.
    assert (
        dispatch_enabled(
            cfg(
                EMAIL_ENABLED=False,
                EMAIL_FROM_ADDRESS=None,
                NOTIFY_FEED_RETENTION_DAYS=0,
                NOTIFY_DISABLED_TYPES=["reminder_due"],
            )
        )
        is False
    )
    # The escape hatch for "something else drains the queue" (decision 2 of the
    # plan), and what the test server runs with.
    assert dispatch_enabled(_config(NOTIFY_DISPATCH_IN_PROCESS=False)) is False


def test_dispatch_once_delivers_against_the_real_database(mail_capture, admin_user_id):
    """The wiring between the loop and the outbox: no injected fakes here."""
    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutboxStatus,
    )
    from checkcheckserver.notify import dispatcher
    from checkcheckserver.notify.outbox import enqueue

    subject = _subject("dispatch-once")

    async def body(session):
        row = await enqueue(
            session,
            user_id=admin_user_id,
            channel=NotificationChannel.email,
            payload=_payload(subject),
        )
        await dispatcher.dispatch_once()
        return await _reload(session, row.id)

    row = _run(body)

    assert row.status == NotificationOutboxStatus.sent.value
    assert len(_captured(mail_capture, subject)) == 1


# ── the test-email endpoint ───────────────────────────────────────────────────


def test_test_email_endpoint_queues_a_message_that_really_gets_sent(mail_capture):
    """The first real producer, end to end: the HTTP endpoint queues, the drain
    in this process delivers, and the message that comes out is the right one."""
    from checkcheckserver.model.notification_outbox import NotificationOutboxStatus
    from checkcheckserver.notify.outbox import drain_once

    queued = req(
        "api/user/me/notification-settings/test-email",
        "post",
        expected_http_code=202,
    )
    assert queued["to"] == ADMIN_USER_EMAIL

    # Hard rate limit: one per minute, so the second call is refused.
    req(
        "api/user/me/notification-settings/test-email",
        "post",
        expected_http_code=429,
    )

    async def body(session):
        await drain_once(session)
        return await _reload(session, uuid.UUID(queued["queued_id"]))

    row = _run(body)

    assert row.status == NotificationOutboxStatus.sent.value
    # Filtered by subject as well as recipient: since chunk E4 every share in the
    # suite queues real mail too, and this drain (unlike the ones above, which
    # work in a time window far in the past) runs at the real now, so it delivers
    # whatever else has come due.
    sent = [
        email
        for email in mail_capture.sent
        if email.to == ADMIN_USER_EMAIL and "test message" in email.subject
    ]
    assert len(sent) == 1
    assert sent[0].html_body


def test_test_email_is_refused_when_there_is_nowhere_to_send_it():
    """Both 409 paths of the endpoint, called directly: the HTTP test above cannot
    reach them, since the test instance has mail on and the admin has an address."""
    from fastapi import HTTPException

    from checkcheckserver.api.routes import routes_notification_settings as endpoint
    from checkcheckserver.db.user import User

    with_address = User(user_name="mailless-test", email="somebody@example.com")
    without_address = User(user_name="mailless-test", email=None)

    async def call(user, config) -> int:
        original = endpoint.config
        endpoint.config = config
        try:
            await endpoint.send_test_email(current_user=user, session=None)
        except HTTPException as exc:
            return exc.status_code
        finally:
            endpoint.config = original
        return 200

    disabled = _config(EMAIL_ENABLED=False, EMAIL_FROM_ADDRESS=None)
    assert asyncio.run(call(with_address, disabled)) == 409
    assert asyncio.run(call(without_address, _config())) == 409


# ── shared config helper ──────────────────────────────────────────────────────


def _config(**overrides):
    """A Config that looks like a configured instance, plus overrides.

    Imported lazily like everything else in this module so nothing binds to the
    environment before the session fixtures have set it up.
    """
    from checkcheckserver.config import Config

    settings = {
        "EMAIL_ENABLED": True,
        "EMAIL_TRANSPORT": "null",
        "EMAIL_FROM_ADDRESS": MAIL_CAPTURE_FROM_ADDRESS,
    }
    settings.update(overrides)
    return Config(**settings)
