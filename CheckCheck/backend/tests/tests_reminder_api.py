"""Tests for chunk R3 of the date-reminder sub-project: the API and the prefs rule.

R1 shipped a table and a calendar, R2 made a due row fire. This is the chunk
where a user can finally create one, so these tests are about the four things
the endpoints are responsible for: who may address a reminder, what a reminder
is allowed to say, how many of them one account may keep, and the one preference
rule that a reminder does not share with the other notification types (plan
decision 8: no digest, because a reminder held back until tomorrow morning is not
a reminder).

**How they run.** Over real HTTP against the running test server, like the rest
of the suite. Two cases cannot be: an instance where an administrator switched
reminders off is a process-level setting, so that one calls the endpoint function
in this process with a substituted ``Config`` (the seam
``tests_notification_prefs.py`` established), and the per-account cap is seeded
straight into the database because 200 HTTP round trips to prove an off-by-one is
not worth the wall clock.

**Isolation.** Every reminder these tests create is due in 2090, so no scan
anywhere can pick one up, and the accounts whose *whole* reminder list is
asserted on (the cap, the cross-card list) are their own. Each test removes what
it made.

The plan is ``docs/plans/DATE_REMINDERS.md`` (sections 7 and 8 R3).
"""

import asyncio
import datetime
import uuid

import pytest

from utils import authorize_for_access_token, create_test_user, req
from statics import MAIL_CAPTURE_FROM_ADDRESS


OWNER_NAME = "reminderapiowner"
OWNER_PW = "reminderapiownerpw_secure1"
OWNER_EMAIL = "reminderapiowner@test.de"

COLLAB_NAME = "reminderapicollab"
COLLAB_PW = "reminderapicollabpw_secure1"
COLLAB_EMAIL = "reminderapicollab@test.de"

STRANGER_NAME = "reminderapistranger"
STRANGER_PW = "reminderapistrangerpw_secure1"
STRANGER_EMAIL = "reminderapistranger@test.de"

# Accounts whose complete reminder list a test asserts on, so nothing another
# test created can drift into the answer.
CAP_NAME = "reminderapicapuser"
CAP_PW = "reminderapicapuserpw_secure1"
CAP_EMAIL = "reminderapicapuser@test.de"

LIST_NAME = "reminderapilistuser"
LIST_PW = "reminderapilistuserpw_secure1"
LIST_EMAIL = "reminderapilistuser@test.de"


# ── helpers ───────────────────────────────────────────────────────────────────


