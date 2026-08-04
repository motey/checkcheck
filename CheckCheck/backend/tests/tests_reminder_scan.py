"""Tests for chunk R2 of the date-reminder sub-project: the scan and the fan-out.

R1 shipped a table and a calendar that nothing read. This is the chunk where a
row whose time has come turns into a notification, so these tests are about the
four decisions the scan takes per row: is it mine to claim, may this user still
see the card, what does the message say, and when does it fire next.

**How they run.** ``scan_due_once`` is called in this process, against the same
database the test server uses, with a substituted ``Config`` so one test can look
like an instance where an administrator disabled reminders. Nothing is drained:
what is asserted about mail is that the right message was *queued*, which is the
part this chunk is responsible for (delivery is E4's and has its own tests).

**Isolation.** Every scan runs at its own instant, always before the year 2000,
because the sibling reminder tests work in windows from 2003 onwards and a scan
picks up every pending row that is due at the instant it is given. Each test also
removes the rows it created, so a reminder rolled forward by one test cannot fire
again inside the next one, and every assertion is filtered by the card it was
made on.

The plan is ``docs/plans/DATE_REMINDERS.md`` (sections 5, 6, 8 R2).
"""

import asyncio
import datetime
import uuid

import pytest

from utils import authorize_for_access_token, create_test_user, req
from statics import MAIL_CAPTURE_FROM_ADDRESS


SCAN_USER_NAME = "reminderscanuser"
SCAN_USER_PW = "reminderscanuserpw_secure1"
SCAN_USER_EMAIL = "reminderscanuser@test.de"

COLLAB_USER_NAME = "reminderscancollab"
COLLAB_USER_PW = "reminderscancollabpw_secure1"
COLLAB_USER_EMAIL = "reminderscancollab@test.de"


# ── helpers ───────────────────────────────────────────────────────────────────


def _run(body):
    """Run *body* with a fresh session against the test database.

    Same shape (and same reasons) as its twins in ``tests_reminders.py`` and
    ``tests_notification_wiring.py``: lazy imports so nothing binds to the
    environment before the session fixtures have run, and the engine is disposed
    because every test drives its own event loop with ``asyncio.run``.
    """
    import checkcheckserver.model._tables  # noqa: F401  (registers every table)
    from checkcheckserver.db._engine import db_engine
    from checkcheckserver.db._session import get_async_session_context

    async def _main():
        try:
            async with get_async_session_context() as session:
                return await body(session)
        finally:
            await db_engine.dispose()

    return asyncio.run(_main())


def _config(**overrides):
    """A Config that looks like a configured instance, plus overrides."""
    from checkcheckserver.config import Config

    settings = {
        "EMAIL_ENABLED": True,
        "EMAIL_TRANSPORT": "null",
        "EMAIL_FROM_ADDRESS": MAIL_CAPTURE_FROM_ADDRESS,
    }
    settings.update(overrides)
    return Config(**settings)


def _utc(*args) -> datetime.datetime:
    return datetime.datetime(*args)


def _uid(user: dict) -> uuid.UUID:
    return uuid.UUID(str(user["id"]))


@pytest.fixture(scope="module")
def scan_user() -> dict:
    user = create_test_user(SCAN_USER_NAME, SCAN_USER_PW, SCAN_USER_EMAIL)
    user["token"] = authorize_for_access_token(SCAN_USER_NAME, SCAN_USER_PW)
    return user


@pytest.fixture(scope="module")
def collab_user() -> dict:
    user = create_test_user(COLLAB_USER_NAME, COLLAB_USER_PW, COLLAB_USER_EMAIL)
    user["token"] = authorize_for_access_token(COLLAB_USER_NAME, COLLAB_USER_PW)
    return user


def _card(owner: dict, name: str) -> dict:
    """A card of this test's own, so notifications can be filtered by it."""
    return req("api/checklist", "post", b={"name": name}, access_token=owner["token"])


async def _create(session, *, user, card, remind_at, **kwargs):
    from checkcheckserver.db.scheduled_notification import create_reminder

    return await create_reminder(
        session,
        user_id=_uid(user),
        cl_id=uuid.UUID(str(card["id"])),
        remind_at=remind_at,
        **kwargs,
    )


