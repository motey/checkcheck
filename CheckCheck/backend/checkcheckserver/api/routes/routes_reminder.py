"""Reminder API (chunk R3 of the date-reminder plan).

Five endpoints, all authenticated, all scoped to the caller, and all online-only
(plan decision 6: reminders are per-user side data, so they stay out of the delta
feed and out of the offline outbox, exactly like notification settings).

Two rules run through the whole module:

**Somebody else's reminder is a 404 on every verb, never a 403.** A reminder is
personal (decision 1), so the existence of one the caller did not set is not
something they may learn. The ownership filter lives in
``db/scheduled_notification.py``, which returns ``None`` rather than a row the
caller does not own; this module only turns that into a status code.

**The card is authorised, the reminder is owned.** Creating and listing go
through ``require_checklist_permission(view)``, the same dependency every other
card route uses, so a missing card is a 404, a tombstoned one a 410 and a
revoked share a 403 without this module knowing how any of that works. ``view``
is enough on purpose: reminding yourself about a card you can only read changes
nothing for anybody else. The addressed-by-id verbs (``PATCH``, ``DELETE``) do
**not** re-check card access, because a user whose share was revoked must still
be able to delete the reminder they are stuck with; it would never fire anyway
(the scan cancels it at fire time, plan decision 2).

See ``docs/plans/DATE_REMINDERS.md`` sections 7 and 8 R3.
"""

import datetime
import uuid
from typing import Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from pydantic import BaseModel, Field
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from checkcheckserver.api.access import (
    ChecklistAccessLevel,
    UserChecklistAccess,
    require_checklist_permission,
)
from checkcheckserver.api.auth.security import get_current_user
from checkcheckserver.config import Config
from checkcheckserver.db._session import get_async_session
from checkcheckserver.db.checklist import CheckListCRUD
from checkcheckserver.db.scheduled_notification import (
    MAX_PENDING_PER_CARD,
    MAX_PENDING_PER_USER,
    count_pending_for_card,
    count_pending_for_user,
    create_reminder,
    delete_reminder,
    get_reminder,
    list_for_card,
    list_upcoming_for_user,
)
from checkcheckserver.db.user import User
from checkcheckserver.db.user_notification_settings import get_settings
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import naive_utc_now
from checkcheckserver.model.checklist import CheckList
from checkcheckserver.model.scheduled_notification import (
    REMINDER_NOTE_MAX_LENGTH,
    ReminderRecurrence,
    ReminderStatus,
    ScheduledNotification,
)
from checkcheckserver.notify import prefs, reminders
from checkcheckserver.notify.recurrence import anchor_day_for


config = Config()
log = get_logger()

fast_api_reminder_router: APIRouter = APIRouter()


# ── the response ──────────────────────────────────────────────────────────────


class ReminderRead(BaseModel):
    """One reminder, plus the name of the card it is about.

    The name is resolved here rather than left to the client (plan section 7):
    the "my reminders" list spans cards, and a second round trip per row to
    render a heading is the kind of thing that is easy to add and impossible to
    take back.
    """

    id: uuid.UUID
    checklist_id: uuid.UUID = Field(description="The card this reminder is about.")
    checklist_name: Optional[str] = Field(
        default=None,
        description=(
            "Name of that card, or null when the caller can no longer see it "
            "(the card was deleted, or the share was revoked). Such a reminder "
            "is cancelled the moment it would have fired."
        ),
    )
    remind_at: datetime.datetime = Field(
        description="Naive UTC. The next (or only) time this fires."
    )
    timezone: Optional[str] = Field(
        default=None,
        description=(
            "IANA zone the recurrence is computed in, snapshotted when the "
            "reminder was created. Null means UTC."
        ),
    )
    recurrence: ReminderRecurrence
    note: Optional[str] = Field(default=None, description="The user's own text.")
    status: ReminderStatus
    last_fired_at: Optional[datetime.datetime] = None
    fire_count: int
    created_at: datetime.datetime