def _run(body):
    """Run *body* with a fresh session against the test database.

    Same shape (and same reasons) as its twins in ``tests_reminder_scan.py`` and
    ``tests_notification_prefs.py``: lazy imports so nothing binds to the
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


def _uid(user: dict) -> uuid.UUID:
    return uuid.UUID(str(user["id"]))


def _at(*args) -> str:
    """A naive-UTC instant as the API takes it. Always far enough out that no
    scan can claim the row while the suite is running."""
    return datetime.datetime(*args).isoformat()


def _user(name, pw, email) -> dict:
    user = create_test_user(name, pw, email)
    user["token"] = authorize_for_access_token(name, pw)
    return user


@pytest.fixture(scope="module")
def owner() -> dict:
    return _user(OWNER_NAME, OWNER_PW, OWNER_EMAIL)


@pytest.fixture(scope="module")
def collaborator() -> dict:
    return _user(COLLAB_NAME, COLLAB_PW, COLLAB_EMAIL)


@pytest.fixture(scope="module")
def stranger() -> dict:
    return _user(STRANGER_NAME, STRANGER_PW, STRANGER_EMAIL)


@pytest.fixture(scope="module")
def cap_user() -> dict:
    return _user(CAP_NAME, CAP_PW, CAP_EMAIL)


@pytest.fixture(scope="module")
def list_user() -> dict:
    return _user(LIST_NAME, LIST_PW, LIST_EMAIL)


def _card(user: dict, name: str) -> dict:
    return req("api/checklist", "post", b={"name": name}, access_token=user["token"])


def _share(card: dict, owner: dict, other: dict, permission: str = "view") -> None:
    req(
        f"api/checklist/{card['id']}/shares/{other['id']}",
        "put",
        b={"permission": permission},
        access_token=owner["token"],
    )


def _create(user: dict, card: dict, **body) -> dict:
    body.setdefault("remind_at", _at(2090, 1, 1, 9, 0))
    return req(
        f"api/checklist/{card['id']}/reminders",
        "post",
        b=body,
        access_token=user["token"],
        expected_http_code=201,
    )


def _list(user: dict, card: dict, **q) -> list:
    return req(
        f"api/checklist/{card['id']}/reminders",
        q=q or None,
        access_token=user["token"],
    )


def _drop(user: dict, reminder: dict) -> None:
    req(
        f"api/reminder/{reminder['id']}",
        "delete",
        expected_http_code=204,
        access_token=user["token"],
    )


def _stored(reminder_id):
    """The row as the database has it, for the columns the API does not return."""

    async def body(session):
        from sqlmodel import select

        from checkcheckserver.model.scheduled_notification import ScheduledNotification

        result = await session.exec(
            select(ScheduledNotification)
            .where(ScheduledNotification.id == uuid.UUID(str(reminder_id)))
            .execution_options(populate_existing=True)
        )
        return result.one_or_none()

    return _run(body)


def _wipe(user: dict) -> None:
    """Remove every reminder of *user*, whatever state it is in."""

    async def body(session):
        from sqlmodel import col, delete

        from checkcheckserver.model.scheduled_notification import ScheduledNotification

        await session.exec(
            delete(ScheduledNotification).where(
                col(ScheduledNotification.user_id) == _uid(user)
            )
        )
        await session.commit()

    _run(body)


# ── creating ──────────────────────────────────────────────────────────────────


def test_a_reminder_is_created_and_read_back_with_the_card_name(owner):
    """The happy path, and the one thing the response carries beyond the row:
    the card's name, so a list spanning cards needs no second round trip."""
    card = _card(owner, "Reminder API: create")

    created = _create(
        owner,
        card,
        remind_at=_at(2090, 3, 1, 9, 0),
        note="Call the plumber",
        recurrence="weekly",
    )

    assert created["checklist_id"] == card["id"]
    assert created["checklist_name"] == "Reminder API: create"
    assert created["remind_at"].startswith("2090-03-01T09:00:00")
    assert created["recurrence"] == "weekly"
    assert created["note"] == "Call the plumber"
    assert created["status"] == "pending"
    assert created["fire_count"] == 0
    assert created["last_fired_at"] is None

    listed = _list(owner, card)
    assert [r["id"] for r in listed] == [created["id"]]

    _drop(owner, created)
    assert _list(owner, card) == []


def test_the_list_is_soonest_first_and_hides_finished_reminders_by_default(owner):
    from checkcheckserver.db.scheduled_notification import cancel
    from checkcheckserver.model.scheduled_notification import ScheduledNotification

    card = _card(owner, "Reminder API: ordering")
    later = _create(owner, card, remind_at=_at(2090, 5, 1, 9, 0))
    sooner = _create(owner, card, remind_at=_at(2090, 4, 1, 9, 0))
    finished = _create(owner, card, remind_at=_at(2090, 4, 15, 9, 0))

    async def body(session):
        row = await session.get(
            ScheduledNotification, uuid.UUID(str(finished["id"]))
        )
        await cancel(session, row)

    _run(body)

    assert [r["id"] for r in _list(owner, card)] == [sooner["id"], later["id"]]
    # Asking for them explicitly is what the "already happened" view would do.
    with_finished = _list(owner, card, include_finished=True)
    assert {r["id"] for r in with_finished} == {
        sooner["id"],
        later["id"],
        finished["id"],
    }
    assert [r for r in with_finished if r["id"] == finished["id"]][0]["status"] == (
        "cancelled"
    )

    for reminder in (later, sooner, finished):
        _drop(owner, reminder)


def test_a_time_that_has_already_passed_is_refused(owner):
    card = _card(owner, "Reminder API: past")

    req(
        f"api/checklist/{card['id']}/reminders",
        "post",
        b={"remind_at": _at(2020, 1, 1, 9, 0)},
        expected_http_code=400,
        access_token=owner["token"],
    )
    assert _list(owner, card) == []


