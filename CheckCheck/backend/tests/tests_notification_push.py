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

# The public half of a *different* throwaway pair. Valid in shape and perfectly
# usable next to its own private key, which is the point: pasted next to the one
# above it is the operator mistake finding 9 is about (chunk N3).
_OTHER_VAPID_PUBLIC_KEY = (
    "BBaRXwsYioVtfQX7qR4vJnknhsiZ1Od9eGXNnFgEGdvhFWIqx89fX3bu8k4dL0ps16Jl1llW-vUDj6tlejDAItQ"
)

# What a stubbed resolver answers when a test wants an endpoint the SSRF guard
# accepts. Not one of the RFC 5737 documentation ranges (192.0.2/24,
# 198.51.100/24, 203.0.113/24): Python's ``ipaddress`` reports all three as
# private, so they would be refused, which is the opposite of what they are for
# here. This one is example.com's, and nothing in this suite connects to it.
_A_PUBLIC_ADDRESS = "93.184.216.34"


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
    from checkcheckserver.notify.push import PushSubscriptionGone

    user = user_factory("mixed")

    async def body(session):
        dead = await _subscribe(session, user.id, endpoint="https://push.example/dead")
        alive = await _subscribe(session, user.id, endpoint="https://push.example/alive")
        await _emit(session, user_id=user.id, type=NotificationType.card_shared)

        push_capture.raise_for[dead.id] = PushSubscriptionGone("410 Gone")
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
    from checkcheckserver.notify.push import PushSubscriptionGone

    user = user_factory("allgone")

    async def body(session):
        a = await _subscribe(session, user.id, endpoint="https://push.example/a-gone")
        b = await _subscribe(session, user.id, endpoint="https://push.example/b-gone")
        await _emit(session, user_id=user.id, type=NotificationType.card_shared)

        push_capture.raise_for[a.id] = PushSubscriptionGone("410 Gone")
        push_capture.raise_for[b.id] = PushSubscriptionGone("404 Not Found")
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
    itself is out of scope for this suite, see the plan's P2 handoff note).

    The resolver is stubbed too, because since chunk N2 ``_send_sync`` judges
    the endpoint before it sends and would otherwise never reach the classifier.
    """
    import pywebpush

    from checkcheckserver.notify import push as push_module

    _stub_resolver(monkeypatch, [_A_PUBLIC_ADDRESS])

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

    # Chunk N3, finding 2: only 404 and 410 mean "this subscription is gone",
    # which is the only outcome that deletes a device. Every other 4xx is the
    # push service refusing the request, which is this server's problem to fix
    # and must leave the subscription alone.
    cases = [
        (404, push_module.PushSubscriptionGone),
        (410, push_module.PushSubscriptionGone),
        (400, push_module.PushEndpointRefused),
        (401, push_module.PushEndpointRefused),
        (403, push_module.PushEndpointRefused),
        (413, push_module.PushEndpointRefused),
        (429, push_module.TransientPushError),
        (500, push_module.TransientPushError),
        (503, push_module.TransientPushError),
    ]
    for status_code, expected in cases:
        monkeypatch.setattr(pywebpush, "webpush", make_fake_webpush(status_code))
        with pytest.raises(expected) as raised:
            push_module._send_sync(push, target, config)
        # Exactly this class. `PushSubscriptionGone` and `PushEndpointRefused`
        # share a base, so a `raises` on the base would pass for either and
        # would not notice the delete moving back to the wrong one.
        assert type(raised.value) is expected

    # No response at all (a socket error, a timeout): still worth retrying.
    def no_response_webpush(**kwargs):
        raise pywebpush.WebPushException("no response", response=None)

    monkeypatch.setattr(pywebpush, "webpush", no_response_webpush)
    with pytest.raises(push_module.TransientPushError):
        push_module._send_sync(push, target, config)


# ── the SSRF guard (chunk N2, finding 1) ─────────────────────────────────────
#
# Modelled on tests_notification_webhooks.py's guard section. Registration is
# driven in-process rather than over HTTP for the same reason the fan-out is:
# the live test server has push switched off, so every HTTP call to the register
# endpoint stops at the 409 before it ever reaches the guard. The route function
# reads its module-level `config`, so that is what the fixture below swaps.


@pytest.fixture
def push_enabled_routes():
    """The settings router, with push switched on for one test."""
    from checkcheckserver.api.routes import routes_notification_settings as routes

    original = routes.config
    routes.config = _config()
    try:
        yield routes
    finally:
        routes.config = original


def _stub_resolver(monkeypatch, addresses):
    """Answer every lookup, in both the async and the blocking resolver, with
    *addresses*, so a test can put a name into any range it likes without
    depending on what this machine's DNS says."""
    from checkcheckserver.notify import net_guard

    async def fake_async(host, port):
        return list(addresses)

    def fake_sync(host, port):
        return list(addresses)

    monkeypatch.setattr(net_guard, "resolve_addresses", fake_async)
    monkeypatch.setattr(net_guard, "resolve_addresses_sync", fake_sync)