async def _scan(session, *, now, config=None):
    from checkcheckserver.notify.reminders import scan_due_once

    return await scan_due_once(session, config=config or _config(), now=now)


async def _feed_for(session, *, user, card):
    """This user's in-app notifications about this card, oldest first."""
    from sqlmodel import col, select

    from checkcheckserver.model.notification import Notification

    result = await session.exec(
        select(Notification)
        .where(Notification.user_id == _uid(user))
        .where(Notification.cl_id == uuid.UUID(str(card["id"])))
        .order_by(col(Notification.created_at))
    )
    return list(result.all())


async def _queued_mail_subjects(session, *, user):
    """The subjects of every email row queued for this user, oldest first."""
    from sqlmodel import col, select

    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutbox,
    )

    result = await session.exec(
        select(NotificationOutbox)
        .where(NotificationOutbox.user_id == _uid(user))
        .where(NotificationOutbox.channel == NotificationChannel.email.value)
        .order_by(col(NotificationOutbox.created_at))
        .execution_options(populate_existing=True)
    )
    return [(row.payload or {}).get("subject") for row in result.all()]


async def _reload(session, row_id: uuid.UUID):
    from sqlmodel import select

    from checkcheckserver.model.scheduled_notification import ScheduledNotification

    result = await session.exec(
        select(ScheduledNotification)
        .where(ScheduledNotification.id == row_id)
        .execution_options(populate_existing=True)
    )
    return result.one_or_none()


async def _forget(session, *rows):
    """Remove this test's rows so a later scan cannot pick them up again."""
    from checkcheckserver.db.scheduled_notification import delete_reminder

    for row in rows:
        await delete_reminder(session, row.id, row.user_id)


def _context(**overrides) -> dict:
    """A render context of the shape ``notification_context`` produces."""
    from checkcheckserver.notify.render import notification_context

    payload = {
        "checklist_name": overrides.pop("checklist_name", "Groceries"),
        "note": overrides.pop("note", None),
    }
    return notification_context(
        type=overrides.pop("type", "reminder_due"),
        notification_id=overrides.pop("notification_id", uuid.uuid4()),
        cl_id=overrides.pop("cl_id", uuid.uuid4()),
        payload=payload,
        created_at=datetime.datetime(2026, 8, 2, 9, 0),
        **overrides,
    )


# ── what a reminder says ──────────────────────────────────────────────────────


def test_a_reminder_subject_leads_with_the_users_own_note():
    """The note is the only part of the message the user chose. It goes first."""
    from checkcheckserver.notify.render import render_email

    message = render_email(
        [_context(note="Call the plumber")],
        to="somebody@example.com",
        recipient_name="Somebody",
        unsubscribe_url=None,
        config=_config(),
    )

    assert message["subject"] == "Reminder: Call the plumber"
    assert "Call the plumber" in message["text_body"]
    assert "Groceries" in message["text_body"]


def test_a_reminder_without_a_note_names_the_card():
    from checkcheckserver.notify.render import render_email

    message = render_email(
        [_context(checklist_name="Groceries")],
        to="somebody@example.com",
        recipient_name=None,
        unsubscribe_url=None,
        config=_config(),
    )

    assert message["subject"] == 'Reminder: "Groceries"'
    assert "You asked to be reminded about" in message["text_body"]


def test_minimal_content_mode_tells_neither_the_card_nor_the_note():
    """``minimal`` is the operator's answer to how much may leave the instance.

    A note ("call the clinic about the results") is exactly the kind of text
    that made them choose it, so it is held to the same rule as a card title.
    """
    from checkcheckserver.notify.render import render_email

    message = render_email(
        [_context(checklist_name="Divorce paperwork", note="Call the lawyer")],
        to="somebody@example.com",
        recipient_name="Somebody",
        unsubscribe_url=None,
        config=_config(NOTIFY_EMAIL_CONTENT_MODE="minimal"),
    )

    assert message["subject"] == "Reminder"
    for part in (message["subject"], message["text_body"], message["html_body"]):
        assert "Divorce paperwork" not in part
        assert "Call the lawyer" not in part


