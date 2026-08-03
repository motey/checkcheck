"""Tests for the push channel (chunk P1 of the system-notifications plan).

Same harness and reasoning as ``tests_notification_webhooks.py``: the live test
server is booted with ``NOTIFY_PUSH_ENABLED`` off (no VAPID keys configured), so
the fan-out, the queue and the sender are exercised by calling them in this
process with a config that has push switched on, and the two disabled-instance
HTTP checks run straight against the live server without needing to touch its
config.

The plan is ``docs/plans/SYSTEM_NOTIFICATIONS.md``, section 8 chunk P1.
"""

import asyncio
import datetime
import uuid
from types import SimpleNamespace

import pytest

from utils import create_test_user, req


# A throwaway VAPID key pair, valid in shape (py_vapid loads it fine) but not
# tied to any real push service. Fine for tests: nothing here talks to a real
# push endpoint, everything goes through CapturingPushSender.
_TEST_VAPID_PUBLIC_KEY = (
    "BH-DWhYfjSH5OVS2sjII4dGEP46ueAfPWQklJ_zITJqoWtfKgjHBTDxE_X5jdPms-zR3R9b43oCqYFnxwmwk_PY"
)
_TEST_VAPID_PRIVATE_KEY = "Mp6hqDn1uEMxJwMvqchFdkrCiID8zYUIvTwDI-rmLSA"


# ── helpers ───────────────────────────────────────────────────────────────────


def _run(body):
    """Run *body* with a fresh session against the test database."""
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
    """An instance with the push channel switched on, plus overrides."""
    from checkcheckserver.config import Config

    settings = {
        "EMAIL_ENABLED": True,
        "EMAIL_TRANSPORT": "null",
        "EMAIL_FROM_ADDRESS": "push-tests@example.test",
        "NOTIFY_PUSH_ENABLED": True,
        "VAPID_PUBLIC_KEY": _TEST_VAPID_PUBLIC_KEY,
        "VAPID_PRIVATE_KEY": _TEST_VAPID_PRIVATE_KEY,
        "VAPID_CONTACT_EMAIL": "admin@example.test",
        # The push channel is what these tests look at; mail for the same
        # notification would only add rows to filter out.
        "NOTIFY_DEFAULT_MODES": {
            "card_shared": {"in_app": "immediate", "email": "off", "push": "immediate"},
            "card_invited": {"in_app": "immediate", "email": "off", "push": "immediate"},
            "public_link_opened": {"in_app": "immediate", "email": "off", "push": "off"},
        },
    }
    settings.update(overrides)
    return Config(**settings)


async def _emit(session, *, user_id, type, cl_id=None, payload=None, config=None):
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


async def _rows(session, user_id, channel=None):
    from sqlmodel import select

    from checkcheckserver.model.notification_outbox import NotificationOutbox

    query = (
        select(NotificationOutbox)
        .where(NotificationOutbox.user_id == user_id)
        .order_by(NotificationOutbox.created_at)
        .execution_options(populate_existing=True)
    )
    if channel is not None:
        query = query.where(NotificationOutbox.channel == channel)
    return list((await session.exec(query)).all())


async def _snapshot(session, user_id):
    """One user's push rows as plain dicts, detached from the session."""
    from checkcheckserver.model.notification_outbox import NotificationChannel

    return [
        {"status": row.status, "attempts": row.attempts, "last_error": row.last_error}
        for row in await _rows(session, user_id, NotificationChannel.push.value)
    ]


async def _subscribe(session, user_id, *, endpoint, p256dh="p256dh", auth="auth"):
    from checkcheckserver.db import push_subscription

    return await push_subscription.upsert(
        session,
        user_id=user_id,
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        user_agent="pytest",
    )


async def _drain(session, *, config=None, sender=None, minutes_ahead=5):
    """Deliver everything due *minutes_ahead* from now.

    Looking at the queue from the future rather than sleeping through a
    backoff, the same trick the other outbox tests use.
    """
    from checkcheckserver.model._base_model import naive_utc_now
    from checkcheckserver.notify.outbox import drain_once

    return await drain_once(
        session,
        now=naive_utc_now() + datetime.timedelta(minutes=minutes_ahead),
        config=config or _config(),
        push_sender=sender,
    )


@pytest.fixture(scope="module")
def user_factory():
    """Accounts owned by exactly one test each, so rows can be told apart."""
    made = {}

    def make(slug: str):
        if slug not in made:
            name = f"p1{slug}user"
            created = create_test_user(name, f"{name}_pw_secure1", f"{name}@test.de")
            made[slug] = SimpleNamespace(id=uuid.UUID(created["id"]), name=name)
        return made[slug]

    return make