def test_a_time_zone_that_is_not_an_iana_name_is_refused(owner):
    card = _card(owner, "Reminder API: bad zone")

    req(
        f"api/checklist/{card['id']}/reminders",
        "post",
        b={"remind_at": _at(2090, 1, 1, 9, 0), "timezone": "Middle/Earth"},
        expected_http_code=400,
        access_token=owner["token"],
    )
    assert _list(owner, card) == []


def test_a_note_longer_than_the_column_is_refused(owner):
    card = _card(owner, "Reminder API: long note")

    req(
        f"api/checklist/{card['id']}/reminders",
        "post",
        b={"remind_at": _at(2090, 1, 1, 9, 0), "note": "x" * 201},
        expected_http_code=422,
        access_token=owner["token"],
    )


def test_a_time_with_an_offset_is_stored_as_utc(owner):
    """The database column is naive UTC, and a client that knows its own zone
    sends the offset rather than doing the conversion itself."""
    card = _card(owner, "Reminder API: offset")

    created = _create(owner, card, remind_at="2090-06-01T09:00:00+02:00")

    assert created["remind_at"].startswith("2090-06-01T07:00:00")
    _drop(owner, created)


def test_the_zone_snapshot_prefers_the_accounts_own_setting(owner):
    """Plan decision 5: the account's zone is where this normally comes from, and
    the one the client sends only covers a user who never opened the settings
    dialog."""
    card = _card(owner, "Reminder API: zone snapshot")

    req(
        "api/user/me/notification-settings",
        "put",
        b={"timezone": "Europe/Berlin"},
        access_token=owner["token"],
    )
    from_account = _create(
        owner, card, remind_at=_at(2090, 7, 1, 9, 0), timezone="Pacific/Auckland"
    )
    assert from_account["timezone"] == "Europe/Berlin"

    req(
        "api/user/me/notification-settings",
        "put",
        b={"timezone": None},
        access_token=owner["token"],
    )
    from_client = _create(
        owner, card, remind_at=_at(2090, 7, 2, 9, 0), timezone="Pacific/Auckland"
    )
    assert from_client["timezone"] == "Pacific/Auckland"

    # And nothing anywhere means UTC.
    bare = _create(owner, card, remind_at=_at(2090, 7, 3, 9, 0))
    assert bare["timezone"] is None

    for reminder in (from_account, from_client, bare):
        _drop(owner, reminder)


def test_a_monthly_reminder_stores_the_day_it_was_set_for(owner):
    """The anchor is derived by the API, not sent by the client: it is the one
    column that lets a reminder set for the 31st survive February."""
    card = _card(owner, "Reminder API: monthly anchor")

    created = _create(
        owner,
        card,
        remind_at=_at(2090, 1, 31, 9, 0),
        recurrence="monthly",
    )

    assert _stored(created["id"]).anchor_day == 31
    _drop(owner, created)


# ── who may address what ──────────────────────────────────────────────────────


def test_view_permission_is_enough_and_the_reminder_stays_private(owner, collaborator):
    """Plan decision 1: reminding yourself about a card you can only read changes
    nothing for anybody else, and the owner never learns you did it."""
    card = _card(owner, "Reminder API: shared card")
    _share(card, owner, collaborator, "view")

    theirs = _create(collaborator, card, note="Their own reminder")

    assert [r["id"] for r in _list(collaborator, card)] == [theirs["id"]]
    # The same endpoint, the same card, the owner's own token: nothing.
    assert _list(owner, card) == []

    _drop(collaborator, theirs)


def test_a_card_the_caller_cannot_see_is_403_and_a_deleted_one_is_410(
    owner, stranger
):
    """Both come from the shared access dependency, which is the point of using
    it: this module does not get to invent its own answer."""
    card = _card(owner, "Reminder API: no access")

    req(
        f"api/checklist/{card['id']}/reminders",
        "post",
        b={"remind_at": _at(2090, 1, 1, 9, 0)},
        expected_http_code=403,
        access_token=stranger["token"],
    )
    req(
        f"api/checklist/{card['id']}/reminders",
        expected_http_code=403,
        access_token=stranger["token"],
    )

    req(f"api/checklist/{card['id']}", "delete", access_token=owner["token"])
    req(
        f"api/checklist/{card['id']}/reminders",
        "post",
        b={"remind_at": _at(2090, 1, 1, 9, 0)},
        expected_http_code=410,
        access_token=owner["token"],
    )