def _register(routes, session, user_id, endpoint):
    from types import SimpleNamespace as NS

    return routes.register_push_subscription(
        routes.PushSubscriptionRegister(
            endpoint=endpoint,
            keys=routes.PushSubscriptionKeys(p256dh="p256dh", auth="auth"),
            user_agent="pytest",
        ),
        current_user=NS(id=user_id),
        session=session,
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        # Not https, whatever the host: a real push service is always TLS, and
        # allowing plain http would hand back the whole private range through
        # any name at all.
        "http://push.example/ok",
        "http://127.0.0.1:5432/",
        # An address literal needs no DNS to be judged.
        "https://127.0.0.1:5432/",
        "https://169.254.169.254/latest/meta-data/",  # the cloud metadata service
        "https://10.0.0.5/push",
        "https://[::1]/push",
        "https://[::ffff:127.0.0.1]/push",  # loopback wearing an IPv6 hat
        "https://user:secret@push.example/push",
        "not a url at all",
        "https:///push",
    ],
)
def test_registering_a_private_or_non_https_endpoint_is_a_400_and_writes_no_row(
    push_enabled_routes, user_factory, endpoint
):
    from fastapi import HTTPException

    from checkcheckserver.db import push_subscription

    user = user_factory("ssrfreject")

    async def body(session):
        with pytest.raises(HTTPException) as raised:
            await _register(push_enabled_routes, session, user.id, endpoint)
        rows = await push_subscription.list_for_user(session, user.id)
        return raised.value, [r.endpoint for r in rows]

    error, endpoints = _run(body)
    assert error.status_code == 400
    # The refusal says what kind of thing was wrong and nothing about what the
    # name resolved to: that detail is an information leak of its own, and it
    # goes to the debug log instead.
    assert "127.0.0.1" not in error.detail
    assert "10.0.0.5" not in error.detail
    assert endpoints == []


def test_a_hostname_resolving_into_a_private_range_is_refused_too(
    push_enabled_routes, user_factory, monkeypatch
):
    """The check is on the resolved address, never on the name: a name an
    attacker controls can point anywhere, and checking the string would catch
    nothing."""
    from fastapi import HTTPException

    from checkcheckserver.db import push_subscription

    user = user_factory("ssrfdns")
    _stub_resolver(monkeypatch, ["192.168.1.10"])

    async def body(session):
        with pytest.raises(HTTPException) as raised:
            await _register(
                push_enabled_routes, session, user.id, "https://push.example/rebound"
            )
        rows = await push_subscription.list_for_user(session, user.id)
        return raised.value, rows

    error, rows = _run(body)
    assert error.status_code == 400
    assert "192.168.1.10" not in error.detail
    assert rows == []


def test_one_private_address_among_several_is_enough_to_refuse(
    push_enabled_routes, user_factory, monkeypatch
):
    """Every address has to pass, not just the first one: a name that answers
    with a public address and a loopback one would otherwise be a way to pick
    which of the two the connection uses."""
    from fastapi import HTTPException

    user = user_factory("ssrfmixed")
    _stub_resolver(monkeypatch, [_A_PUBLIC_ADDRESS, "127.0.0.1"])

    async def body(session):
        with pytest.raises(HTTPException) as raised:
            await _register(
                push_enabled_routes, session, user.id, "https://push.example/mixed"
            )
        return raised.value

    assert _run(body).status_code == 400