@pytest.fixture
def push_capture():
    """A capturing push sender, installed for the duration of one test."""
    from checkcheckserver.notify.push import CapturingPushSender, reset_push_sender, set_push_sender

    sender = CapturingPushSender()
    set_push_sender(sender)
    try:
        yield sender
    finally:
        reset_push_sender()


# ── the fan-out ───────────────────────────────────────────────────────────────


def test_a_notification_reaches_every_subscribed_device(push_capture, user_factory):
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutboxStatus,
    )

    user = user_factory("multi")
    cl_id = uuid.uuid4()

    async def body(session):
        await _subscribe(session, user.id, endpoint="https://push.example/device-a")
        await _subscribe(session, user.id, endpoint="https://push.example/device-b")
        await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            cl_id=cl_id,
            payload={
                "actor_id": str(uuid.uuid4()),
                "actor_display_name": "Anna Analyst",
                "checklist_name": "Weekend groceries",
            },
            config=_config(NOTIFY_PUSH_CONTENT_MODE="full"),
        )
        await _drain(session, config=_config(NOTIFY_PUSH_CONTENT_MODE="full"))
        return await _rows(session, user.id, NotificationChannel.push.value)

    rows = _run(body)

    assert len(rows) == 1
    assert rows[0].status == NotificationOutboxStatus.sent.value
    assert len(push_capture.sent) == 2
    endpoints = {target.endpoint for _push, target in push_capture.sent}
    assert endpoints == {
        "https://push.example/device-a",
        "https://push.example/device-b",
    }
    push, _target = push_capture.sent[0]
    assert "Anna Analyst" in push.title
    assert "Weekend groceries" in push.title
    assert f"?card={cl_id}" in push.url
    assert push.tag


def test_no_subscription_at_enqueue_time_queues_nothing(push_capture, user_factory):
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationChannel

    user = user_factory("none")

    async def body(session):
        await _emit(session, user_id=user.id, type=NotificationType.card_shared)
        await _drain(session)
        return await _rows(session, user.id, NotificationChannel.push.value)

    assert _run(body) == []
    assert push_capture.sent == []


def test_a_user_who_switched_the_channel_off_gets_nothing(push_capture, user_factory):
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationChannel
    from checkcheckserver.db.user_notification_settings import get_or_create_settings
    from checkcheckserver.notify.prefs import apply_prefs_patch

    user = user_factory("muted")

    async def body(session):
        await _subscribe(session, user.id, endpoint="https://push.example/muted-device")
        settings = await get_or_create_settings(session, user.id)
        settings.prefs = apply_prefs_patch(
            settings.prefs, {"card_shared": {"push": "off"}}
        )
        session.add(settings)
        await session.commit()
        await _emit(session, user_id=user.id, type=NotificationType.card_shared)
        await _drain(session)
        return await _rows(session, user.id, NotificationChannel.push.value)

    assert _run(body) == []
    assert push_capture.sent == []


def test_the_master_switch_being_off_queues_nothing(push_capture, user_factory):
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationChannel

    user = user_factory("instanceoff")

    async def body(session):
        await _subscribe(session, user.id, endpoint="https://push.example/instanceoff")
        await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            config=_config(NOTIFY_PUSH_ENABLED=False),
        )
        await _drain(session)
        return await _rows(session, user.id, NotificationChannel.push.value)

    assert _run(body) == []
    assert push_capture.sent == []


def test_zero_subscriptions_at_delivery_time_cancels_the_row(push_capture, user_factory):
    """Discovered late rather than at enqueue: a device that unsubscribes
    between queueing and delivery leaves the row with nowhere to go, which is
    not an error (plan section 4)."""
    from checkcheckserver.db import push_subscription
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationOutboxStatus

    user = user_factory("vanished")

    async def body(session):
        sub = await _subscribe(session, user.id, endpoint="https://push.example/vanished")
        await _emit(session, user_id=user.id, type=NotificationType.card_shared)
        await push_subscription.delete_for_user(
            session, subscription_id=sub.id, user_id=user.id
        )
        await _drain(session)
        return await _snapshot(session, user.id)

    rows = _run(body)
    assert len(rows) == 1
    assert rows[0]["status"] == NotificationOutboxStatus.cancelled.value
    assert rows[0]["last_error"] is None
    assert push_capture.sent == []


def test_a_mixed_outcome_ends_sent_and_removes_only_the_dead_subscription(
    push_capture, user_factory
):
    from checkcheckserver.db import push_subscription
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationOutboxStatus
    from checkcheckserver.notify.push import PermanentPushError

    user = user_factory("mixed")

    async def body(session):
        dead = await _subscribe(session, user.id, endpoint="https://push.example/dead")
        alive = await _subscribe(session, user.id, endpoint="https://push.example/alive")
        await _emit(session, user_id=user.id, type=NotificationType.card_shared)

        push_capture.raise_for[dead.id] = PermanentPushError("410 Gone")
        await _drain(session)

        remaining = await push_subscription.list_for_user(session, user.id)
        return await _snapshot(session, user.id), {s.endpoint for s in remaining}

    rows, remaining_endpoints = _run(body)
    assert len(rows) == 1
    assert rows[0]["status"] == NotificationOutboxStatus.sent.value
    assert remaining_endpoints == {"https://push.example/alive"}