def test_somebody_elses_reminder_is_a_404_on_every_verb(owner, stranger):
    """Never a 403. A reminder somebody else set is not a thing the caller may
    learn about, and a 403 would confirm that this id names one."""
    card = _card(owner, "Reminder API: not yours")
    _share(card, owner, stranger, "view")
    mine = _create(owner, card, note="Mine")

    req(
        f"api/reminder/{mine['id']}",
        "patch",
        b={"note": "Hijacked"},
        expected_http_code=404,
        access_token=stranger["token"],
    )
    req(
        f"api/reminder/{mine['id']}",
        "delete",
        expected_http_code=404,
        access_token=stranger["token"],
    )
    # Even though the stranger can see the card itself, and so gets a list.
    assert _list(stranger, card) == []

    # Untouched.
    assert _list(owner, card)[0]["note"] == "Mine"
    _drop(owner, mine)


def test_an_id_that_does_not_exist_is_the_same_404(owner):
    missing = str(uuid.uuid4())
    req(
        f"api/reminder/{missing}",
        "patch",
        b={"note": "x"},
        expected_http_code=404,
        access_token=owner["token"],
    )
    req(
        f"api/reminder/{missing}",
        "delete",
        expected_http_code=404,
        access_token=owner["token"],
    )


def test_reminders_need_a_logged_in_user(owner):
    card = _card(owner, "Reminder API: anonymous")

    req(
        f"api/checklist/{card['id']}/reminders",
        expected_http_code=401,
        suppress_auth=True,
    )
    req(
        f"api/checklist/{card['id']}/reminders",
        "post",
        b={"remind_at": _at(2090, 1, 1, 9, 0)},
        expected_http_code=401,
        suppress_auth=True,
    )
    req("api/user/me/reminders", expected_http_code=401, suppress_auth=True)
    req(
        f"api/reminder/{uuid.uuid4()}",
        "delete",
        expected_http_code=401,
        suppress_auth=True,
    )


# ── changing and removing ─────────────────────────────────────────────────────


def test_patch_changes_the_time_the_repeat_rule_and_the_note(owner):
    card = _card(owner, "Reminder API: patch")
    created = _create(owner, card, note="First", recurrence="daily")

    changed = req(
        f"api/reminder/{created['id']}",
        "patch",
        b={"remind_at": _at(2090, 2, 2, 8, 30), "recurrence": "none", "note": None},
        access_token=owner["token"],
    )

    assert changed["remind_at"].startswith("2090-02-02T08:30:00")
    assert changed["recurrence"] == "none"
    assert changed["note"] is None
    assert changed["checklist_name"] == "Reminder API: patch"

    # A field the body does not mention keeps its stored value, which is what
    # makes the note editable without re-sending a time.
    note_only = req(
        f"api/reminder/{created['id']}",
        "patch",
        b={"note": "Second"},
        access_token=owner["token"],
    )
    assert note_only["note"] == "Second"
    assert note_only["remind_at"] == changed["remind_at"]
    assert note_only["recurrence"] == "none"

    _drop(owner, created)


def test_patch_moves_the_monthly_anchor_with_the_time(owner):
    """A reminder rescheduled from the 15th to the 31st means the 31st from now
    on, so the anchor follows the intent rather than staying where it was."""
    card = _card(owner, "Reminder API: patch anchor")
    created = _create(
        owner, card, remind_at=_at(2090, 1, 15, 9, 0), recurrence="monthly"
    )
    assert _stored(created["id"]).anchor_day == 15

    req(
        f"api/reminder/{created['id']}",
        "patch",
        b={"remind_at": _at(2090, 1, 31, 9, 0)},
        access_token=owner["token"],
    )

    assert _stored(created["id"]).anchor_day == 31
    _drop(owner, created)


