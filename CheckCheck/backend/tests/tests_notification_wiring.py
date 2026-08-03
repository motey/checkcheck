"""Tests for chunk E4 of the notification sub-project: the fan-out itself.

E1 to E3 built a mail transport, a queue and a preference resolver that nothing
consumed. This is the chunk where a real notification turns into a real message,
so these tests are almost all about the *decisions* taken on the way: whether to
queue at all, when the message becomes due, what it may say, and what collapses
into what.

**How they run.** ``emit_notification`` is called in this process, with the real
CRUD objects and the real database, and usually with a substituted ``Config`` so
one test can look like an instance with mail off and the next like one where an
administrator disabled a type. The messages are then produced by calling
``drain_once()`` directly, with the capturing transport from the ``mail_capture``
fixture installed.

**Isolation.** Unlike ``tests_notification_outbox.py``, these cannot work in a
time window far in the past: the fan-out stamps ``not_before`` itself, from the
real clock. So they drain from a little way in the *future* instead, which also
picks up rows other tests left behind, and every assertion is therefore filtered
by the recipient's address. Each test that cares owns a user nobody else touches.

The plan is ``docs/plans/EMAIL_NOTIFICATIONS.md`` (sections 4, 4.1, 6, 7 E4).
"""

import asyncio
import datetime
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from utils import (
    authorize_for_access_token,
    create_test_user,
    get_server_base_url,
    req,
)
from statics import MAIL_CAPTURE_FROM_ADDRESS


# ── helpers ───────────────────────────────────────────────────────────────────