def test_every_subscription_permanently_gone_fails_the_row(push_capture, user_factory):
    from checkcheckserver.db import push_subscription
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationOutboxStatus
    from checkcheckserver.notify.push import PermanentPushError

    user = user_factory("allgone")

    async def body(session):
        a = await _subscribe(session, user.id, endpoint="https://push.example/a-gone")
        b = await _subscribe(session, user.id, endpoint="https://push.example/b-gone")
        await _emit(session, user_id=user.id, type=NotificationType.card_shared)

        push_capture.raise_for[a.id] = PermanentPushError("410 Gone")
        push_capture.raise_for[b.id] = PermanentPushError("404 Not Found")
        await _drain(session)

        remaining = await push_subscription.list_for_user(session, user.id)
        return await _snapshot(session, user.id), remaining

    rows, remaining = _run(body)
    assert len(rows) == 1
    assert rows[0]["status"] == NotificationOutboxStatus.failed.value
    assert rows[0]["last_error"]
    assert remaining == []


def test_a_transient_failure_retries_with_backoff(user_factory):
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationOutboxStatus
    from checkcheckserver.notify.push import CapturingPushSender, TransientPushError

    user = user_factory("flaky")

    async def body(session):
        await _subscribe(session, user.id, endpoint="https://push.example/flaky")
        await _emit(session, user_id=user.id, type=NotificationType.card_shared)

        sender = CapturingPushSender()
        sender.raise_on_send = TransientPushError("503 from the push service")
        await _drain(session, sender=sender)
        return await _snapshot(session, user.id)

    rows = _run(body)
    assert len(rows) == 1
    assert rows[0]["status"] == NotificationOutboxStatus.pending.value
    assert rows[0]["attempts"] == 1
    assert "503" in rows[0]["last_error"]


def test_minimal_content_mode_is_independent_of_the_email_setting(
    push_capture, user_factory
):
    """Decision 3 of the plan: NOTIFY_PUSH_CONTENT_MODE is its own switch, not
    inherited from NOTIFY_EMAIL_CONTENT_MODE."""
    from checkcheckserver.model.notification import NotificationType

    user = user_factory("contentmode")

    async def body(session):
        await _subscribe(session, user.id, endpoint="https://push.example/contentmode")
        # Email is set to `full`; push stays at its own default (`minimal`).
        await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            payload={
                "actor_id": str(uuid.uuid4()),
                "actor_display_name": "Secret Sender",
                "checklist_name": "Confidential Card",
            },
            config=_config(NOTIFY_EMAIL_CONTENT_MODE="full"),
        )
        await _drain(session, config=_config(NOTIFY_EMAIL_CONTENT_MODE="full"))

    _run(body)
    assert len(push_capture.sent) == 1
    push, _target = push_capture.sent[0]
    assert "Secret Sender" not in push.title
    assert "Confidential Card" not in push.title
    assert "Secret Sender" not in push.body
    assert "Confidential Card" not in push.body


def test_the_hourly_cap_drops_the_overflow_instead_of_queueing_it(user_factory):
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationChannel

    user = user_factory("capped")
    cfg = _config(NOTIFY_PUSH_MAX_PER_USER_PER_HOUR=2)

    async def body(session):
        await _subscribe(session, user.id, endpoint="https://push.example/capped")
        for _ in range(3):
            await _emit(
                session, user_id=user.id, type=NotificationType.card_shared, config=cfg
            )
        return await _rows(session, user.id, NotificationChannel.push.value)

    rows = _run(body)
    assert len(rows) == 2


# ── the subscription store ──────────────────────────────────────────────────


def test_subscribing_twice_upserts_instead_of_duplicating(user_factory):
    from checkcheckserver.db import push_subscription

    user = user_factory("resubscribe")

    async def body(session):
        first = await _subscribe(
            session, user.id, endpoint="https://push.example/resub", auth="auth-1"
        )
        second = await _subscribe(
            session, user.id, endpoint="https://push.example/resub", auth="auth-2"
        )
        rows = await push_subscription.list_for_user(session, user.id)
        return first.id, second.id, [r.auth for r in rows]

    first_id, second_id, auths = _run(body)
    assert first_id == second_id
    assert auths == ["auth-2"]