def test_patch_into_the_past_is_refused(owner):
    card = _card(owner, "Reminder API: patch past")
    created = _create(owner, card)

    req(
        f"api/reminder/{created['id']}",
        "patch",
        b={"remind_at": _at(2020, 1, 1, 9, 0)},
        expected_http_code=400,
        access_token=owner["token"],
    )
    assert _stored(created["id"]).remind_at == datetime.datetime(2090, 1, 1, 9, 0)

    _drop(owner, created)


def test_a_new_time_revives_a_finished_reminder(owner):
    """What a snooze is, in one request. A reminder that has fired (or that the
    scan cancelled) goes back to pending when it is given a new time; without
    that, a PATCH on a finished row would accept a time nothing would ever act
    on."""
    from checkcheckserver.db.scheduled_notification import cancel
    from checkcheckserver.model.scheduled_notification import ScheduledNotification

    card = _card(owner, "Reminder API: revive")
    created = _create(owner, card)

    async def body(session):
        row = await session.get(
            ScheduledNotification, uuid.UUID(str(created["id"]))
        )
        await cancel(session, row)

    _run(body)
    assert _stored(created["id"]).status == "cancelled"

    revived = req(
        f"api/reminder/{created['id']}",
        "patch",
        b={"remind_at": _at(2090, 9, 9, 9, 0)},
        access_token=owner["token"],
    )

    assert revived["status"] == "pending"
    assert revived["remind_at"].startswith("2090-09-09T09:00:00")
    # A note-only edit leaves a finished reminder finished, which is right: it
    # changes what the row says, not whether it is going to happen.
    _drop(owner, created)


def test_delete_is_real_and_the_second_one_is_a_404(owner):
    card = _card(owner, "Reminder API: delete")
    created = _create(owner, card)

    _drop(owner, created)

    assert _stored(created["id"]) is None
    req(
        f"api/reminder/{created['id']}",
        "delete",
        expected_http_code=404,
        access_token=owner["token"],
    )


# ── the caps ──────────────────────────────────────────────────────────────────


def test_the_per_card_cap_is_enforced(owner):
    from checkcheckserver.db.scheduled_notification import MAX_PENDING_PER_CARD

    card = _card(owner, "Reminder API: per-card cap")
    created = [
        _create(owner, card, remind_at=_at(2090, 1, 1, 9, minute))
        for minute in range(MAX_PENDING_PER_CARD)
    ]

    req(
        f"api/checklist/{card['id']}/reminders",
        "post",
        b={"remind_at": _at(2090, 1, 1, 10, 0)},
        expected_http_code=409,
        access_token=owner["token"],
    )

    # Room for one more the moment something goes, so the cap is a limit rather
    # than a lifetime quota.
    _drop(owner, created[0])
    replacement = _create(owner, card, remind_at=_at(2090, 1, 1, 10, 0))

    for reminder in created[1:] + [replacement]:
        _drop(owner, reminder)


def test_the_per_account_cap_is_enforced(cap_user):
    """Seeded rather than driven through the API: 200 round trips to prove an
    off-by-one is not worth the wall clock. They all sit on one card, which the
    per-card cap would never allow, so the 409 this asserts can only be the
    per-account one."""
    from checkcheckserver.db.scheduled_notification import (
        MAX_PENDING_PER_USER,
        create_reminder,
    )

    crowded = _card(cap_user, "Reminder API: per-account cap, seeded")
    fresh = _card(cap_user, "Reminder API: per-account cap, empty card")

    async def body(session):
        for index in range(MAX_PENDING_PER_USER):
            await create_reminder(
                session,
                user_id=_uid(cap_user),
                cl_id=uuid.UUID(str(crowded["id"])),
                remind_at=datetime.datetime(2090, 1, 1, 9, 0)
                + datetime.timedelta(minutes=index),
                commit=False,
            )
        await session.commit()

    _run(body)

    # An empty card, so the per-card cap has nothing to say about it.
    req(
        f"api/checklist/{fresh['id']}/reminders",
        "post",
        b={"remind_at": _at(2090, 1, 1, 9, 0)},
        expected_http_code=409,
        access_token=cap_user["token"],
    )

    _wipe(cap_user)
    # With room again, the same request is fine.
    _drop(cap_user, _create(cap_user, fresh))