def test_a_name_that_does_not_resolve_is_a_400_rather_than_a_row(
    push_enabled_routes, user_factory, monkeypatch
):
    from fastapi import HTTPException

    from checkcheckserver.db import push_subscription
    from checkcheckserver.notify import net_guard

    user = user_factory("ssrfnxdomain")

    async def fake_resolve(host, port):
        raise net_guard.HostResolutionError("Name or service not known")

    monkeypatch.setattr(net_guard, "resolve_addresses", fake_resolve)

    async def body(session):
        with pytest.raises(HTTPException) as raised:
            await _register(
                push_enabled_routes, session, user.id, "https://nope.invalid/push"
            )
        rows = await push_subscription.list_for_user(session, user.id)
        return raised.value, rows

    error, rows = _run(body)
    assert error.status_code == 400
    assert rows == []


def test_a_public_https_endpoint_registers_normally(
    push_enabled_routes, user_factory, monkeypatch
):
    from checkcheckserver.db import push_subscription

    user = user_factory("ssrfaccept")
    _stub_resolver(monkeypatch, [_A_PUBLIC_ADDRESS])
    endpoint = "https://push.example/accepted"

    async def body(session):
        info = await _register(push_enabled_routes, session, user.id, endpoint)
        rows = await push_subscription.list_for_user(session, user.id)
        return info, [r.endpoint for r in rows]

    info, endpoints = _run(body)
    assert info.endpoint == endpoint
    assert endpoints == [endpoint]


def test_send_sync_refuses_a_private_endpoint_without_calling_pywebpush(monkeypatch):
    """The second half of the guard: an endpoint that resolved publicly when the
    browser subscribed can resolve privately by the time the row is drained, so
    the judgement is repeated here."""
    import pywebpush

    from checkcheckserver.notify import push as push_module

    calls = []

    def fake_webpush(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)

    target = push_module.PushSubscriptionTarget(
        id=uuid.uuid4(), endpoint="https://127.0.0.1/push", p256dh="p", auth="a"
    )
    push = push_module.OutgoingPush(title="t", body="b", url="https://x/", tag="tag")

    with pytest.raises(push_module.PushEndpointRefused):
        push_module._send_sync(push, target, _config())
    assert calls == []


def test_send_sync_hands_a_public_endpoint_to_pywebpush(monkeypatch):
    import pywebpush

    from checkcheckserver.notify import push as push_module

    calls = []
    monkeypatch.setattr(pywebpush, "webpush", lambda **kwargs: calls.append(kwargs))
    _stub_resolver(monkeypatch, [_A_PUBLIC_ADDRESS])

    target = push_module.PushSubscriptionTarget(
        id=uuid.uuid4(), endpoint="https://push.example/live", p256dh="p", auth="a"
    )
    push = push_module.OutgoingPush(title="t", body="b", url="https://x/", tag="tag")

    push_module._send_sync(push, target, _config())
    assert len(calls) == 1
    assert calls[0]["subscription_info"]["endpoint"] == "https://push.example/live"


def test_a_refusal_at_delivery_fails_the_row_and_keeps_the_subscription(
    user_factory, monkeypatch
):
    """A refused endpoint is not a dead one. Only `PushSubscriptionGone`
    deletes the subscription it came from; this one must not, or a host that
    starts resolving privately would quietly unsubscribe somebody's device."""
    import pywebpush

    from checkcheckserver.db import push_subscription
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationOutboxStatus

    user = user_factory("ssrfdelivery")
    calls = []
    monkeypatch.setattr(pywebpush, "webpush", lambda **kwargs: calls.append(kwargs))

    async def body(session):
        # Straight into the store, bypassing the route: the row this simulates
        # is one that passed the registration guard and only became private
        # afterwards, which is exactly the case the delivery re-check exists for.
        await _subscribe(session, user.id, endpoint="https://127.0.0.1/push")
        await _emit(session, user_id=user.id, type=NotificationType.card_shared)
        # No sender override: the real PywebpushSender has to run for the guard
        # inside it to be the thing under test.
        await _drain(session)
        remaining = await push_subscription.list_for_user(session, user.id)
        return await _snapshot(session, user.id), [r.endpoint for r in remaining]

    rows, remaining_endpoints = _run(body)
    assert len(rows) == 1
    assert rows[0]["status"] == NotificationOutboxStatus.failed.value
    assert rows[0]["attempts"] == 1
    assert "refused" in rows[0]["last_error"].lower()
    # The subscription is left exactly where it was, and nothing went out.
    assert remaining_endpoints == ["https://127.0.0.1/push"]
    assert calls == []