def _run(body):
    """Run *body* with a fresh session against the test database.

    Same shape (and same reasons) as its twin in ``tests_notification_outbox.py``:
    lazy imports so nothing binds to the environment before the session fixtures
    have run, and the engine is disposed because every test drives its own event
    loop with ``asyncio.run``.
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


async def _emit(session, *, user_id, type, cl_id=None, payload=None, config=None):
    """Call the seam under test with the real CRUD objects."""
    from checkcheckserver.db.notification import NotificationCRUD, emit_notification
    from checkcheckserver.db.sync_notification import SyncNotifiationCRUD

    return await emit_notification(
        NotificationCRUD(session),
        SyncNotifiationCRUD(session),
        user_id=user_id,
        type=type,
        cl_id=cl_id or uuid.uuid4(),
        payload=payload,
        config=config or _config(),
    )


async def _rows(session, user_id):
    """Every outbox row belonging to one user, oldest first."""
    from sqlmodel import select

    from checkcheckserver.model.notification_outbox import NotificationOutbox

    result = await session.exec(
        select(NotificationOutbox)
        .where(NotificationOutbox.user_id == user_id)
        .order_by(NotificationOutbox.created_at)
        .execution_options(populate_existing=True)
    )
    return list(result.all())


async def _feed(session, user_id):
    from sqlmodel import select

    from checkcheckserver.model.notification import Notification

    result = await session.exec(
        select(Notification).where(Notification.user_id == user_id)
    )
    return list(result.all())


async def _set_prefs(session, user_id, patch):
    from checkcheckserver.db.user_notification_settings import get_or_create_settings
    from checkcheckserver.notify.prefs import apply_prefs_patch

    settings = await get_or_create_settings(session, user_id)
    settings.prefs = apply_prefs_patch(settings.prefs, patch)
    session.add(settings)
    await session.commit()
    return settings


async def _drain(session, *, minutes_ahead=5, config=None):
    """Deliver everything that is due *minutes_ahead* from now.

    The suppression window is two minutes by default, so anything an immediate
    fan-out queued in this test has come due by then. Rows belonging to other
    tests come along for the ride, which is why every assertion filters by
    address.
    """
    from checkcheckserver.model._base_model import naive_utc_now
    from checkcheckserver.notify.outbox import drain_once

    return await drain_once(
        session,
        now=naive_utc_now() + datetime.timedelta(minutes=minutes_ahead),
        config=config,
    )


def _mail_to(mail_capture, address):
    return [email for email in mail_capture.sent if email.to == address]


def _share_payload(actor_id=None, actor="Anna Analyst", card="Weekend groceries"):
    """What the share routes put in a notification payload."""
    return {
        "actor_id": str(actor_id or uuid.uuid4()),
        "actor_user_name": "anna",
        "actor_display_name": actor,
        "checklist_name": card,
    }


@pytest.fixture(scope="module")
def user_factory():
    """Accounts owned by exactly one test each, so mail can be told apart."""
    made = {}

    def make(slug: str):
        if slug not in made:
            name = f"e4{slug}user"
            password = f"{name}_pw_secure1"
            email = f"{name}@test.de"
            created = create_test_user(name, password, email)
            made[slug] = SimpleNamespace(
                id=uuid.UUID(created["id"]),
                name=name,
                password=password,
                email=email,
            )
        return made[slug]

    return make


# ── one message per notification type ─────────────────────────────────────────


def test_a_shared_card_produces_one_mail_naming_the_card_and_the_actor(
    mail_capture, user_factory
):
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationOutboxStatus

    user = user_factory("shared")
    cl_id = uuid.uuid4()

    async def body(session):
        noti = await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            cl_id=cl_id,
            payload=_share_payload(),
        )
        queued = await _rows(session, user.id)
        await _drain(session)
        return noti, queued, await _rows(session, user.id)

    noti, queued, rows = _run(body)
    mail = _mail_to(mail_capture, user.email)

    # The feed entry still happens, and the mail is linked to it so reading the
    # notification in the app can still cancel a message that is not out yet.
    assert noti is not None
    assert len(queued) == 1 and queued[0].notification_id == noti.id
    assert len(mail) == 1
    assert rows[0].status == NotificationOutboxStatus.sent.value

    assert "Anna Analyst" in mail[0].subject
    assert "Weekend groceries" in mail[0].subject
    # The deep link carries both parameters: the card to open and the
    # notification to mark read once it is on screen (chunk E5).
    assert f"?card={cl_id}&n={noti.id}" in mail[0].text_body
    # Same link in the HTML part, with the separator escaped as HTML requires.
    assert f"?card={cl_id}&amp;n={noti.id}" in mail[0].html_body
    assert mail[0].headers["List-Unsubscribe"].startswith("<")
    assert mail[0].headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    # Everything about one card threads together in a mail client.
    assert str(cl_id) in mail[0].headers["References"]


def test_an_invitation_and_an_opened_public_link_get_their_own_wording(
    mail_capture, user_factory
):
    from checkcheckserver.model.notification import NotificationType

    invitee = user_factory("invited")
    owner = user_factory("linkowner")

    async def body(session):
        await _emit(
            session,
            user_id=invitee.id,
            type=NotificationType.card_invited,
            payload=_share_payload(card="Sprint planning"),
        )
        await _emit(
            session,
            user_id=owner.id,
            type=NotificationType.public_link_opened,
            payload={"checklist_name": "Public menu"},
        )
        await _drain(session)

    _run(body)

    invitation = _mail_to(mail_capture, invitee.email)
    opened = _mail_to(mail_capture, owner.email)
    assert len(invitation) == 1
    assert "invited you to" in invitation[0].subject
    assert "Sprint planning" in invitation[0].subject
    assert len(opened) == 1
    assert opened[0].subject == 'Your public link to "Public menu" was opened'
    # An anonymous visitor has no name, and the message must not invent one.
    assert "Someone" not in opened[0].text_body


def test_sharing_a_card_over_http_really_queues_the_mail(mail_capture, user_factory):
    """The one test that goes through the actual call site.

    Everything else here calls ``emit_notification`` directly, which is the right
    seam for the decisions but proves nothing about the route that reaches it.
    This one shares a card the way the client does and then drains the queue in
    this process. The test server runs with ``NOTIFY_DISPATCH_IN_PROCESS=False``,
    so the row is still there to be delivered here.
    """
    from checkcheckserver.model.notification_outbox import NotificationOutboxStatus

    user = user_factory("httpshare")
    card = req(
        "api/checklist",
        "post",
        b={"name": "E4 shared over HTTP", "color_id": "yellow"},
    )
    req(
        f"api/checklist/{card['id']}/shares/{user.id}",
        "put",
        b={"permission": "edit"},
    )

    async def body(session):
        await _drain(session)
        return await _rows(session, user.id)

    rows = _run(body)
    mail = _mail_to(mail_capture, user.email)

    assert len(rows) == 1 and rows[0].status == NotificationOutboxStatus.sent.value
    assert len(mail) == 1
    assert "E4 shared over HTTP" in mail[0].subject
    # The actor is whoever shared it, which is the admin the suite logs in as.
    assert f"?card={card['id']}" in mail[0].text_body


# ── when nothing should be sent ───────────────────────────────────────────────


def test_no_mail_when_the_user_switched_that_type_off(mail_capture, user_factory):
    from checkcheckserver.model.notification import NotificationType

    user = user_factory("optedout")

    async def body(session):
        await _set_prefs(session, user.id, {"card_shared": {"email": "off"}})
        noti = await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            payload=_share_payload(),
        )
        await _drain(session)
        return noti, await _rows(session, user.id)

    noti, rows = _run(body)

    # The bell still rings; only the mail is gone, and it was never queued.
    assert noti is not None
    assert rows == []
    assert _mail_to(mail_capture, user.email) == []


def test_the_in_app_channel_can_be_muted_without_losing_the_mail(
    mail_capture, user_factory
):
    """The open question this chunk had to answer.

    The preference matrix offers ``in_app: off``, and the only honest way to
    honour it is to not write the notification row: a row that exists shows up in
    the bell, the badge and the delta feed however it is flagged. The email
    channel is independent, so a user who wants mail and no bell gets exactly
    that.
    """
    from checkcheckserver.model.notification import NotificationType

    user = user_factory("nobell")

    async def body(session):
        await _set_prefs(session, user.id, {"card_shared": {"in_app": "off"}})
        noti = await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            payload=_share_payload(),
        )
        await _drain(session)
        return noti, await _feed(session, user.id), await _rows(session, user.id)

    noti, feed, rows = _run(body)

    assert noti is None
    assert feed == []
    # The mail still went out, and has no notification to be cancelled by.
    assert len(rows) == 1 and rows[0].notification_id is None
    assert len(_mail_to(mail_capture, user.email)) == 1


def test_a_user_without_an_address_produces_no_row_at_all(mail_capture):
    """Section 4.1.4: an ordinary outcome, not a queued row that fails later.

    OIDC accounts can arrive with no email claim at all, so this is the normal
    state of a real user, not an edge case.
    """
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.user import User

    async def body(session):
        user = User(user_name=f"e4noaddress{uuid.uuid4().hex[:8]}", email=None)
        session.add(user)
        await session.commit()
        noti = await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            payload=_share_payload(),
        )
        return noti, await _rows(session, user.id)

    noti, rows = _run(body)

    assert noti is not None
    assert rows == []


def test_an_unverified_address_is_skipped_when_the_instance_demands_verification(
    user_factory,
):
    """``NOTIFY_EMAIL_REQUIRE_VERIFIED`` (4.1.5). Nothing flips
    ``is_email_verified`` anywhere in the codebase yet, so on this instance the
    switch stops all mail, which is exactly what its description says."""
    from checkcheckserver.model.notification import NotificationType

    user = user_factory("unverified")

    async def body(session):
        await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_invited,
            payload=_share_payload(),
            config=_config(NOTIFY_EMAIL_REQUIRE_VERIFIED=True),
        )
        return await _rows(session, user.id)

    assert _run(body) == []


def test_a_type_the_administrator_disabled_reaches_nobody(user_factory):
    """The hard cap beats everything, on every channel at once: no feed entry and
    no mail, whatever the user asked for."""
    from checkcheckserver.model.notification import NotificationType

    user = user_factory("capped")

    async def body(session):
        await _set_prefs(session, user.id, {"public_link_opened": {"email": "immediate"}})
        noti = await _emit(
            session,
            user_id=user.id,
            type=NotificationType.public_link_opened,
            payload={"checklist_name": "Capped card"},
            config=_config(NOTIFY_DISABLED_TYPES=["public_link_opened"]),
        )
        return noti, await _rows(session, user.id)

    noti, rows = _run(body)

    assert noti is None
    assert rows == []


def test_reading_the_notification_inside_the_window_cancels_the_mail(
    mail_capture, user_factory
):
    """Suppression rule 4.1.1 through the real fan-out: somebody looking at the
    app right now does not need mail about what they are looking at."""
    from checkcheckserver.model._base_model import naive_utc_now
    from checkcheckserver.model.notification_outbox import NotificationOutboxStatus
    from checkcheckserver.model.notification import NotificationType

    user = user_factory("readfirst")

    async def body(session):
        from checkcheckserver.db.notification import NotificationCRUD

        noti = await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            payload=_share_payload(),
        )
        queued = await _rows(session, user.id)
        # Still queued, not sent: the message waits out the suppression window.
        assert queued[0].not_before > naive_utc_now()

        await NotificationCRUD(session).mark_read(noti.id, user.id)
        await _drain(session)
        return await _rows(session, user.id)

    rows = _run(body)

    assert len(rows) == 1
    assert rows[0].status == NotificationOutboxStatus.cancelled.value
    assert _mail_to(mail_capture, user.email) == []


# ── coalescing ────────────────────────────────────────────────────────────────


def test_a_group_share_gives_every_recipient_one_mail_and_only_their_own(
    mail_capture, user_factory
):
    """N users, N mails, not N squared, and nobody sees anybody else's."""
    from checkcheckserver.model.notification import NotificationType

    users = [user_factory(f"group{index}") for index in range(3)]
    cl_id = uuid.uuid4()
    payload = _share_payload(card="Team offsite")

    async def body(session):
        for user in users:
            await _emit(
                session,
                user_id=user.id,
                type=NotificationType.card_shared,
                cl_id=cl_id,
                payload=payload,
            )
        await _drain(session)

    _run(body)

    for user in users:
        mail = _mail_to(mail_capture, user.email)
        assert len(mail) == 1, f"{user.email} got {len(mail)} messages"
        assert "Team offsite" in mail[0].subject