def _to_read(
    row: ScheduledNotification, checklist_name: Optional[str]
) -> ReminderRead:
    return ReminderRead(
        id=row.id,
        checklist_id=row.cl_id,
        checklist_name=checklist_name,
        remind_at=row.remind_at,
        timezone=row.timezone,
        recurrence=row.recurrence,
        note=row.note,
        status=row.status,
        last_fired_at=row.last_fired_at,
        fire_count=row.fire_count,
        created_at=row.created_at,
    )


# ── the request bodies ────────────────────────────────────────────────────────


class ReminderCreate(BaseModel):
    remind_at: datetime.datetime = Field(
        description=(
            "When to remind. Send it with an offset (`2026-08-03T09:00:00+02:00`) "
            "or as UTC; a value without an offset is read as UTC. Must be in the "
            "future."
        ),
        examples=["2026-08-03T09:00:00+02:00"],
    )
    recurrence: ReminderRecurrence = Field(
        default=ReminderRecurrence.none,
        description=(
            "`none` fires once and is then done. The repeating rules are computed "
            "on the wall clock of the reminder's own time zone, so `daily` stays "
            "at the same local hour across a daylight-saving change."
        ),
    )
    note: Optional[str] = Field(
        default=None,
        max_length=REMINDER_NOTE_MAX_LENGTH,
        description="Optional text of your own, shown in the notification.",
        examples=["Call the plumber"],
    )
    timezone: Optional[str] = Field(
        default=None,
        description=(
            "IANA zone to compute the recurrence in. Only used when the account "
            "has no time zone in its notification settings, which is where this "
            "normally comes from; ignored otherwise. Null means UTC."
        ),
        examples=["Europe/Berlin"],
    )


class ReminderUpdate(BaseModel):
    """A partial update. A field that is absent keeps its stored value.

    The card and the time zone are not in here on purpose: a reminder is about
    one card for its whole life, and its zone is a snapshot taken at creation
    (plan decision 5) so that changing the account's zone later does not shift
    every existing recurring reminder.
    """

    remind_at: Optional[datetime.datetime] = Field(
        default=None,
        description=(
            "A new time, in the future. Sending one also puts a reminder that had "
            "already finished back to `pending`, which is what a snooze is."
        ),
    )
    recurrence: Optional[ReminderRecurrence] = None
    note: Optional[str] = Field(
        default=None,
        max_length=REMINDER_NOTE_MAX_LENGTH,
        description="Null clears the note.",
    )


# ── helpers ───────────────────────────────────────────────────────────────────


def _as_naive_utc(when: datetime.datetime) -> datetime.datetime:
    """The database stores naive UTC; a client may send anything with an offset.

    A value without an offset is taken as UTC rather than rejected: it is what
    every other datetime in this API means, and a client that knows its own zone
    sends the offset.
    """
    if when.tzinfo is None:
        return when
    return when.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def _require_future(when: datetime.datetime) -> None:
    if when <= naive_utc_now():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That time has already passed. Pick a time in the future.",
        )


def _require_reminders_enabled() -> None:
    """Refuse to store a reminder nothing on this instance would ever deliver.

    ``NOTIFY_DISABLED_TYPES`` is the feature's only off switch (plan section 6),
    and it already stops the scan, so accepting the row would mean promising
    something the instance has been configured not to do.
    """
    if not reminders.reminders_enabled(config):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reminders are switched off on this server.",
        )


async def _require_capacity(
    session: AsyncSession, *, user_id: uuid.UUID, cl_id: uuid.UUID
) -> None:
    """Both caps, checked before a new pending reminder is stored.

    409 rather than 400: the request is well formed, there is simply no room
    left. The counts come from ``db/scheduled_notification.py`` rather than
    being re-derived here, so the cap and the query that enforces it cannot
    drift apart.
    """
    if await count_pending_for_card(session, user_id=user_id, cl_id=cl_id) >= (
        MAX_PENDING_PER_CARD
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You already have {MAX_PENDING_PER_CARD} reminders on this card. "
                "Remove one first."
            ),
        )
    if await count_pending_for_user(session, user_id=user_id) >= MAX_PENDING_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You already have {MAX_PENDING_PER_USER} reminders. Remove one "
                "first."
            ),
        )