def test_several_reminders_at_once_become_one_plural_subject():
    """Reminders coming due together are one mail, and no note stands for all."""
    from checkcheckserver.notify.render import render_email

    message = render_email(
        [_context(note="Call the plumber"), _context(note="Water the plants")],
        to="somebody@example.com",
        recipient_name=None,
        unsubscribe_url=None,
        config=_config(),
    )

    assert message["subject"] == "2 reminders"
    assert "Call the plumber" in message["text_body"]
    assert "Water the plants" in message["text_body"]


def test_the_webhook_body_carries_the_note_and_is_null_for_other_types():
    from checkcheckserver.notify.render import webhook_body

    reminder = webhook_body(_context(note="Call the plumber"), _config())
    share = webhook_body(_context(type="card_shared"), _config())
    minimal = webhook_body(
        _context(note="Call the plumber"),
        _config(NOTIFY_EMAIL_CONTENT_MODE="minimal"),
    )

    assert reminder["type"] == "reminder_due"
    assert reminder["note"] == "Call the plumber"
    assert reminder["checklist_name"] == "Groceries"
    assert reminder["actor"] is None  # nobody reminded you but you
    # Always present, never absent: a receiver indexes it without guarding.
    assert share["note"] is None
    assert minimal["note"] is None
    assert minimal["checklist_name"] is None


# ── the scan ──────────────────────────────────────────────────────────────────


def test_a_due_one_off_fires_once_and_only_once(scan_user):
    """The whole feature in one test: a row comes due, a notification appears,
    and the next scan finds nothing left to do."""
    from checkcheckserver.model.scheduled_notification import ReminderStatus

    card = _card(scan_user, "Reminder scan: one-off")

    async def body(session):
        row = await _create(
            session,
            user=scan_user,
            card=card,
            remind_at=_utc(1990, 1, 1, 9, 0),
            note="Call the plumber",
        )
        first = await _scan(session, now=_utc(1990, 1, 1, 9, 0))
        after_first = await _feed_for(session, user=scan_user, card=card)
        second = await _scan(session, now=_utc(1990, 1, 1, 9, 30))
        after_second = await _feed_for(session, user=scan_user, card=card)
        stored = await _reload(session, row.id)
        subjects = await _queued_mail_subjects(session, user=scan_user)
        await _forget(session, row)
        return (
            first,
            second,
            after_first,
            after_second,
            stored.status,
            stored.fire_count,
            subjects,
        )

    first, second, after_first, after_second, status, fire_count, subjects = _run(body)

    assert first.fired == 1
    assert second.fired == 0
    assert len(after_first) == 1
    assert after_first[0].type == "reminder_due"
    assert after_first[0].payload["note"] == "Call the plumber"
    assert len(after_second) == 1
    assert status == ReminderStatus.done
    assert fire_count == 1
    # The fan-out is the point of the chunk: the same event also became mail,
    # because `reminder_due` ships with `email: immediate` (plan decision 7).
    assert "Reminder: Call the plumber" in subjects


def test_a_reminder_that_is_not_due_yet_is_left_alone(scan_user):
    card = _card(scan_user, "Reminder scan: not yet")

    async def body(session):
        row = await _create(
            session, user=scan_user, card=card, remind_at=_utc(1991, 1, 1, 9, 0)
        )
        result = await _scan(session, now=_utc(1991, 1, 1, 8, 59))
        feed = await _feed_for(session, user=scan_user, card=card)
        await _forget(session, row)
        return result, feed

    result, feed = _run(body)

    assert result.due == 0
    assert feed == []


def test_a_recurring_reminder_rolls_forward_and_stays_pending(scan_user):
    from checkcheckserver.model.scheduled_notification import (
        ReminderRecurrence,
        ReminderStatus,
    )

    card = _card(scan_user, "Reminder scan: daily")

    async def body(session):
        row = await _create(
            session,
            user=scan_user,
            card=card,
            remind_at=_utc(1992, 1, 1, 9, 0),
            recurrence=ReminderRecurrence.daily,
        )
        await _scan(session, now=_utc(1992, 1, 1, 9, 0))
        # A second tick at the very same instant. The roll forward happened as
        # part of the claim, so the row is no longer due and nothing repeats;
        # the race between two *concurrent* claims is
        # `tests_reminders.py::test_a_second_claim_of_the_same_occurrence_loses`.
        again = await _scan(session, now=_utc(1992, 1, 1, 9, 0))
        stored = await _reload(session, row.id)
        state = (stored.status, stored.remind_at, stored.fire_count)
        feed = await _feed_for(session, user=scan_user, card=card)
        await _forget(session, row)
        return state, feed, again

    (status, remind_at, fire_count), feed, again = _run(body)

    assert status == ReminderStatus.pending
    assert remind_at == _utc(1992, 1, 2, 9, 0)
    assert fire_count == 1
    assert len(feed) == 1
    assert again.due == 0


