"""Tests for chunk R1 of the date-reminder sub-project: schema, calendar, CRUD.

Two halves that barely touch:

* The **calendar** (``notify/recurrence.py``) is pure, so those tests are plain
  function calls with no database, no fixtures and no clock. They are the bulk of
  this file on purpose: every interesting reminder bug is a calendar bug, and a
  calendar bug is cheap to write a test for and expensive to find in production.
* The **CRUD** (``db/scheduled_notification.py``) runs in this process against
  the same database the live server uses, following the ``_run`` shape the
  notification tests already established. It owns its own user and its own card,
  created over HTTP, so nothing here depends on what another test left behind.

Nothing fires yet: the scan and the notification type are chunk R2. What is
asserted here is that a row can be created, found, claimed exactly once, rolled
forward and cleaned up.

The plan is ``docs/plans/DATE_REMINDERS.md`` (sections 3.1, 4, 8 R1).
"""

import asyncio
import datetime
import uuid

import pytest

from utils import authorize_for_access_token, create_test_user, req


REMINDER_USER_NAME = "reminderuser"
REMINDER_USER_PW = "reminderuserpw_secure1"
REMINDER_USER_EMAIL = "reminderuser@test.de"

# A second account, so "somebody else's reminder" is a real account and not a
# random UUID that no row could ever match anyway.
OTHER_USER_NAME = "reminderotheruser"
OTHER_USER_PW = "reminderotheruserpw_secure1"
OTHER_USER_EMAIL = "reminderotheruser@test.de"

BERLIN = "Europe/Berlin"


# ── helpers ───────────────────────────────────────────────────────────────────