# ── everything upcoming ───────────────────────────────────────────────────────


def test_my_reminders_spans_cards_and_carries_only_the_callers_own(
    list_user, stranger
):
    first = _card(list_user, "Reminder API: mine, card one")
    second = _card(list_user, "Reminder API: mine, card two")
    _share(second, list_user, stranger, "view")

    later = _create(list_user, first, remind_at=_at(2090, 8, 2, 9, 0))
    sooner = _create(list_user, second, remind_at=_at(2090, 8, 1, 9, 0))
    theirs = _create(stranger, second, remind_at=_at(2090, 8, 1, 10, 0))

    mine = req("api/user/me/reminders", access_token=list_user["token"])

    assert [r["id"] for r in mine] == [sooner["id"], later["id"]]
    assert [r["checklist_name"] for r in mine] == [
        "Reminder API: mine, card two",
        "Reminder API: mine, card one",
    ]
    assert theirs["id"] not in [r["id"] for r in mine]

    for user, reminder in ((list_user, later), (list_user, sooner), (stranger, theirs)):
        _drop(user, reminder)


def test_my_reminders_leaves_out_a_card_the_caller_can_no_longer_open(
    owner, list_user
):
    """Such a row is cancelled the moment it would have fired (plan decision 2),
    so listing it would only promise something that is not going to happen."""
    card = _card(owner, "Reminder API: revoked before it fires")
    _share(card, owner, list_user, "view")
    doomed = _create(list_user, card, remind_at=_at(2090, 10, 1, 9, 0))

    assert doomed["id"] in [
        r["id"] for r in req("api/user/me/reminders", access_token=list_user["token"])
    ]

    req(
        f"api/checklist/{card['id']}/shares/{list_user['id']}",
        "delete",
        expected_http_code=204,
        access_token=owner["token"],
    )

    assert doomed["id"] not in [
        r["id"] for r in req("api/user/me/reminders", access_token=list_user["token"])
    ]
    # The row itself is still there; the user just cannot see the card any more.
    assert _stored(doomed["id"]) is not None
    _wipe(list_user)


# ── an instance with reminders switched off ───────────────────────────────────


def test_an_instance_with_reminders_disabled_refuses_to_store_one(owner):
    """``NOTIFY_DISABLED_TYPES`` is the feature's only off switch (plan section
    6). It already stops the scan; accepting a row here would mean promising
    something the instance is configured never to do.

    Called in this process with a substituted Config, because the cap is an
    instance-wide setting and the running test server does not have it.
    """
    from fastapi import HTTPException

    from checkcheckserver.api.access import UserChecklistAccess
    from checkcheckserver.api.routes import routes_reminder as endpoint
    from checkcheckserver.db.user import User
    from checkcheckserver.model.checklist import CheckList

    card = _card(owner, "Reminder API: disabled instance")
    disabled = _config(NOTIFY_DISABLED_TYPES=["reminder_due"])

    async def body(session):
        user = await session.get(User, _uid(owner))
        checklist = await session.get(CheckList, uuid.UUID(str(card["id"])))
        access = UserChecklistAccess(
            user=user, checklist=checklist, collaborators=[]
        )
        create = endpoint.ReminderCreate(remind_at=datetime.datetime(2090, 1, 1, 9, 0))

        original = endpoint.config
        endpoint.config = disabled
        try:
            await endpoint.create_card_reminder(
                reminder=create,
                checklist_access=access,
                current_user=user,
                session=session,
            )
        except HTTPException as exc:
            refused = exc.status_code
        else:
            refused = 201
        finally:
            endpoint.config = original

        # The same call on an instance that has them on.
        created = await endpoint.create_card_reminder(
            reminder=create,
            checklist_access=access,
            current_user=user,
            session=session,
        )
        return refused, created

    refused, created = _run(body)

    assert refused == 409
    assert created.status == "pending"
    _drop(owner, {"id": str(created.id)})


# ── decision 8: a reminder does not go into a digest ──────────────────────────