def test_an_outage_does_not_deliver_a_backlog(scan_user):
    """The server was down for three days. A daily reminder owes exactly one
    notification, not three, and its next occurrence is tomorrow."""
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence

    card = _card(scan_user, "Reminder scan: outage")

    async def body(session):
        row = await _create(
            session,
            user=scan_user,
            card=card,
            remind_at=_utc(1993, 1, 1, 9, 0),
            recurrence=ReminderRecurrence.daily,
        )
        # Back up three days later, with three missed occurrences behind it.
        await _scan(session, now=_utc(1993, 1, 4, 10, 0))
        stored = await _reload(session, row.id)
        state = (stored.remind_at, stored.fire_count)
        feed = await _feed_for(session, user=scan_user, card=card)
        await _forget(session, row)
        return state, feed

    (remind_at, fire_count), feed = _run(body)

    assert len(feed) == 1
    assert fire_count == 1
    assert remind_at == _utc(1993, 1, 5, 9, 0)


def test_a_tombstoned_card_cancels_the_reminder_silently(scan_user):
    """Plan decision 2: the row survives the deletion, the fire-time check does
    not, and the user hears nothing about a card they deleted on purpose."""
    from checkcheckserver.model.scheduled_notification import ReminderStatus

    card = _card(scan_user, "Reminder scan: deleted card")

    async def body(session):
        row = await _create(
            session, user=scan_user, card=card, remind_at=_utc(1994, 1, 1, 9, 0)
        )
        result = await _scan(session, now=_utc(1994, 1, 1, 9, 0))
        stored = await _reload(session, row.id)
        feed = await _feed_for(session, user=scan_user, card=card)
        await _forget(session, row)
        return result, stored.status, stored.fire_count, feed

    req(f"api/checklist/{card['id']}", "delete", access_token=scan_user["token"])
    result, status, fire_count, feed = _run(body)

    assert result.cancelled == 1
    assert result.fired == 0
    assert status == ReminderStatus.cancelled
    assert fire_count == 0
    assert feed == []


def test_a_recurring_reminder_is_cancelled_too_not_just_skipped(scan_user):
    """A card somebody can no longer open does not become openable next week."""
    from checkcheckserver.model.scheduled_notification import (
        ReminderRecurrence,
        ReminderStatus,
    )

    card = _card(scan_user, "Reminder scan: deleted card, weekly")

    async def body(session):
        row = await _create(
            session,
            user=scan_user,
            card=card,
            remind_at=_utc(1995, 1, 1, 9, 0),
            recurrence=ReminderRecurrence.weekly,
        )
        await _scan(session, now=_utc(1995, 1, 1, 9, 0))
        stored = await _reload(session, row.id)
        status = stored.status
        await _forget(session, row)
        return status

    req(f"api/checklist/{card['id']}", "delete", access_token=scan_user["token"])

    assert _run(body) == ReminderStatus.cancelled


def test_a_collaborator_is_reminded_about_a_card_shared_with_them(
    scan_user, collab_user
):
    """A reminder is personal (plan decision 1): anybody who can see the card can
    set their own, and it reaches them and nobody else."""
    card = _card(scan_user, "Reminder scan: shared card")
    req(
        f"api/checklist/{card['id']}/shares/{collab_user['id']}",
        "put",
        b={"permission": "view"},
        access_token=scan_user["token"],
    )

    async def body(session):
        row = await _create(
            session,
            user=collab_user,
            card=card,
            remind_at=_utc(1996, 1, 1, 9, 0),
            note="Their own reminder",
        )
        await _scan(session, now=_utc(1996, 1, 1, 9, 0))
        theirs = await _feed_for(session, user=collab_user, card=card)
        owners = await _feed_for(session, user=scan_user, card=card)
        await _forget(session, row)
        return [n.type for n in theirs], [n.type for n in owners]

    theirs, owners = _run(body)

    assert theirs.count("reminder_due") == 1
    # The owner set nothing, so the owner hears nothing.
    assert "reminder_due" not in owners


