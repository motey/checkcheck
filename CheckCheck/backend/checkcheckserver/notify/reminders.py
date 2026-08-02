"""The due-reminder scan (chunk R2 of the date-reminder plan).

One function the dispatcher calls on every tick. It is the only thing in the
codebase that turns time passing into a notification, and it is deliberately
small, because everything it needs already exists: the calendar is
``notify/recurrence.py``, the claim is ``db/scheduled_notification.py``, and the
fan-out to the bell, the inbox and a webhook is the single ``emit_notification``
seam that the sharing notifications already go through.

Three properties are worth reading before changing anything here:

**The claim happens before the emit, and the claim is also the roll forward.**
A row leaves :func:`~checkcheckserver.db.scheduled_notification.claim_due`
already pointing at its next occurrence (recurring) or already ``done``
(one-off), so a process killed between the claim and the emit costs exactly one
occurrence rather than delivering the same reminder on every tick forever.

**Validity is re-checked here and only here** (plan decision 2). The row survives
a card being tombstoned or a share being revoked; just before emitting, this
module asks the one predicate that means "this user can see this card"
(``CheckListCRUD._add_user_has_access_query``) and silently cancels the reminder
if the answer is no. Cancelling eagerly at each revocation seam instead would be
four or five call sites that must all stay correct forever, and any one of them
missed leaks a notification about a card the recipient can no longer open.

**One occurrence per scan, never a backlog.** A server that was down for three
days does not deliver three days of dailies: the roll forward skips every missed
occurrence in one step (``next_occurrence(..., now=now)``) and the user is
reminded once, now.

The scan is bounded by :data:`SCAN_BATCH_LIMIT` so one tick cannot hold the
dispatcher loop. Whatever is left over is still due on the next tick, which is
seconds away.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from checkcheckserver.config import Config
from checkcheckserver.db.scheduled_notification import (
    cancel,
    claim_due,
    due_rows,
    mark_fired,
)
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import naive_utc_now
from checkcheckserver.model.checklist import CheckList
from checkcheckserver.model.notification import NotificationType
from checkcheckserver.model.scheduled_notification import ScheduledNotification
from checkcheckserver.notify.recurrence import next_occurrence


log = get_logger()


# How many due reminders one tick handles. A bound rather than a policy: the tick
# defaults to 30 seconds, so anything beyond this simply goes out a moment later,
# and a loop that spent minutes on one scan would stop draining the outbox.
SCAN_BATCH_LIMIT = 100


@dataclass
class ReminderScanResult:
    """What one scan did. Returned for logging and for tests, not persisted."""

    due: int = 0  # rows the scan looked at
    fired: int = 0  # notifications emitted
    cancelled: int = 0  # rows whose card is gone, or whose access is
    lost: int = 0  # rows another scan claimed first

    @property
    def touched(self) -> int:
        return self.fired + self.cancelled


def reminders_enabled(config: Config) -> bool:
    """Whether this instance delivers reminders at all.

    There is no dedicated master switch on purpose (plan section 6): an operator
    who wants the feature off lists ``reminder_due`` in ``NOTIFY_DISABLED_TYPES``,
    which is the same knob that locks the type in the settings dialog and gives
    the user a reason for it. This makes that one knob also stop the scan, rather
    than leaving it to poll a table whose rows could never be delivered.
    """
    return NotificationType.reminder_due.value not in (
        config.NOTIFY_DISABLED_TYPES or []
    )


async def scan_due_once(
    session: AsyncSession,
    *,
    config: Optional[Config] = None,
    now: Optional[datetime.datetime] = None,
) -> ReminderScanResult:
    """Fire every reminder that has come due, once each. The unit the loop repeats."""
    config = config or Config()
    now = now or naive_utc_now()
    result = ReminderScanResult()
    if not reminders_enabled(config):
        return result

    rows = await due_rows(session, now=now, limit=SCAN_BATCH_LIMIT, config=config)
    result.due = len(rows)
    for row in rows:
        # Computed before the claim because the claim writes it: `now` is passed
        # so an occurrence missed during an outage is skipped rather than
        # replayed (see the module docstring).
        next_at = next_occurrence(
            row.remind_at,
            recurrence=row.recurrence,
            timezone_name=row.timezone,
            anchor_day=row.anchor_day,
            now=now,
        )
        if not await claim_due(session, row, next_remind_at=next_at, now=now):
            # Another scan got there first. Not an error and not worth a log line
            # above debug: the compare-and-swap doing its job is the normal
            # outcome of two overlapping ticks.
            result.lost += 1
            continue

        checklist_name = await _visible_card_name(
            session, user_id=row.user_id, cl_id=row.cl_id
        )
        if checklist_name is None:
            await cancel(session, row, now=now)
            result.cancelled += 1
            log.debug(
                "[reminders] cancelled reminder %s: user %s can no longer see card %s",
                row.id,
                row.user_id,
                row.cl_id,
            )
            continue

        await _emit(session, row, checklist_name=checklist_name, config=config)
        await mark_fired(session, row, now=now)
        result.fired += 1

    if result.touched:
        log.info(
            "[reminders] %s due, %s fired, %s cancelled",
            result.due,
            result.fired,
            result.cancelled,
        )
    return result


async def _visible_card_name(
    session: AsyncSession, *, user_id: uuid.UUID, cl_id: uuid.UUID
) -> Optional[str]:
    """The card's name if this user can still open it, else ``None``.

    The access predicate is borrowed from ``CheckListCRUD`` rather than rebuilt:
    it is the single definition of "this user can see this card" (owner or
    accepted collaborator, tombstone masked, position row required), so every
    future way of losing access is covered here without this module changing.

    The name comes back from the same query on purpose. It is needed for the
    notification payload, and reading it separately would open a window where the
    access check passed and the read did not.
    """
    from checkcheckserver.db.checklist import CheckListCRUD

    crud = CheckListCRUD(session)
    query = crud._add_user_has_access_query(
        select(CheckList.name).where(CheckList.id == cl_id), user_id
    )
    return (await session.exec(query)).one_or_none()


async def _emit(
    session: AsyncSession,
    row: ScheduledNotification,
    *,
    checklist_name: str,
    config: Config,
) -> None:
    """Hand one due reminder to the notification fan-out.

    Imported inside the function because ``db.notification`` reaches back into
    this package (``notify.outbox``, ``notify.render``), and this module is
    imported by the dispatcher: a module-level import would make the order the
    two halves of ``notify`` load in depend on who booted first.

    The payload carries no actor, which is correct rather than an omission: the
    user is reminding themselves. It also means the immediate dedupe key falls
    into its existing "no actor" branch, so several reminders coming due in the
    same minute arrive as one mail.
    """
    from checkcheckserver.db.notification import NotificationCRUD, emit_notification
    from checkcheckserver.db.sync_notification import SyncNotifiationCRUD

    await emit_notification(
        NotificationCRUD(session),
        SyncNotifiationCRUD(session),
        user_id=row.user_id,
        type=NotificationType.reminder_due,
        cl_id=row.cl_id,
        payload={"checklist_name": checklist_name, "note": row.note},
        config=config,
    )