def test_delete_only_removes_the_owners_own_subscription(user_factory):
    from checkcheckserver.db import push_subscription

    owner = user_factory("owner")
    other = user_factory("other")

    async def body(session):
        sub = await _subscribe(session, owner.id, endpoint="https://push.example/owned")
        stolen = await push_subscription.delete_for_user(
            session, subscription_id=sub.id, user_id=other.id
        )
        remaining = await push_subscription.list_for_user(session, owner.id)
        removed = await push_subscription.delete_for_user(
            session, subscription_id=sub.id, user_id=owner.id
        )
        return stolen, len(remaining), removed

    stolen, remaining_count, removed = _run(body)
    assert stolen is False
    assert remaining_count == 1
    assert removed is True


# ── the transport's status-code classification ──────────────────────────────


def test_send_sync_classifies_push_service_status_codes(monkeypatch):
    """``_send_sync`` never touches the network here: ``pywebpush.webpush`` is
    monkeypatched to raise with a canned status code, so this exercises only
    this module's transient/permanent classification (the wire protocol
    itself is out of scope for this suite, see the plan's P2 handoff note)."""
    import pywebpush

    from checkcheckserver.notify import push as push_module

    def make_fake_webpush(status_code):
        class FakeResponse:
            pass

        FakeResponse.status_code = status_code
        FakeResponse.text = "detail from the push service"

        def fake_webpush(**kwargs):
            raise pywebpush.WebPushException("boom", response=FakeResponse())

        return fake_webpush

    target = push_module.PushSubscriptionTarget(
        id=uuid.uuid4(), endpoint="https://push.example/classify", p256dh="p", auth="a"
    )
    push = push_module.OutgoingPush(title="t", body="b", url="https://x/", tag="tag")
    config = _config()

    cases = [
        (404, push_module.PermanentPushError),
        (410, push_module.PermanentPushError),
        (400, push_module.PermanentPushError),
        (429, push_module.TransientPushError),
        (500, push_module.TransientPushError),
        (503, push_module.TransientPushError),
    ]
    for status_code, expected in cases:
        monkeypatch.setattr(pywebpush, "webpush", make_fake_webpush(status_code))
        with pytest.raises(expected):
            push_module._send_sync(push, target, config)


# ── VAPID key validation (config boot check) ────────────────────────────────


def test_boot_fails_loudly_when_push_is_enabled_without_vapid_keys():
    from checkcheckserver.config import Config

    with pytest.raises(Exception, match="VAPID"):
        Config(NOTIFY_PUSH_ENABLED=True, EMAIL_ENABLED=False)


def test_boot_fails_loudly_on_a_malformed_vapid_key():
    from checkcheckserver.config import Config

    with pytest.raises(Exception, match="not a usable key pair"):
        Config(
            NOTIFY_PUSH_ENABLED=True,
            EMAIL_ENABLED=False,
            VAPID_PUBLIC_KEY="not-valid-base64url-key",
            VAPID_PRIVATE_KEY="also-not-valid",
            VAPID_CONTACT_EMAIL="admin@example.test",
        )


def test_boot_accepts_a_real_vapid_key_pair():
    from checkcheckserver.config import Config

    config = Config(
        NOTIFY_PUSH_ENABLED=True,
        EMAIL_ENABLED=False,
        VAPID_PUBLIC_KEY=_TEST_VAPID_PUBLIC_KEY,
        VAPID_PRIVATE_KEY=_TEST_VAPID_PRIVATE_KEY,
        VAPID_CONTACT_EMAIL="admin@example.test",
    )
    assert config.NOTIFY_PUSH_ENABLED is True


# ── the disabled instance, over HTTP ─────────────────────────────────────────
#
# The live test server has NOTIFY_PUSH_ENABLED off (no VAPID keys configured),
# so these run straight against it rather than in-process.


def test_public_config_reports_push_disabled():
    public_config = req("api/public-config")
    assert public_config["push_enabled"] is False
    assert public_config["vapid_public_key"] is None


def test_registering_a_subscription_is_refused_when_push_is_disabled():
    req(
        "api/user/me/push-subscriptions",
        "post",
        b={
            "endpoint": "https://push.example/refused",
            "keys": {"p256dh": "p", "auth": "a"},
        },
        expected_http_code=409,
    )


def test_test_push_is_refused_when_push_is_disabled():
    req(
        "api/user/me/notification-settings/test-push",
        "post",
        expected_http_code=409,
    )


def test_listing_and_deleting_subscriptions_work_even_while_push_is_disabled():
    """Only *creating* a subscription needs the master switch on: an operator
    who turns push off after users subscribed must still let them see and
    remove their own devices."""
    subscriptions = req("api/user/me/push-subscriptions")
    assert subscriptions == []

    req(
        f"api/user/me/push-subscriptions/{uuid.uuid4()}",
        "delete",
        expected_http_code=404,
    )