def test_a_revoked_share_cancels_the_collaborators_reminder(scan_user, collab_user):
    """The check is at fire time, so every future way of losing access is covered
    without the revocation paths knowing this feature exists."""
    from checkcheckserver.model.scheduled_notification import ReminderStatus

    card = _card(scan_user, "Reminder scan: revoked share")
    req(
        f"api/checklist/{card['id']}/shares/{collab_user['id']}",
        "put",
        b={"permission": "view"},
        access_token=scan_user["token"],
    )

    async def body(session):
        row = await _create(
            session, user=collab_user, card=card, remind_at=_utc(1997, 1, 1, 9, 0)
        )
        return row

    row = _run(body)

    req(
        f"api/checklist/{card['id']}/shares/{collab_user['id']}",
        "delete",
        expected_http_code=204,
        access_token=scan_user["token"],
    )

    async def after(session):
        result = await _scan(session, now=_utc(1997, 1, 1, 9, 0))
        stored = await _reload(session, row.id)
        feed = await _feed_for(session, user=collab_user, card=card)
        await _forget(session, stored)
        return result, stored.status, feed

    result, status, feed = _run(after)

    assert result.cancelled == 1
    assert status == ReminderStatus.cancelled
    assert [n.type for n in feed].count("reminder_due") == 0


def test_an_instance_with_reminders_disabled_fires_nothing(scan_user):
    """``NOTIFY_DISABLED_TYPES`` is the feature's off switch (plan section 6), and
    it stops the scan rather than leaving it to claim rows nothing can deliver."""
    from checkcheckserver.model.scheduled_notification import ReminderStatus

    card = _card(scan_user, "Reminder scan: disabled instance")

    async def body(session):
        row = await _create(
            session, user=scan_user, card=card, remind_at=_utc(1998, 1, 1, 9, 0)
        )
        result = await _scan(
            session,
            now=_utc(1998, 1, 1, 9, 0),
            config=_config(NOTIFY_DISABLED_TYPES=["reminder_due"]),
        )
        stored = await _reload(session, row.id)
        feed = await _feed_for(session, user=scan_user, card=card)
        await _forget(session, row)
        return result, stored.status, feed

    result, status, feed = _run(body)

    assert result.due == 0
    assert result.fired == 0
    # Still pending, so switching the type back on delivers it rather than having
    # quietly consumed it while nobody was listening.
    assert status == ReminderStatus.pending
    assert feed == []


def test_a_user_who_muted_the_bell_still_gets_the_mail(scan_user):
    """The fan-out is the existing one, so a reminder honours the matrix like
    every other type: in-app off means no feed row, and the mail is unaffected."""
    card = _card(scan_user, "Reminder scan: muted bell")

    async def body(session):
        from checkcheckserver.db.user_notification_settings import (
            get_or_create_settings,
        )
        from checkcheckserver.notify.prefs import apply_prefs_patch

        settings = await get_or_create_settings(session, _uid(scan_user))
        settings.prefs = apply_prefs_patch(
            settings.prefs, {"reminder_due": {"in_app": "off"}}
        )
        session.add(settings)
        await session.commit()

        row = await _create(
            session,
            user=scan_user,
            card=card,
            remind_at=_utc(1999, 1, 1, 9, 0),
            note="Muted bell reminder",
        )
        result = await _scan(session, now=_utc(1999, 1, 1, 9, 0))
        feed = await _feed_for(session, user=scan_user, card=card)
        subjects = await _queued_mail_subjects(session, user=scan_user)
        await _forget(session, row)

        # Put the preference back: the fixture user is shared with every test in
        # this module and the next one would otherwise inherit a muted bell.
        settings.prefs = apply_prefs_patch(
            settings.prefs, {"reminder_due": {"in_app": None}}
        )
        session.add(settings)
        await session.commit()
        return result, feed, subjects

    result, feed, subjects = _run(body)

    assert result.fired == 1
    assert feed == []
    assert "Reminder: Muted bell reminder" in subjects


