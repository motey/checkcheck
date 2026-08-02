"""When a repeating reminder fires next (chunk R1 of the date-reminder plan).

Pure functions, no database and no config, because every interesting case here is
a calendar case and calendar cases deserve to be tested exhaustively rather than
through a fixture.

The one rule everything else follows from: **a recurrence is a wall-clock
promise**. "Every day at 09:00" means 09:00 on the user's own clock, so the
interval is added in local time and converted back, never added to a UTC
timestamp. Adding 24 hours in UTC would walk the reminder an hour off at every
DST change and nobody would understand why.

Three edges this module takes a position on, all tested:

**A month that is too short.** A reminder set for the 31st fires on 28 or 29
February and then on 31 March again. The clamp applies to the *computed*
occurrence, never to the stored intent, which is what ``anchor_day`` on the row
is for.

**A local time that does not exist, or exists twice.** Spring forward skips an
hour, autumn repeats one. Both are resolved by ``zoneinfo``'s default ``fold=0``
handling: a skipped time shifts forward by the gap, a repeated one takes the
first pass. That is a choice, not an accident. A reminder arriving an hour off
twice a year on the two days a year it can happen beats one that raises.

**An occurrence that is already in the past.** If the server was down for three
days, a daily reminder must fire **once**, not three times, so the roll forward
loops until it lands in the future. Anything else means a restart after an outage
floods somebody's inbox with yesterday.
"""

from __future__ import annotations

import calendar
import datetime
from typing import Optional

from checkcheckserver.log import get_logger
from checkcheckserver.model.scheduled_notification import ReminderRecurrence
# The zone resolver already exists for the daily digest, including the
# "stored zone stopped being valid, warn and fall back to UTC" handling.
# Reused rather than written twice.
from checkcheckserver.notify.schedule import _zone as resolve_zone


log = get_logger()


# A loop bound, not a policy: with the smallest interval (daily) this covers a
# reminder that has been due for well over a century, which can only mean a
# corrupt row or a clock that went backwards. Stopping is better than spinning.
_MAX_ROLL_FORWARD_STEPS = 50_000


def _to_local(when: datetime.datetime, zone: datetime.tzinfo) -> datetime.datetime:
    """Naive UTC to aware local."""
    return when.replace(tzinfo=datetime.timezone.utc).astimezone(zone)


def _to_naive_utc(when: datetime.datetime) -> datetime.datetime:
    """Aware local back to the naive UTC the database stores."""
    return when.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def _add_months(local: datetime.datetime, months: int, anchor_day: int) -> datetime.datetime:
    """Advance *months* calendar months, putting the result on *anchor_day*.

    The day is clamped to the target month's length, so 31 January plus one month
    is 28 (or 29) February, and the month after that is 31 March again, because
    the anchor is passed in rather than read off the previous occurrence.
    """
    month_index = local.month - 1 + months
    year = local.year + month_index // 12
    month = month_index % 12 + 1
    day = min(anchor_day, calendar.monthrange(year, month)[1])
    return local.replace(year=year, month=month, day=day)


def _step(
    local: datetime.datetime,
    recurrence: ReminderRecurrence,
    anchor_day: int,
    steps: int = 1,
) -> datetime.datetime:
    """One interval forward, in local time."""
    if recurrence == ReminderRecurrence.daily:
        return local + datetime.timedelta(days=steps)
    if recurrence == ReminderRecurrence.weekly:
        return local + datetime.timedelta(weeks=steps)
    if recurrence == ReminderRecurrence.monthly:
        return _add_months(local, steps, anchor_day)
    raise ValueError(f"Not a repeating recurrence: {recurrence}")


def anchor_day_for(
    remind_at: datetime.datetime,
    *,
    recurrence: ReminderRecurrence,
    timezone_name: Optional[str] = None,
) -> Optional[int]:
    """The day of month to store on a new monthly reminder, in its own zone.

    ``None`` for every other recurrence, since nothing else needs it. Computed in
    local time on purpose: a 00:30 local reminder is a different calendar day in
    UTC, and the user picked the local one.
    """
    if recurrence != ReminderRecurrence.monthly:
        return None
    return _to_local(remind_at, resolve_zone(timezone_name)).day


def next_occurrence(
    after: datetime.datetime,
    *,
    recurrence: ReminderRecurrence,
    timezone_name: Optional[str] = None,
    anchor_day: Optional[int] = None,
    now: Optional[datetime.datetime] = None,
) -> Optional[datetime.datetime]:
    """The next time a reminder fires after *after*, or ``None`` for a one-off.

    All datetimes are naive UTC, matching what the row stores. *after* is the
    occurrence that just fired. *now* defaults to *after*, and exists so a
    reminder that came due during an outage skips the missed occurrences in one
    step instead of firing for each of them (see the module docstring).

    Returns ``None`` for :attr:`ReminderRecurrence.none`, which is the caller's
    signal to mark the row done.
    """
    if recurrence == ReminderRecurrence.none:
        return None

    zone = resolve_zone(timezone_name)
    local = _to_local(after, zone)
    if recurrence == ReminderRecurrence.monthly and anchor_day is None:
        # A monthly row that predates its anchor, or one built by hand. The day of
        # the occurrence we are stepping from is the best guess available, and it
        # is right for every reminder that has never crossed a short month.
        anchor_day = local.day

    floor = _to_local(now if now is not None else after, zone)
    candidate = _step(local, recurrence, anchor_day or local.day)
    steps = 1
    while candidate <= floor:
        if steps >= _MAX_ROLL_FORWARD_STEPS:
            log.warning(
                "[reminders] giving up rolling a %s reminder forward from %s",
                recurrence.value,
                after.isoformat(),
            )
            return None
        steps += 1
        candidate = _step(local, recurrence, anchor_day or local.day, steps)

    return _to_naive_utc(candidate)