def test_five_shares_from_one_actor_arrive_as_one_message(mail_capture, user_factory):
    """Coalescing rule 4.1.2: the dedupe key is recipient, type and actor, so one
    person sharing a stack of cards is one mail, not a stack of mails."""
    user = user_factory("coalesced")
    actor_id = uuid.uuid4()

    async def body(session):
        from checkcheckserver.model.notification import NotificationType

        for index in range(5):
            await _emit(
                session,
                user_id=user.id,
                type=NotificationType.card_shared,
                payload=_share_payload(actor_id=actor_id, card=f"Card {index}"),
            )
        keys = {row.dedupe_key for row in await _rows(session, user.id)}
        result = await _drain(session)
        return keys, result, await _rows(session, user.id)

    keys, result, rows = _run(body)
    mail = _mail_to(mail_capture, user.email)

    assert len(keys) == 1, keys
    assert len(rows) == 5 and all(row.status == "sent" for row in rows)
    assert len(mail) == 1
    assert result.messages >= 1
    assert mail[0].subject == "Anna Analyst shared 5 cards with you"
    # Every card is still individually reachable from the one message.
    for index in range(5):
        assert f"Card {index}" in mail[0].text_body


def test_two_different_actors_stay_two_messages(mail_capture, user_factory):
    """The other half of the same rule: coalescing must not merge unrelated
    events just because they landed at the same time."""
    from checkcheckserver.model.notification import NotificationType

    user = user_factory("twoactors")

    async def body(session):
        await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            payload=_share_payload(actor="Anna Analyst", card="Anna's card"),
        )
        await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            payload=_share_payload(actor="Bob Builder", card="Bob's card"),
        )
        await _drain(session)

    _run(body)
    mail = _mail_to(mail_capture, user.email)

    assert len(mail) == 2
    assert {email.subject for email in mail} == {
        'Anna Analyst shared "Anna\'s card" with you',
        'Bob Builder shared "Bob\'s card" with you',
    }