def _validated_timezone(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return prefs.validate_timezone(value)
    except prefs.UnknownPreferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )


async def _snapshot_timezone(
    session: AsyncSession, *, user_id: uuid.UUID, requested: Optional[str]
) -> Optional[str]:
    """The zone to store on a new reminder (plan decision 5).

    The account's own setting first, because that is the zone the user told the
    app about and the one the daily digest already uses; then whatever the
    client offered, which covers a user who has never opened the settings
    dialog; then null, meaning UTC.
    """
    settings = await get_settings(session, user_id)
    if settings is not None and settings.timezone:
        return settings.timezone
    return _validated_timezone(requested)


async def _visible_card_names(
    session: AsyncSession, *, user_id: uuid.UUID, cl_ids: Iterable[uuid.UUID]
) -> Dict[uuid.UUID, str]:
    """Names of the cards among *cl_ids* this user can still open.

    Borrowed from ``CheckListCRUD`` rather than rebuilt: it is the single
    definition of "this user can see this card", the same one the fire-time
    check uses, so a card missing from the answer here is exactly a card whose
    reminder the scan would cancel. One query for the whole list.
    """
    ids = list({id for id in cl_ids})
    if not ids:
        return {}
    crud = CheckListCRUD(session)
    query = crud._add_user_has_access_query(
        select(CheckList.id, CheckList.name).where(col(CheckList.id).in_(ids)),
        user_id,
    )
    return {row[0]: row[1] for row in (await session.exec(query)).all()}


# ── one card's reminders ──────────────────────────────────────────────────────