def _run(body, *, sessions: int = 1):
    """Run *body* with fresh session(s) against the test database.

    Same shape (and same reasons) as its twins in ``tests_notification_outbox.py``
    and ``tests_notification_wiring.py``: lazy imports so nothing binds to the
    environment before the session fixtures have run, and the engine is disposed
    because every test drives its own event loop with ``asyncio.run``.

    ``sessions=2`` is what the claim-race test needs: one session's identity map
    hands back the *same* Python object for a row it has already loaded, so two
    competing readers can only be simulated with two sessions.
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


def _utc(*args) -> datetime.datetime:
    """A naive UTC datetime, the way every column in this feature stores one."""
    return datetime.datetime(*args)


def _local(zone_name: str, *args) -> datetime.datetime:
    """A wall-clock time in *zone_name*, converted to the naive UTC we store.

    The tests are written in the user's own terms ("09:00 in Berlin") because
    that is what a reminder promises, and this is the only place the conversion
    happens.
    """
    import zoneinfo

    aware = datetime.datetime(*args, tzinfo=zoneinfo.ZoneInfo(zone_name))
    return aware.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def _as_local(when: datetime.datetime, zone_name: str) -> datetime.datetime:
    """Naive UTC back to a naive wall clock in *zone_name*, for assertions."""
    import zoneinfo

    return (
        when.replace(tzinfo=datetime.timezone.utc)
        .astimezone(zoneinfo.ZoneInfo(zone_name))
        .replace(tzinfo=None)
    )


@pytest.fixture(scope="module")
def reminder_user() -> dict:
    user = create_test_user(REMINDER_USER_NAME, REMINDER_USER_PW, REMINDER_USER_EMAIL)
    user["token"] = authorize_for_access_token(REMINDER_USER_NAME, REMINDER_USER_PW)
    return user


@pytest.fixture(scope="module")
def other_user() -> dict:
    user = create_test_user(OTHER_USER_NAME, OTHER_USER_PW, OTHER_USER_EMAIL)
    user["token"] = authorize_for_access_token(OTHER_USER_NAME, OTHER_USER_PW)
    return user


@pytest.fixture(scope="module")
def reminder_card(reminder_user) -> dict:
    return req(
        "api/checklist",
        "post",
        b={"name": "Reminder test card"},
        access_token=reminder_user["token"],
    )


async def _create(session, *, user, card, remind_at, **kwargs):
    from checkcheckserver.db.scheduled_notification import create_reminder

    return await create_reminder(
        session,
        user_id=uuid.UUID(str(user["id"])),
        cl_id=uuid.UUID(str(card["id"])),
        remind_at=remind_at,
        **kwargs,
    )


# ── the calendar: one-off ─────────────────────────────────────────────────────


def test_a_one_off_reminder_has_no_next_occurrence():
    """The signal the scan turns into "mark this row done"."""
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import next_occurrence

    assert (
        next_occurrence(
            _utc(2026, 8, 2, 9, 0), recurrence=ReminderRecurrence.none
        )
        is None
    )


def test_a_one_off_reminder_gets_no_monthly_anchor():
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import anchor_day_for

    assert (
        anchor_day_for(
            _utc(2026, 8, 31, 9, 0), recurrence=ReminderRecurrence.none
        )
        is None
    )


# ── the calendar: the ordinary intervals ──────────────────────────────────────


def test_daily_and_weekly_land_on_the_same_wall_clock_time():
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import next_occurrence

    fired = _local(BERLIN, 2026, 8, 2, 9, 0)

    tomorrow = next_occurrence(
        fired, recurrence=ReminderRecurrence.daily, timezone_name=BERLIN
    )
    next_week = next_occurrence(
        fired, recurrence=ReminderRecurrence.weekly, timezone_name=BERLIN
    )

    assert _as_local(tomorrow, BERLIN) == datetime.datetime(2026, 8, 3, 9, 0)
    assert _as_local(next_week, BERLIN) == datetime.datetime(2026, 8, 9, 9, 0)


def test_no_time_zone_means_utc():
    """A user who never opened the settings dialog still gets a working reminder."""
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import next_occurrence

    assert next_occurrence(
        _utc(2026, 8, 2, 9, 0), recurrence=ReminderRecurrence.daily
    ) == _utc(2026, 8, 3, 9, 0)


def test_an_unusable_time_zone_falls_back_to_utc_instead_of_raising():
    """A renamed IANA zone, or an image without a zone database.

    A reminder an hour off beats a scan that dies on one bad row.
    """
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import next_occurrence

    assert next_occurrence(
        _utc(2026, 8, 2, 9, 0),
        recurrence=ReminderRecurrence.daily,
        timezone_name="Mars/Olympus_Mons",
    ) == _utc(2026, 8, 3, 9, 0)


# ── the calendar: DST ─────────────────────────────────────────────────────────


def test_spring_forward_keeps_the_wall_clock_time_not_the_utc_offset():
    """Europe/Berlin loses an hour on 29 March 2026.

    A daily 09:00 reminder must stay 09:00 local, which means the UTC instant
    moves by an hour. Adding 24 hours in UTC would have kept 07:00 UTC and told
    the user 10:00, every spring, forever.
    """
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import next_occurrence

    before = _local(BERLIN, 2026, 3, 28, 9, 0)  # CET, 08:00 UTC
    after = next_occurrence(
        before, recurrence=ReminderRecurrence.daily, timezone_name=BERLIN
    )

    assert _as_local(after, BERLIN) == datetime.datetime(2026, 3, 29, 9, 0)
    assert after == _utc(2026, 3, 29, 7, 0)  # CEST: same wall clock, an hour earlier


def test_autumn_fall_back_keeps_the_wall_clock_time_too():
    """The other direction: 25 October 2026 gains an hour."""
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import next_occurrence

    before = _local(BERLIN, 2026, 10, 24, 9, 0)  # CEST, 07:00 UTC
    after = next_occurrence(
        before, recurrence=ReminderRecurrence.daily, timezone_name=BERLIN
    )

    assert _as_local(after, BERLIN) == datetime.datetime(2026, 10, 25, 9, 0)
    assert after == _utc(2026, 10, 25, 8, 0)  # CET


def test_a_local_time_that_does_not_exist_shifts_forward():
    """02:30 does not happen on the morning the clocks go forward.

    ``fold=0`` resolves it with the pre-transition offset, which lands on 03:30
    local. Documented behaviour, not an accident: the alternative is raising on
    one day a year.
    """
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import next_occurrence

    before = _local(BERLIN, 2026, 3, 28, 2, 30)
    after = next_occurrence(
        before, recurrence=ReminderRecurrence.daily, timezone_name=BERLIN
    )

    assert _as_local(after, BERLIN) == datetime.datetime(2026, 3, 29, 3, 30)


# ── the calendar: months ──────────────────────────────────────────────────────


def test_a_monthly_reminder_on_the_31st_is_clamped_but_not_rewritten():
    """The case the ``anchor_day`` column exists for.

    31 January to 28 February is a clamp. The month after that must be 31 March
    again: the reminder means "the last useful day I picked", not "the 28th from
    now on".
    """
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import anchor_day_for, next_occurrence

    january = _utc(2026, 1, 31, 9, 0)
    anchor = anchor_day_for(january, recurrence=ReminderRecurrence.monthly)
    assert anchor == 31

    february = next_occurrence(
        january, recurrence=ReminderRecurrence.monthly, anchor_day=anchor
    )
    assert february == _utc(2026, 2, 28, 9, 0)

    march = next_occurrence(
        february, recurrence=ReminderRecurrence.monthly, anchor_day=anchor
    )
    assert march == _utc(2026, 3, 31, 9, 0)


def test_a_monthly_reminder_clamps_into_a_leap_february():
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import next_occurrence

    assert next_occurrence(
        _utc(2028, 1, 31, 9, 0),
        recurrence=ReminderRecurrence.monthly,
        anchor_day=31,
    ) == _utc(2028, 2, 29, 9, 0)


def test_a_monthly_reminder_rolls_over_the_year():
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import next_occurrence

    assert next_occurrence(
        _utc(2026, 12, 15, 9, 0),
        recurrence=ReminderRecurrence.monthly,
        anchor_day=15,
    ) == _utc(2027, 1, 15, 9, 0)


def test_the_monthly_anchor_is_the_local_day_not_the_utc_one():
    """A 00:30 Berlin reminder is the previous day in UTC. The user picked local."""
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import anchor_day_for

    # 00:30 on the 1st in Berlin is 23:30 on the 31st (or 30th) in UTC.
    remind_at = _local(BERLIN, 2026, 8, 1, 0, 30)
    assert remind_at.day == 31

    assert (
        anchor_day_for(
            remind_at, recurrence=ReminderRecurrence.monthly, timezone_name=BERLIN
        )
        == 1
    )


# ── the calendar: catching up after an outage ─────────────────────────────────


def test_a_reminder_missed_for_days_fires_once_and_skips_the_rest():
    """The server was down from Monday to Thursday.

    A daily reminder owes the user one notification, not four, so the roll
    forward walks past every missed occurrence in one step.
    """
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import next_occurrence

    was_due = _utc(2026, 8, 2, 9, 0)
    now = _utc(2026, 8, 5, 14, 0)

    assert next_occurrence(
        was_due, recurrence=ReminderRecurrence.daily, now=now
    ) == _utc(2026, 8, 6, 9, 0)


def test_catching_up_a_monthly_reminder_keeps_its_anchor():
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import next_occurrence

    assert next_occurrence(
        _utc(2025, 12, 31, 9, 0),
        recurrence=ReminderRecurrence.monthly,
        anchor_day=31,
        now=_utc(2026, 3, 10, 0, 0),
    ) == _utc(2026, 3, 31, 9, 0)


def test_without_a_now_the_next_occurrence_is_simply_the_next_one():
    """The ordinary case: the reminder fired on time, so nothing is skipped."""
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence
    from checkcheckserver.notify.recurrence import next_occurrence

    assert next_occurrence(
        _utc(2026, 8, 2, 9, 0), recurrence=ReminderRecurrence.daily
    ) == _utc(2026, 8, 3, 9, 0)


# ── the table ─────────────────────────────────────────────────────────────────


def test_a_created_reminder_is_pending_and_unfired(reminder_user, reminder_card):
    from checkcheckserver.model.scheduled_notification import ReminderStatus

    async def body(session):
        return await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2031, 1, 1, 9, 0),
            note="water the plants",
        )

    row = _run(body)
    assert row.status == ReminderStatus.pending
    assert row.fire_count == 0
    assert row.last_fired_at is None
    assert row.note == "water the plants"
    assert row.anchor_day is None  # not monthly


def test_creating_a_monthly_reminder_stores_its_anchor(reminder_user, reminder_card):
    from checkcheckserver.model.scheduled_notification import ReminderRecurrence

    async def body(session):
        return await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2031, 1, 31, 9, 0),
            recurrence=ReminderRecurrence.monthly,
            timezone="UTC",
        )

    assert _run(body).anchor_day == 31


def test_a_reminder_is_only_readable_by_the_user_who_set_it(
    reminder_user, other_user, reminder_card
):
    """The ownership scoping every route depends on (plan decision 1)."""
    from checkcheckserver.db.scheduled_notification import get_reminder

    async def body(session):
        row = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2031, 2, 1, 9, 0),
        )
        mine = await get_reminder(session, row.id, uuid.UUID(str(reminder_user["id"])))
        theirs = await get_reminder(session, row.id, uuid.UUID(str(other_user["id"])))
        return mine, theirs

    mine, theirs = _run(body)
    assert mine is not None
    assert theirs is None


def test_listing_a_card_shows_only_pending_reminders(reminder_user, reminder_card):
    from checkcheckserver.db.scheduled_notification import cancel, list_for_card

    async def body(session):
        alive = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2032, 5, 1, 9, 0),
        )
        dead = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2032, 5, 2, 9, 0),
        )
        await cancel(session, dead)
        listed = await list_for_card(
            session,
            user_id=uuid.UUID(str(reminder_user["id"])),
            cl_id=uuid.UUID(str(reminder_card["id"])),
        )
        with_finished = await list_for_card(
            session,
            user_id=uuid.UUID(str(reminder_user["id"])),
            cl_id=uuid.UUID(str(reminder_card["id"])),
            include_finished=True,
        )
        return alive.id, dead.id, [r.id for r in listed], [r.id for r in with_finished]

    alive_id, dead_id, listed, with_finished = _run(body)
    assert alive_id in listed
    assert dead_id not in listed
    assert dead_id in with_finished


def test_a_cancelled_reminder_stops_counting_against_the_cap(
    reminder_user, reminder_card
):
    from checkcheckserver.db.scheduled_notification import (
        cancel,
        count_pending_for_card,
    )

    async def body(session):
        user_id = uuid.UUID(str(reminder_user["id"]))
        cl_id = uuid.UUID(str(reminder_card["id"]))
        before = await count_pending_for_card(session, user_id=user_id, cl_id=cl_id)
        row = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2032, 6, 1, 9, 0),
        )
        during = await count_pending_for_card(session, user_id=user_id, cl_id=cl_id)
        await cancel(session, row)
        after = await count_pending_for_card(session, user_id=user_id, cl_id=cl_id)
        return before, during, after

    before, during, after = _run(body)
    assert during == before + 1
    assert after == before


def test_deleting_someone_elses_reminder_does_nothing(
    reminder_user, other_user, reminder_card
):
    from checkcheckserver.db.scheduled_notification import (
        delete_reminder,
        get_reminder,
    )

    async def body(session):
        row = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2032, 7, 1, 9, 0),
        )
        refused = await delete_reminder(
            session, row.id, uuid.UUID(str(other_user["id"]))
        )
        survived = await get_reminder(
            session, row.id, uuid.UUID(str(reminder_user["id"]))
        )
        removed = await delete_reminder(
            session, row.id, uuid.UUID(str(reminder_user["id"]))
        )
        gone = await get_reminder(session, row.id, uuid.UUID(str(reminder_user["id"])))
        return refused, survived, removed, gone

    refused, survived, removed, gone = _run(body)
    assert refused is False
    assert survived is not None
    assert removed is True
    assert gone is None


# ── claiming ──────────────────────────────────────────────────────────────────


def test_due_rows_finds_a_reminder_whose_time_has_come(reminder_user, reminder_card):
    """Each of these tests works in its own time window in the past, the trick
    ``tests_notification_outbox.py`` established: rows other tests left behind
    are due at their own instants, never at this one."""
    from checkcheckserver.db.scheduled_notification import due_rows

    async def body(session):
        row = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2003, 3, 3, 9, 0),
        )
        found = await due_rows(session, now=_utc(2003, 3, 3, 9, 0))
        not_yet = await due_rows(session, now=_utc(2003, 3, 3, 8, 59))
        return row.id, [r.id for r in found], [r.id for r in not_yet]

    row_id, found, not_yet = _run(body)
    assert row_id in found
    assert row_id not in not_yet


def test_claiming_a_one_off_marks_it_done(reminder_user, reminder_card):
    from checkcheckserver.db.scheduled_notification import claim_due
    from checkcheckserver.model.scheduled_notification import ReminderStatus

    async def body(session):
        row = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2004, 4, 4, 9, 0),
        )
        claimed = await claim_due(session, row, next_remind_at=None)
        return claimed, row.status

    claimed, status = _run(body)
    assert claimed is True
    assert status == ReminderStatus.done


def test_claiming_a_recurring_reminder_rolls_it_forward_and_keeps_it_pending(
    reminder_user, reminder_card
):
    """The claim *is* the roll forward, so a crash right after it costs one
    occurrence rather than the whole reminder."""
    from checkcheckserver.db.scheduled_notification import claim_due
    from checkcheckserver.model.scheduled_notification import (
        ReminderRecurrence,
        ReminderStatus,
    )
    from checkcheckserver.notify.recurrence import next_occurrence

    async def body(session):
        row = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2005, 5, 5, 9, 0),
            recurrence=ReminderRecurrence.daily,
        )
        next_at = next_occurrence(row.remind_at, recurrence=ReminderRecurrence.daily)
        claimed = await claim_due(session, row, next_remind_at=next_at)
        return claimed, row.status, row.remind_at

    claimed, status, remind_at = _run(body)
    assert claimed is True
    assert status == ReminderStatus.pending
    assert remind_at == _utc(2005, 5, 6, 9, 0)


def test_a_second_claim_of_the_same_occurrence_loses(reminder_user, reminder_card):
    """What actually stops a reminder going out twice: the compare-and-swap.

    Two sessions read the same due row (an overlapping tick, or a second
    replica); only the first update finds the row still pending at that instant.
    """
    from checkcheckserver.db.scheduled_notification import claim_due, due_rows

    async def body(first, second):
        row = await _create(
            first,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2006, 6, 6, 9, 0),
        )
        due_at = _utc(2006, 6, 6, 9, 0)
        mine = [r for r in await due_rows(first, now=due_at) if r.id == row.id][0]
        # A genuinely independent read of the same row, exactly as a competing
        # scan has it: still pending, still carrying the old remind_at.
        stale = [r for r in await due_rows(second, now=due_at) if r.id == row.id][0]

        won = await claim_due(first, mine, next_remind_at=None)
        lost = await claim_due(second, stale, next_remind_at=None)
        return won, lost

    won, lost = _run(body, sessions=2)
    assert won is True
    assert lost is False


def test_marking_fired_bumps_the_counters(reminder_user, reminder_card):
    from checkcheckserver.db.scheduled_notification import claim_due, mark_fired

    async def body(session):
        row = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2007, 7, 7, 9, 0),
        )
        await claim_due(session, row, next_remind_at=None)
        await mark_fired(session, row)
        return row.fire_count, row.last_fired_at

    fire_count, last_fired_at = _run(body)
    assert fire_count == 1
    assert last_fired_at is not None


def test_a_cancelled_claim_does_not_count_as_a_fire(reminder_user, reminder_card):
    """The reason cancelling and firing are two calls rather than one flag.

    ``fire_count`` is the one place somebody looks to find out whether their
    reminder ever reached them, so a cancelled occurrence must not inflate it.
    """
    from checkcheckserver.db.scheduled_notification import cancel, claim_due
    from checkcheckserver.model.scheduled_notification import (
        ReminderRecurrence,
        ReminderStatus,
    )

    async def body(session):
        row = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2008, 8, 8, 9, 0),
            recurrence=ReminderRecurrence.weekly,
        )
        await claim_due(session, row, next_remind_at=_utc(2008, 8, 15, 9, 0))
        await cancel(session, row)
        return row.status, row.fire_count

    status, fire_count = _run(body)
    # Cancelling stops a recurring reminder for good, even though the claim had
    # already rolled it forward.
    assert status == ReminderStatus.cancelled
    assert fire_count == 0


def test_a_cancelled_reminder_is_not_picked_up_again(reminder_user, reminder_card):
    from checkcheckserver.db.scheduled_notification import cancel, due_rows

    async def body(session):
        row = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2009, 9, 9, 9, 0),
        )
        await cancel(session, row)
        return row.id, [r.id for r in await due_rows(session, now=_utc(2009, 9, 9, 9, 0))]

    row_id, due = _run(body)
    assert row_id not in due


# ── retention ─────────────────────────────────────────────────────────────────


def test_pruning_removes_finished_reminders_and_leaves_pending_ones(
    reminder_user, reminder_card
):
    """A reminder set two years out is not stale, it is patient."""
    from checkcheckserver.db.scheduled_notification import (
        cancel,
        get_reminder,
        prune_reminders_once,
    )

    async def body(session):
        user_id = uuid.UUID(str(reminder_user["id"]))
        finished = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2010, 10, 10, 9, 0),
        )
        await cancel(session, finished)
        pending = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2033, 10, 10, 9, 0),
        )
        # Far enough in the future that everything finished is past retention.
        await prune_reminders_once(session, now=_utc(2099, 1, 1))
        return (
            await get_reminder(session, finished.id, user_id),
            await get_reminder(session, pending.id, user_id),
        )

    gone, survived = _run(body)
    assert gone is None
    assert survived is not None


def test_pruning_keeps_a_recently_finished_reminder(reminder_user, reminder_card):
    from checkcheckserver.db.scheduled_notification import (
        cancel,
        get_reminder,
        prune_reminders_once,
    )

    async def body(session):
        user_id = uuid.UUID(str(reminder_user["id"]))
        row = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2011, 11, 11, 9, 0),
        )
        await cancel(session, row)
        # `cancel` stamps updated_at with the real clock, so a cutoff computed
        # from the real clock leaves it alone.
        await prune_reminders_once(session)
        return await get_reminder(session, row.id, user_id)

    assert _run(body) is not None


def test_retention_zero_keeps_everything(reminder_user, reminder_card):
    from checkcheckserver.db.scheduled_notification import (
        cancel,
        get_reminder,
        prune_reminders_once,
    )

    async def body(session):
        user_id = uuid.UUID(str(reminder_user["id"]))
        row = await _create(
            session,
            user=reminder_user,
            card=reminder_card,
            remind_at=_utc(2012, 12, 12, 9, 0),
        )
        await cancel(session, row)
        removed = await prune_reminders_once(
            session, now=_utc(2099, 1, 1), retention_days=0
        )
        return removed, await get_reminder(session, row.id, user_id)

    removed, survived = _run(body)
    assert removed == 0
    assert survived is not None