def test_a_read_notification_drops_out_of_a_group_without_taking_it_down(
    mail_capture, user_factory
):
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationOutboxStatus

    user = user_factory("partialread")
    actor_id = uuid.uuid4()

    async def body(session):
        from checkcheckserver.db.notification import NotificationCRUD

        first = await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            payload=_share_payload(actor_id=actor_id, card="Read one"),
        )
        await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            payload=_share_payload(actor_id=actor_id, card="Unread one"),
        )
        await NotificationCRUD(session).mark_read(first.id, user.id)
        await _drain(session)
        return await _rows(session, user.id)

    rows = _run(body)
    mail = _mail_to(mail_capture, user.email)

    statuses = sorted(row.status for row in rows)
    assert statuses == [
        NotificationOutboxStatus.cancelled.value,
        NotificationOutboxStatus.sent.value,
    ]
    assert len(mail) == 1
    assert "Unread one" in mail[0].text_body
    assert "Read one" not in mail[0].text_body


def test_a_stand_alone_message_is_never_swallowed_by_a_group(mail_capture):
    """The test mail carries a dedupe key too (its rate limit lives on it), but no
    render context, so it can never be folded into somebody's digest."""
    from checkcheckserver.notify.outbox import _is_coalescable
    from checkcheckserver.model.notification_outbox import NotificationOutbox

    plain = NotificationOutbox(
        user_id=uuid.uuid4(),
        channel="email",
        payload={"to": "x@example.com", "subject": "s", "text_body": "b"},
        dedupe_key="somebody:test_email",
    )
    assert _is_coalescable(plain) is False