@fast_api_reminder_router.get(
    "/checklist/{checklist_id}/reminders",
    response_model=List[ReminderRead],
    description=(
        "The current user's own reminders on this card, soonest first. Never "
        "anybody else's: a reminder is personal, and a card shared with five "
        "people can carry five independent reminders that none of them can see. "
        "Needs `view` permission on the card."
    ),
)
async def list_card_reminders(
    include_finished: bool = Query(
        default=False,
        description=(
            "Also return reminders that have already fired or were cancelled. "
            "They are kept for 30 days and are not going to fire again."
        ),
    ),
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.view)
    ),
    current_user: User = Security(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> List[ReminderRead]:
    rows = await list_for_card(
        session,
        user_id=current_user.id,
        cl_id=checklist_access.checklist.id,
        include_finished=include_finished,
    )
    name = checklist_access.checklist.name
    return [_to_read(row, name) for row in rows]


@fast_api_reminder_router.post(
    "/checklist/{checklist_id}/reminders",
    response_model=ReminderRead,
    status_code=status.HTTP_201_CREATED,
    description=(
        "Set a reminder on this card for yourself. Needs `view` permission, since "
        "reminding yourself about a card you can only read changes nothing for "
        "anybody else, and notifies nobody but you.\n\n"
        "Returns 400 for a time that has passed or a time zone that is not an IANA "
        "name, and 409 when you are at the per-card or per-account limit, or when "
        "an administrator has switched reminders off on this server."
    ),
)
async def create_card_reminder(
    reminder: ReminderCreate,
    checklist_access: UserChecklistAccess = Security(
        require_checklist_permission(ChecklistAccessLevel.view)
    ),
    current_user: User = Security(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> ReminderRead:
    checklist_id = checklist_access.checklist.id
    _require_reminders_enabled()
    remind_at = _as_naive_utc(reminder.remind_at)
    _require_future(remind_at)
    timezone = await _snapshot_timezone(
        session, user_id=current_user.id, requested=reminder.timezone
    )
    await _require_capacity(session, user_id=current_user.id, cl_id=checklist_id)

    row = await create_reminder(
        session,
        user_id=current_user.id,
        cl_id=checklist_id,
        remind_at=remind_at,
        timezone=timezone,
        recurrence=reminder.recurrence,
        note=reminder.note,
    )
    log.debug(
        "[reminders] user %s set a %s reminder on card %s for %s",
        current_user.id,
        reminder.recurrence.value,
        checklist_id,
        remind_at.isoformat(),
    )
    return _to_read(row, checklist_access.checklist.name)


# ── one reminder ──────────────────────────────────────────────────────────────


@fast_api_reminder_router.patch(
    "/reminder/{reminder_id}",
    response_model=ReminderRead,
    description=(
        "Change the time, the repeat rule or the note of one of your own "
        "reminders. Partial: a field you leave out keeps its stored value.\n\n"
        "Sending a new `remind_at` also revives a reminder that had already fired "
        "or been cancelled, which is how a snooze works; that counts against the "
        "limits again, so it can return 409. Returns 400 for a time that has "
        "passed, and **404 for a reminder that is not yours**, exactly as for one "
        "that does not exist."
    ),
)
async def update_reminder(
    reminder_id: uuid.UUID,
    update: ReminderUpdate,
    current_user: User = Security(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> ReminderRead:
    row = await get_reminder(session, reminder_id, current_user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such reminder."
        )

    provided = update.model_fields_set
    reschedules = "remind_at" in provided and update.remind_at is not None
    if reschedules:
        remind_at = _as_naive_utc(update.remind_at)
        _require_future(remind_at)
        if row.status != ReminderStatus.pending:
            # Bringing a finished reminder back is a create in everything but the
            # http verb, so it passes the same two gates a create does.
            _require_reminders_enabled()
            await _require_capacity(
                session, user_id=current_user.id, cl_id=row.cl_id
            )
        row.remind_at = remind_at
        row.status = ReminderStatus.pending
    if "recurrence" in provided and update.recurrence is not None:
        row.recurrence = update.recurrence
    if "note" in provided:
        row.note = update.note

    if reschedules or "recurrence" in provided:
        # The monthly anchor is derived from the intent, so it is recomputed
        # whenever the intent moves: a reminder rescheduled from the 15th to the
        # 31st means the 31st from now on.
        row.anchor_day = anchor_day_for(
            row.remind_at, recurrence=row.recurrence, timezone_name=row.timezone
        )

    session.add(row)
    await session.commit()
    await session.refresh(row)

    names = await _visible_card_names(
        session, user_id=current_user.id, cl_ids=[row.cl_id]
    )
    return _to_read(row, names.get(row.cl_id))


@fast_api_reminder_router.delete(
    "/reminder/{reminder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    description=(
        "Remove one of your own reminders. A real delete, not a tombstone: this "
        "table is not part of the sync feed, so there is no offline client that "
        "could resurrect the row. 404 for a reminder that is not yours, exactly as "
        "for one that does not exist."
    ),
)
async def remove_reminder(
    reminder_id: uuid.UUID,
    current_user: User = Security(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    if not await delete_reminder(session, reminder_id, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such reminder."
        )


# ── everything upcoming ───────────────────────────────────────────────────────


@fast_api_reminder_router.get(
    "/user/me/reminders",
    response_model=List[ReminderRead],
    description=(
        "Every reminder the current user still has coming, across all cards, "
        "soonest first. Reminders on cards the user can no longer open are left "
        "out: they will be cancelled the moment they would have fired, so showing "
        "them would only promise something that is not going to happen."
    ),
)
async def list_my_reminders(
    limit: int = Query(default=100, ge=1, le=200),
    current_user: User = Security(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> List[ReminderRead]:
    rows = await list_upcoming_for_user(
        session, user_id=current_user.id, limit=limit
    )
    names = await _visible_card_names(
        session, user_id=current_user.id, cl_ids=[row.cl_id for row in rows]
    )
    return [_to_read(row, names[row.cl_id]) for row in rows if row.cl_id in names]