def test_a_reminder_offers_no_digest_on_the_email_channel():
    """The whole of decision 8, at the level the rest of the code asks about it."""
    from checkcheckserver.notify.prefs import (
        PreferenceChannel,
        allowed_modes,
        mode_restriction_reason,
    )

    assert allowed_modes(PreferenceChannel.email, "reminder_due") == [
        "off",
        "immediate",
    ]
    assert mode_restriction_reason("reminder_due", PreferenceChannel.email)

    # Narrowing only, and only for this type on this channel.
    assert allowed_modes(PreferenceChannel.email, "card_shared") == [
        "off",
        "immediate",
        "hourly",
        "daily",
    ]
    assert mode_restriction_reason("card_shared", PreferenceChannel.email) is None
    assert allowed_modes(PreferenceChannel.email) == [
        "off",
        "immediate",
        "hourly",
        "daily",
    ]
    # The bell and the webhook never offered one anyway.
    assert allowed_modes(PreferenceChannel.in_app, "reminder_due") == [
        "off",
        "immediate",
    ]


def test_a_digest_stored_for_a_reminder_falls_through_instead_of_being_honoured():
    """The restriction is applied on the read path too, because a rule added in a
    later release finds rows that were legal when they were saved."""
    from checkcheckserver.model.user_notification_settings import (
        UserNotificationSettings,
    )
    from checkcheckserver.notify.prefs import NotificationMode, resolve_mode

    stored = UserNotificationSettings(
        user_id=uuid.uuid4(), prefs={"reminder_due": {"email": "daily"}}
    )
    config = _config(NOTIFY_DEFAULT_MODES={"reminder_due": {"email": "immediate"}})

    assert resolve_mode(stored, "reminder_due", "email", config=config) == (
        NotificationMode.immediate
    )
    # An operator's default is held to the same rule.
    bad_default = _config(NOTIFY_DEFAULT_MODES={"reminder_due": {"email": "hourly"}})
    assert resolve_mode(None, "reminder_due", "email", config=bad_default) == (
        NotificationMode.off  # the code default, mail off
    )
    # The same stored value is still honoured for a type that does offer digests.
    also_stored = UserNotificationSettings(
        user_id=uuid.uuid4(), prefs={"card_shared": {"email": "daily"}}
    )
    assert resolve_mode(also_stored, "card_shared", "email", config=config) == (
        NotificationMode.daily
    )


def test_the_settings_matrix_stops_offering_a_digest_for_reminders(owner):
    """What the settings dialog reads, so it does not have to know the rule."""
    settings = req(
        "api/user/me/notification-settings", access_token=owner["token"]
    )

    def entry(type, channel):
        return [t for t in settings["types"] if t["type"] == type][0]["channels"][
            channel
        ]

    reminder_email = entry("reminder_due", "email")
    assert reminder_email["allowed_modes"] == ["off", "immediate"]
    assert reminder_email["mode_restriction_reason"]
    # Shipped default: a reminder mails immediately (plan decision 7).
    assert reminder_email["default_mode"] == "immediate"

    shared_email = entry("card_shared", "email")
    assert shared_email["allowed_modes"] == ["off", "immediate", "hourly", "daily"]
    assert shared_email["mode_restriction_reason"] is None


def test_put_refuses_a_digest_for_reminders_and_accepts_the_other_two(owner):
    for mode in ("hourly", "daily"):
        req(
            "api/user/me/notification-settings",
            "put",
            b={"prefs": {"reminder_due": {"email": mode}}},
            expected_http_code=400,
            access_token=owner["token"],
        )

    for mode in ("off", "immediate"):
        saved = req(
            "api/user/me/notification-settings",
            "put",
            b={"prefs": {"reminder_due": {"email": mode}}},
            access_token=owner["token"],
        )
        entry = [t for t in saved["types"] if t["type"] == "reminder_due"][0]
        assert entry["channels"]["email"]["mode"] == mode

    # Put the account back to inheriting, so nothing after this inherits a choice.
    req(
        "api/user/me/notification-settings",
        "put",
        b={"prefs": {"reminder_due": {"email": None}}},
        access_token=owner["token"],
    )