# ── content mode ──────────────────────────────────────────────────────────────


def test_minimal_content_mode_leaks_neither_the_card_name_nor_the_actor(
    mail_capture, user_factory
):
    from checkcheckserver.model.notification import NotificationType

    user = user_factory("minimal")

    async def body(session):
        await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            payload=_share_payload(actor="Secret Person", card="Secret Card"),
            config=_config(NOTIFY_EMAIL_CONTENT_MODE="minimal"),
        )
        await _drain(session)

    _run(body)
    mail = _mail_to(mail_capture, user.email)

    assert len(mail) == 1
    whole_message = " ".join(
        [mail[0].subject, mail[0].text_body, mail[0].html_body or ""]
    )
    assert "Secret Person" not in whole_message
    assert "Secret Card" not in whole_message
    # It still has to be actionable, so the link stays.
    assert "?card=" in mail[0].text_body
    assert mail[0].subject == "A card was shared with you"


# ── the hourly cap ────────────────────────────────────────────────────────────


def test_the_per_user_hourly_cap_drops_the_overflow_instead_of_queueing_it(
    user_factory,
):
    """``NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR``: a blunt backstop, applied at
    enqueue time. Queueing the overflow would only deliver the flood later."""
    from checkcheckserver.model.notification import NotificationType

    user = user_factory("capacity")

    async def body(session):
        config = _config(NOTIFY_EMAIL_MAX_PER_USER_PER_HOUR=2)
        for index in range(4):
            await _emit(
                session,
                user_id=user.id,
                # A different actor each time, so nothing coalesces and every
                # message really is its own row.
                type=NotificationType.card_shared,
                payload=_share_payload(card=f"Flood {index}"),
                config=config,
            )
        return await _rows(session, user.id), await _feed(session, user.id)

    rows, feed = _run(body)

    assert len(rows) == 2
    # The in-app feed is not rate limited: the cap protects an inbox, not the app.
    assert len(feed) == 4


# ── digests ───────────────────────────────────────────────────────────────────


def test_an_hourly_digest_collects_its_window_into_one_message(
    mail_capture, user_factory
):
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.notify.prefs import NotificationMode
    from checkcheckserver.notify.schedule import digest_due_at

    user = user_factory("hourly")

    async def body(session):
        from checkcheckserver.model._base_model import naive_utc_now

        await _set_prefs(session, user.id, {"card_shared": {"email": "hourly"}})
        await _set_prefs(session, user.id, {"card_invited": {"email": "hourly"}})
        await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            payload=_share_payload(card="Digest card"),
        )
        await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_invited,
            payload=_share_payload(actor="Bob Builder", card="Digest invite"),
        )
        rows = await _rows(session, user.id)
        # Snapshotted before draining: claiming a row rewrites `not_before` on
        # the very instance loaded here (same identity map).
        scheduled = (
            {row.dedupe_key for row in rows},
            {row.not_before for row in rows},
            {row.id for row in rows},
        )
        due = digest_due_at(NotificationMode.hourly, now=naive_utc_now())
        # Not due before the window closes, however long the suppression window is.
        early = await _drain(session, minutes_ahead=0)
        late = await _drain(
            session,
            minutes_ahead=(due - naive_utc_now()).total_seconds() / 60 + 1,
        )
        return scheduled, due, early, late

    (keys, due_times, ids), due, early, _late = _run(body)
    mail = _mail_to(mail_capture, user.email)

    # One window, one key, one due time, whatever the notification type: a digest
    # is one message about everything that happened.
    assert len(keys) == 1
    assert due_times == {due}
    # Nothing goes out while the window is still open.
    assert ids.isdisjoint(early.ids_sent)
    assert len(mail) == 1
    assert mail[0].subject == "Your hourly summary: 2 notifications"
    assert "Digest card" in mail[0].text_body
    assert "Digest invite" in mail[0].text_body