# ── the narrowed delete (chunk N3, finding 2) ────────────────────────────────
#
# The failure this section exists to prevent: one mismatched VAPID key pair
# makes every push service answer 401, and before N3 that deleted every
# subscription on the instance, device by device, with a debug line each.


def _fake_webpush_answering(monkeypatch, status_code):
    """Make ``pywebpush.webpush`` raise as a push service answering
    *status_code* would, and return the list its calls land in."""
    import pywebpush

    calls = []

    class FakeResponse:
        status_code = None
        text = "detail from the push service"

    FakeResponse.status_code = status_code

    def fake_webpush(**kwargs):
        calls.append(kwargs)
        raise pywebpush.WebPushException("refused", response=FakeResponse())

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    return calls


@pytest.mark.parametrize("status_code", [401, 413])
def test_a_push_service_refusal_fails_the_row_and_keeps_the_subscription(
    user_factory, monkeypatch, status_code
):
    """401 is a broken VAPID key and 413 is a payload this server built too
    large. Both are the operator's problem, and neither is evidence that the
    user's device is gone, so the row fails and the subscription survives."""
    from checkcheckserver.db import push_subscription
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationOutboxStatus

    user = user_factory(f"refused{status_code}")
    endpoint = f"https://push.example/refused-{status_code}"
    _stub_resolver(monkeypatch, [_A_PUBLIC_ADDRESS])
    calls = _fake_webpush_answering(monkeypatch, status_code)

    async def body(session):
        await _subscribe(session, user.id, endpoint=endpoint)
        await _emit(session, user_id=user.id, type=NotificationType.card_shared)
        # No sender override: the real sender and its classification are the
        # thing under test.
        await _drain(session)
        remaining = await push_subscription.list_for_user(session, user.id)
        return await _snapshot(session, user.id), [r.endpoint for r in remaining]

    rows, remaining_endpoints = _run(body)
    assert len(rows) == 1
    assert rows[0]["status"] == NotificationOutboxStatus.failed.value
    assert rows[0]["attempts"] == 1
    # The row is kept for inspection and says which status caused it: that
    # string is all an operator has to go on.
    assert str(status_code) in rows[0]["last_error"]
    assert remaining_endpoints == [endpoint]
    assert len(calls) == 1


def test_only_a_gone_subscription_is_deleted_when_both_outcomes_happen_at_once(
    push_capture, user_factory
):
    """One device 410s and another 401s in the same attempt: the dead one goes,
    the refused one stays, and the row fails because nothing was delivered."""
    from checkcheckserver.db import push_subscription
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationOutboxStatus
    from checkcheckserver.notify.push import PushEndpointRefused, PushSubscriptionGone

    user = user_factory("goneandrefused")

    async def body(session):
        dead = await _subscribe(session, user.id, endpoint="https://push.example/n3-dead")
        refused = await _subscribe(
            session, user.id, endpoint="https://push.example/n3-refused"
        )
        await _emit(session, user_id=user.id, type=NotificationType.card_shared)

        push_capture.raise_for[dead.id] = PushSubscriptionGone("410 Gone")
        push_capture.raise_for[refused.id] = PushEndpointRefused(
            "The push service refused this request (401)."
        )
        await _drain(session)

        remaining = await push_subscription.list_for_user(session, user.id)
        return await _snapshot(session, user.id), [r.endpoint for r in remaining]

    rows, remaining_endpoints = _run(body)
    assert len(rows) == 1
    assert rows[0]["status"] == NotificationOutboxStatus.failed.value
    assert remaining_endpoints == ["https://push.example/n3-refused"]


# ── the push payload byte budget (chunk N3, decision 2) ──────────────────────


def _oversized_context(cl_id=None):
    """A reminder on a card whose name and note are both far past the budget.
    Not an attack, just a long note: nothing constrains either field's length."""
    return {
        "type": "reminder_due",
        "cl_id": cl_id or uuid.uuid4(),
        "notification_id": uuid.uuid4(),
        "checklist_name": "Weekend groceries " + ("shopping " * 400),
        "note": "Remember to " + ("bring the reusable bags " * 300),
        "actor": "Anna Analyst",
    }