# ── the dispatcher ────────────────────────────────────────────────────────────


def test_the_loop_runs_for_reminders_alone():
    """An in-app-only reminder works on an instance with neither mail nor
    webhooks, and nothing else in the process notices that one came due."""
    from checkcheckserver.notify.dispatcher import channels_enabled, dispatch_enabled

    def cfg(**overrides):
        return _config(NOTIFY_DISPATCH_IN_PROCESS=True, **overrides)

    silent = cfg(
        EMAIL_ENABLED=False,
        EMAIL_FROM_ADDRESS=None,
        NOTIFY_FEED_RETENTION_DAYS=0,
    )
    assert channels_enabled(silent) is False
    assert dispatch_enabled(silent) is True
    # Nothing to send, nothing to tidy and no reminders to watch for: down.
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


def test_dispatch_once_scans_even_when_no_channel_is_enabled(scan_user):
    """The dispatcher change that is easiest to get wrong: ``dispatch_once`` used
    to return without opening a session when nothing could be delivered."""
    from checkcheckserver.notify import dispatcher

    card = _card(scan_user, "Reminder scan: no channel")
    silent = _config(
        EMAIL_ENABLED=False,
        EMAIL_FROM_ADDRESS=None,
        NOTIFY_WEBHOOK_ENABLED=False,
    )

    async def body(session):
        row = await _create(
            session, user=scan_user, card=card, remind_at=_utc(1985, 1, 1, 9, 0)
        )
        before = await _queued_mail_subjects(session, user=scan_user)
        # The real entry point, with the real session handling behind it. It
        # takes no clock, so the reminder is set in the past and is due on the
        # first pass.
        await dispatcher.dispatch_once(silent)
        feed = await _feed_for(session, user=scan_user, card=card)
        after = await _queued_mail_subjects(session, user=scan_user)
        await _forget(session, row)
        return feed, len(before), len(after)

    feed, before, after = _run(body)

    assert [n.type for n in feed] == ["reminder_due"]
    # The bell is all this instance has, and the drain was skipped: no channel
    # is enabled, so nothing could have queued anything either.
    assert after == before


def test_a_failing_scan_does_not_stop_the_mail(scan_user, monkeypatch):
    """The two halves of a tick are independent.

    Mail is the older and more critical path, so one reminder the scan cannot
    handle must not keep everybody else's messages queued tick after tick.
    """
    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutboxStatus,
    )
    from checkcheckserver.notify import dispatcher
    from checkcheckserver.notify.outbox import enqueue

    async def exploding_scan(session, **kwargs):
        raise RuntimeError("a reminder the scan cannot handle")

    monkeypatch.setattr(dispatcher, "scan_due_once", exploding_scan)

    async def body(session):
        from sqlmodel import select

        from checkcheckserver.model.notification_outbox import NotificationOutbox

        row = await enqueue(
            session,
            user_id=_uid(scan_user),
            channel=NotificationChannel.email,
            payload={
                "to": SCAN_USER_EMAIL,
                "subject": "Mail that must go out anyway",
                "text_body": "Hello.",
            },
        )
        await dispatcher.dispatch_once(_config())
        result = await session.exec(
            select(NotificationOutbox)
            .where(NotificationOutbox.id == row.id)
            .execution_options(populate_existing=True)
        )
        return result.one().status

    assert _run(body) == NotificationOutboxStatus.sent.value


def test_the_hourly_pass_prunes_finished_reminders(scan_user):
    """Retention rides along with the outbox and feed prunes, per plan 3.1."""
    from checkcheckserver.db.scheduled_notification import cancel

    card = _card(scan_user, "Reminder scan: retention")

    async def body(session):
        row = await _create(
            session, user=scan_user, card=card, remind_at=_utc(1986, 1, 1, 9, 0)
        )
        await cancel(session, row, now=_utc(1986, 1, 1, 9, 0))
        return row

    row = _run(body)

    async def after(session):
        from checkcheckserver.notify import dispatcher

        await dispatcher._prune(_config())
        return await _reload(session, row.id)

    # `cancel` stamped updated_at in 1986, which is well past any retention.
    assert _run(after) is None