def test_an_empty_digest_window_sends_nothing(mail_capture, user_factory):
    """Nothing assembles a digest on a timer, so a quiet window costs nothing and
    produces nothing: there are simply no rows for it."""
    from checkcheckserver.notify.prefs import NotificationMode
    from checkcheckserver.notify.schedule import digest_due_at

    user = user_factory("quiet")

    async def body(session):
        from checkcheckserver.model._base_model import naive_utc_now

        await _set_prefs(session, user.id, {"card_shared": {"email": "daily"}})
        due = digest_due_at(NotificationMode.daily, now=naive_utc_now())
        await _drain(
            session, minutes_ahead=(due - naive_utc_now()).total_seconds() / 60 + 1
        )
        return await _rows(session, user.id)

    assert _run(body) == []
    assert _mail_to(mail_capture, user.email) == []


def test_a_daily_digest_is_due_at_the_local_morning_of_the_users_time_zone(
    user_factory,
):
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.notify.schedule import DAILY_DIGEST_HOUR

    user = user_factory("timezone")
    zone = "Pacific/Kiritimati"  # UTC+14, so nothing can accidentally match UTC

    async def body(session):
        import zoneinfo

        settings = await _set_prefs(session, user.id, {"card_shared": {"email": "daily"}})
        settings.timezone = zone
        session.add(settings)
        await session.commit()

        await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            payload=_share_payload(),
        )
        rows = await _rows(session, user.id)
        local = (
            rows[0]
            .not_before.replace(tzinfo=datetime.timezone.utc)
            .astimezone(zoneinfo.ZoneInfo(zone))
        )
        return local

    local = _run(body)

    assert (local.hour, local.minute) == (DAILY_DIGEST_HOUR, 0)


def test_hourly_and_daily_windows_are_computed_where_they_are_documented():
    """The scheduling rules on their own, without a database in the way."""
    from checkcheckserver.notify.prefs import NotificationMode
    from checkcheckserver.notify.schedule import digest_due_at

    now = datetime.datetime(2026, 8, 2, 10, 42, 13)

    assert digest_due_at(NotificationMode.hourly, now=now) == datetime.datetime(
        2026, 8, 2, 11, 0
    )
    # 08:00 in Berlin is 06:00 UTC, and 10:42 UTC is past it, so it is tomorrow's.
    assert digest_due_at(
        NotificationMode.daily, now=now, timezone_name="Europe/Berlin"
    ) == datetime.datetime(2026, 8, 3, 6, 0)
    # Before the boundary it is today's.
    assert digest_due_at(
        NotificationMode.daily,
        now=datetime.datetime(2026, 8, 2, 3, 0),
        timezone_name="Europe/Berlin",
    ) == datetime.datetime(2026, 8, 2, 6, 0)
    # An unusable stored zone must not break a notification.
    assert digest_due_at(
        NotificationMode.daily, now=now, timezone_name="Mars/Olympus_Mons"
    ) == datetime.datetime(2026, 8, 3, 8, 0)


# ── unsubscribe ───────────────────────────────────────────────────────────────


def _token_from(email) -> str:
    url = email.headers["List-Unsubscribe"].strip("<>")
    return parse_qs(urlparse(url).query)["token"][0]


def _unsubscribe(method: str, token: str):
    return getattr(requests, method)(
        f"{get_server_base_url()}/api/notifications/unsubscribe",
        params={"token": token},
    )


def test_the_unsubscribe_link_switches_exactly_one_type_off(
    mail_capture, user_factory
):
    """End to end, the way a recipient uses it: take the link out of the message
    that arrived, open it, press the button."""
    from checkcheckserver.model.notification import NotificationType

    user = user_factory("unsub")

    async def body(session):
        await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            payload=_share_payload(),
        )
        await _drain(session)

    _run(body)
    mail = _mail_to(mail_capture, user.email)
    assert len(mail) == 1
    token = _token_from(mail[0])

    access_token = authorize_for_access_token(user.name, user.password)

    def channel(type: str, name: str = "email"):
        settings = req("api/user/me/notification-settings", access_token=access_token)
        entry = [row for row in settings["types"] if row["type"] == type][0]
        return entry["channels"][name]

    assert channel("card_shared")["user_choice"] is None

    # A mail scanner pre-fetching the link must not unsubscribe anybody, so the
    # GET only shows the confirmation.
    shown = _unsubscribe("get", token)
    assert shown.status_code == 200
    assert "cards being shared with you" in shown.text
    assert channel("card_shared")["user_choice"] is None

    applied = _unsubscribe("post", token)
    assert applied.status_code == 200
    assert channel("card_shared")["user_choice"] == "off"
    assert channel("card_shared")["mode"] == "off"
    # Exactly one type and exactly one channel: nothing else moved.
    assert channel("card_shared", "in_app")["user_choice"] is None
    assert channel("card_invited")["user_choice"] is None
    assert channel("card_invited")["mode"] != "off"