def test_an_oversized_push_payload_is_truncated_to_the_budget():
    from checkcheckserver.notify import render

    config = _config(
        NOTIFY_PUSH_CONTENT_MODE="full", SERVER_PUBLIC_URL="https://checks.example.test"
    )
    payload = render.push_payload(_oversized_context(), config, tag="reminder:abc")

    assert render._push_payload_size(payload) <= render.PUSH_PAYLOAD_MAX_BYTES
    # The title is what a lock screen shows first, so it survives, still says
    # what happened, and says that it was cut.
    assert payload["title"].startswith("Reminder: Remember to bring")
    assert payload["title"].endswith("…")
    # The link and the dedupe key are never truncated: a clipped URL is a
    # broken one, and the tag is what stops two notifications stacking up.
    assert payload["url"].startswith("https://checks.example.test/?card=")
    assert payload["tag"] == "reminder:abc"


def test_the_body_is_given_up_before_the_title():
    """Ordering, not just the total: the body of a reminder repeats the card
    name, so it is the cheaper half to lose."""
    from checkcheckserver.notify import render

    config = _config(NOTIFY_PUSH_CONTENT_MODE="full")
    payload = render.push_payload(_oversized_context(), config, tag="t")

    assert len(payload["title"]) > len(payload["body"])


def test_a_payload_within_the_budget_is_left_exactly_as_it_was():
    from checkcheckserver.notify import render

    config = _config(NOTIFY_PUSH_CONTENT_MODE="full")
    context = {
        "type": "card_shared",
        "cl_id": uuid.uuid4(),
        "notification_id": uuid.uuid4(),
        "checklist_name": "Weekend groceries",
        "actor": "Anna Analyst",
    }
    payload = render.push_payload(context, config, tag="t")

    assert payload["title"] == 'Anna Analyst shared "Weekend groceries" with you'
    assert "…" not in payload["title"]
    assert "…" not in payload["body"]


def test_an_oversized_payload_actually_leaves_the_server(push_capture, user_factory):
    """The end of the same story: a reminder-sized payload reaches the sender
    shortened instead of being refused with a 413 by the push service."""
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationOutboxStatus
    from checkcheckserver.notify import render

    user = user_factory("oversized")
    cfg = _config(NOTIFY_PUSH_CONTENT_MODE="full")
    cl_id = uuid.uuid4()

    async def body(session):
        await _subscribe(session, user.id, endpoint="https://push.example/oversized")
        await _emit(
            session,
            user_id=user.id,
            type=NotificationType.card_shared,
            cl_id=cl_id,
            payload={
                "actor_id": str(uuid.uuid4()),
                "actor_display_name": "Anna Analyst",
                "checklist_name": "Weekend groceries " + ("shopping " * 400),
            },
            config=cfg,
        )
        await _drain(session, config=cfg)
        return await _snapshot(session, user.id)

    rows = _run(body)
    assert len(rows) == 1
    assert rows[0]["status"] == NotificationOutboxStatus.sent.value
    assert len(push_capture.sent) == 1
    push, _target = push_capture.sent[0]
    size = render._push_payload_size(
        {"title": push.title, "body": push.body, "url": push.url, "tag": push.tag}
    )
    assert size <= render.PUSH_PAYLOAD_MAX_BYTES
    assert "Anna Analyst" in push.title


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


def test_boot_fails_on_two_valid_halves_from_different_key_pairs():
    """Finding 9. Both keys load, both are the right shape, and every push this
    instance ever sends would be rejected by the push service: the public half
    goes to the browser as `applicationServerKey` and the private half signs
    with a different identity. Cheaper to refuse to start."""
    from checkcheckserver.config import Config

    with pytest.raises(Exception, match="not a usable key pair"):
        Config(
            NOTIFY_PUSH_ENABLED=True,
            EMAIL_ENABLED=False,
            VAPID_PUBLIC_KEY=_OTHER_VAPID_PUBLIC_KEY,
            VAPID_PRIVATE_KEY=_TEST_VAPID_PRIVATE_KEY,
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