def test_the_confirmation_form_carries_the_token_back_unchanged(user_factory):
    """Finding 11 (chunk N1): the token reaches a URL context in the form action.

    It is percent-encoded there the way ``unsubscribe_url()`` already encodes the
    same value, so the link in the message and the button on the page cannot
    disagree about what the token is, and what comes back out of the page is
    byte for byte what went in.
    """
    import html
    import re
    from urllib.parse import quote

    from checkcheckserver.notify import unsubscribe as unsub

    user = user_factory("formaction")

    async def body(session):
        from checkcheckserver.db.user_notification_settings import get_or_create_settings

        settings = await get_or_create_settings(session, user.id)
        return settings.unsubscribe_secret

    secret = _run(body)
    token = unsub.mint_token(user_id=user.id, type="card_shared", secret=secret)

    shown = _unsubscribe("get", token)
    assert shown.status_code == 200
    action = html.unescape(re.search(r'action="([^"]*)"', shown.text).group(1))
    # The same encoding `unsubscribe_url()` applies to the same value, so the
    # form action is the message's link minus the origin.
    assert action == f"{unsub.UNSUBSCRIBE_PATH}?token={quote(token)}"

    carried = parse_qs(urlparse(action).query)["token"][0]
    assert carried == token
    assert _unsubscribe("post", carried).status_code == 200


def test_forged_tampered_and_expired_tokens_get_the_same_neutral_page(user_factory):
    from checkcheckserver.notify import unsubscribe as unsub

    user = user_factory("forged")

    async def body(session):
        from checkcheckserver.db.user_notification_settings import get_or_create_settings

        settings = await get_or_create_settings(session, user.id)
        return settings.unsubscribe_secret

    secret = _run(body)
    valid = unsub.mint_token(user_id=user.id, type="card_shared", secret=secret)
    payload, _, signature = valid.partition(".")

    cases = {
        "garbage": "not-a-token",
        "unsigned": payload,
        "wrong signature": f"{payload}.{signature[:-4]}AAAA",
        # Same signature, different claims: the signature covers the payload
        # string verbatim, so re-encoding it cannot help.
        "tampered claims": (
            unsub.mint_token(
                user_id=user.id, type="card_invited", secret="somebody else's secret"
            ).partition(".")[0]
            + f".{signature}"
        ),
        "expired": unsub.mint_token(
            user_id=user.id,
            type="card_shared",
            secret=secret,
            now=datetime.datetime(2020, 1, 1),
        ),
        "nobody": unsub.mint_token(
            user_id=uuid.uuid4(), type="card_shared", secret=secret
        ),
    }

    for name, token in cases.items():
        for method in ("get", "post"):
            response = _unsubscribe(method, token)
            assert response.status_code == 400, f"{name} via {method}"
            assert "not valid" in response.text, f"{name} via {method}"
            # Never a hint about whether that account exists.
            assert str(user.id) not in response.text
            assert user.email not in response.text

    # And the real one still works after all that.
    assert _unsubscribe("post", valid).status_code == 200


def test_unsubscribing_beats_a_default_the_administrator_set_on(
    mail_capture, user_factory
):
    """The link has to work whatever the instance configuration is, which is why
    it writes the preference directly instead of going through the validator that
    the settings dialog uses."""
    from checkcheckserver.model.notification import NotificationType

    user = user_factory("unsublocked")

    async def body(session):
        await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_invited,
            payload=_share_payload(),
        )
        await _drain(session)

    _run(body)
    mail = _mail_to(mail_capture, user.email)
    assert len(mail) == 1

    assert _unsubscribe("post", _token_from(mail[0])).status_code == 200

    async def after(session):
        from checkcheckserver.db.user_notification_settings import get_settings
        from checkcheckserver.notify.prefs import (
            NotificationMode,
            PreferenceChannel,
            resolve_mode,
        )

        settings = await get_settings(session, user.id)
        return resolve_mode(
            settings,
            "card_invited",
            PreferenceChannel.email,
            config=_config(
                NOTIFY_DEFAULT_MODES={"card_invited": {"email": "immediate"}}
            ),
        ) == NotificationMode.off

    assert _run(after) is True
