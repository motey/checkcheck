"""Tests for the webhook channel and the feed retention pass (chunk E6).

Two features that share a module because they share a harness: both are driven in
this process, against the test database, with a substituted ``Config``.

**Why not over HTTP.** ``NOTIFY_WEBHOOK_ENABLED`` is deliberately left off on the
test instance: ``tests_notification_prefs.py`` asserts what an administrator-
locked channel looks like, and the webhook channel is the one that is locked
there. Switching it on for the whole process would quietly delete that coverage.
So the fan-out, the queue and the sender are exercised by calling them with a
config that has webhooks on, which is also the only way to test the SSRF guard's
two sides (refused by default, allowed with the flag) in one run.

The plan is ``docs/plans/EMAIL_NOTIFICATIONS.md``, section 7 chunk E6 items 2
and 3.
"""

import asyncio
import datetime
import uuid
from types import SimpleNamespace

import pytest

from utils import create_test_user
from statics import MAIL_CAPTURE_FROM_ADDRESS


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
    """An instance with the webhook channel switched on, plus overrides."""
    from checkcheckserver.config import Config

    settings = {
        "EMAIL_ENABLED": True,
        "EMAIL_TRANSPORT": "null",
        "EMAIL_FROM_ADDRESS": MAIL_CAPTURE_FROM_ADDRESS,
        "NOTIFY_WEBHOOK_ENABLED": True,
        # The webhook channel is what these tests look at; mail for the same
        # notification would only add rows to filter out.
        "NOTIFY_DEFAULT_MODES": {
            "card_shared": {"in_app": "immediate", "email": "off", "webhook": "immediate"},
            "card_invited": {"in_app": "immediate", "email": "off", "webhook": "immediate"},
            "public_link_opened": {"in_app": "immediate", "email": "off", "webhook": "off"},
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
    """One user's webhook rows as plain dicts, detached from the session."""
    from checkcheckserver.model.notification_outbox import NotificationChannel

    return [
        {"status": row.status, "attempts": row.attempts, "last_error": row.last_error}
        for row in await _rows(session, user_id, NotificationChannel.webhook.value)
    ]


async def _set_webhook(session, user_id, url, *, prefs=None):
    from checkcheckserver.db.user_notification_settings import get_or_create_settings
    from checkcheckserver.notify.prefs import apply_prefs_patch

    settings = await get_or_create_settings(session, user_id)
    settings.webhook_url = url
    if prefs:
        settings.prefs = apply_prefs_patch(settings.prefs, prefs)
    session.add(settings)
    await session.commit()
    return settings


async def _drain(session, *, config=None, sender=None, minutes_ahead=5):
    """Deliver everything due *minutes_ahead* from now.

    Looking at the queue from the future rather than sleeping through a backoff,
    the same trick the other outbox tests use. A second drain in one test has to
    look further ahead than the first, because claiming a row already scheduled
    its next attempt.
    """
    from checkcheckserver.model._base_model import naive_utc_now
    from checkcheckserver.notify.outbox import drain_once

    return await drain_once(
        session,
        now=naive_utc_now() + datetime.timedelta(minutes=minutes_ahead),
        config=config or _config(),
        webhook_sender=sender,
    )


@pytest.fixture(scope="module")
def user_factory():
    """Accounts owned by exactly one test each, so rows can be told apart."""
    made = {}

    def make(slug: str):
        if slug not in made:
            name = f"e6{slug}user"
            created = create_test_user(name, f"{name}_pw_secure1", f"{name}@test.de")
            made[slug] = SimpleNamespace(id=uuid.UUID(created["id"]), name=name)
        return made[slug]

    return make


@pytest.fixture
def webhook_capture():
    """A capturing webhook sender, installed for the duration of one test."""
    from checkcheckserver.notify.webhooks import (
        CapturingWebhookSender,
        reset_webhook_sender,
        set_webhook_sender,
    )

    sender = CapturingWebhookSender()
    set_webhook_sender(sender)
    try:
        yield sender
    finally:
        reset_webhook_sender()


# ── the fan-out ───────────────────────────────────────────────────────────────


def test_a_notification_reaches_the_users_own_endpoint(webhook_capture, user_factory):
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutboxStatus,
    )

    user = user_factory("hooked")
    cl_id = uuid.uuid4()
    url = "https://hooks.example.org/checkcheck"

    async def body(session):
        await _set_webhook(session, user.id, url)
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
        )
        queued = await _rows(session, user.id, NotificationChannel.webhook.value)
        # Snapshotted before the drain: claiming a row rewrites `not_before` with
        # the next attempt's backoff, so reading it afterwards would be reading
        # the wrong moment.
        schedule = [(row.not_before, row.created_at, row.dedupe_key) for row in queued]
        await _drain(session)
        return schedule, await _rows(session, user.id, NotificationChannel.webhook.value)

    schedule, rows = _run(body)

    assert len(schedule) == 1
    not_before, created_at, dedupe_key = schedule[0]
    # Due straight away: the suppression window is a kindness to a human inbox.
    assert not_before <= created_at + datetime.timedelta(seconds=1)
    # No dedupe key, so a webhook can never be folded into a summary of five.
    assert dedupe_key is None
    assert rows[0].status == NotificationOutboxStatus.sent.value

    assert len(webhook_capture.sent) == 1
    posted = webhook_capture.sent[0]
    assert posted.url == url
    assert posted.body["type"] == "card_shared"
    assert posted.body["checklist_id"] == str(cl_id)
    assert posted.body["checklist_name"] == "Weekend groceries"
    assert posted.body["actor"] == "Anna Analyst"
    assert f"?card={cl_id}" in posted.body["url"]
    assert "Anna Analyst" in posted.body["text"]
    # Every key is always present, so a receiver can index without guarding.
    for key in (
        "type",
        "notification_id",
        "checklist_id",
        "checklist_name",
        "actor",
        "created_at",
        "url",
        "app",
        "text",
    ):
        assert key in posted.body


def test_the_master_switch_and_a_missing_url_both_queue_nothing(
    webhook_capture, user_factory
):
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationChannel

    off_instance = user_factory("hookoff")
    no_url = user_factory("hooknourl")

    async def body(session):
        # An instance with webhooks disabled: the resolver caps the channel off,
        # exactly like an administrator-disabled type.
        await _set_webhook(session, off_instance.id, "https://hooks.example.org/x")
        await _emit(
            session,
            user_id=off_instance.id,
            type=NotificationType.card_shared,
            config=_config(NOTIFY_WEBHOOK_ENABLED=False),
        )
        # Webhooks on, the user opted in, but they never saved a URL.
        await _emit(session, user_id=no_url.id, type=NotificationType.card_shared)
        await _drain(session)
        return (
            await _rows(session, off_instance.id, NotificationChannel.webhook.value),
            await _rows(session, no_url.id, NotificationChannel.webhook.value),
        )

    off_rows, no_url_rows = _run(body)
    assert off_rows == []
    assert no_url_rows == []
    assert webhook_capture.sent == []


def test_a_user_who_switched_the_channel_off_gets_nothing(webhook_capture, user_factory):
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import NotificationChannel

    user = user_factory("hookmuted")

    async def body(session):
        await _set_webhook(
            session,
            user.id,
            "https://hooks.example.org/muted",
            prefs={"card_shared": {"webhook": "off"}},
        )
        await _emit(session, user_id=user.id, type=NotificationType.card_shared)
        await _drain(session)
        return await _rows(session, user.id, NotificationChannel.webhook.value)

    assert _run(body) == []
    assert webhook_capture.sent == []


def test_a_url_the_user_changes_afterwards_does_not_redirect_a_queued_message(
    webhook_capture, user_factory
):
    """The target is snapshotted with the body, like an email's address."""
    from checkcheckserver.model.notification import NotificationType

    user = user_factory("hookmoved")

    async def body(session):
        await _set_webhook(session, user.id, "https://hooks.example.org/first")
        await _emit(session, user_id=user.id, type=NotificationType.card_shared)
        await _set_webhook(session, user.id, "https://hooks.example.org/second")
        await _drain(session)

    _run(body)
    assert [w.url for w in webhook_capture.sent] == ["https://hooks.example.org/first"]


def test_a_failing_receiver_retries_and_a_refusal_does_not(user_factory):
    from checkcheckserver.model.notification import NotificationType
    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutboxStatus,
    )
    from checkcheckserver.notify.webhooks import (
        CapturingWebhookSender,
        PermanentWebhookError,
        TransientWebhookError,
    )

    flaky = user_factory("hookflaky")
    refused = user_factory("hookrefused")

    async def body(session):
        await _set_webhook(session, flaky.id, "https://hooks.example.org/flaky")
        await _set_webhook(session, refused.id, "https://hooks.example.org/refused")
        await _emit(session, user_id=flaky.id, type=NotificationType.card_shared)
        await _emit(session, user_id=refused.id, type=NotificationType.card_shared)

        transient = CapturingWebhookSender()
        transient.raise_on_send = TransientWebhookError("503 from the receiver")
        await _drain(session, sender=transient)
        # Read out as plain values, not as ORM rows: the second drain below
        # refreshes the same instances, so a row held from here would silently
        # change under the assertions.
        flaky_rows = await _snapshot(session, flaky.id)

        permanent = CapturingWebhookSender()
        permanent.raise_on_send = PermanentWebhookError("404 from the receiver")
        # Further ahead than the first drain: that one spent an attempt on this
        # row too and pushed it into its backoff.
        await _drain(session, sender=permanent, minutes_ahead=30)
        return flaky_rows, await _snapshot(session, refused.id)

    flaky_rows, refused_rows = _run(body)

    # Transient: still pending, one attempt spent, scheduled for later.
    assert len(flaky_rows) == 1
    assert flaky_rows[0]["status"] == NotificationOutboxStatus.pending.value
    assert flaky_rows[0]["attempts"] == 1
    assert "503" in flaky_rows[0]["last_error"]

    # Permanent: dead-lettered on the first attempt, with the reason kept.
    assert len(refused_rows) == 1
    assert refused_rows[0]["status"] == NotificationOutboxStatus.failed.value
    assert "404" in refused_rows[0]["last_error"]


def test_an_unusable_webhook_payload_fails_instead_of_retrying_forever(user_factory):
    from checkcheckserver.model.notification_outbox import (
        NotificationChannel,
        NotificationOutboxStatus,
    )
    from checkcheckserver.notify import outbox

    user = user_factory("hookjunk")

    async def body(session):
        # Straight into the table: `enqueue` rejects this shape, which is the
        # point, so the bad row has to be planted the way an older release
        # might have left one behind.
        from checkcheckserver.model.notification_outbox import NotificationOutbox

        row = NotificationOutbox(
            user_id=user.id,
            channel=NotificationChannel.webhook,
            payload={"url": "https://hooks.example.org/x"},  # no body
        )
        session.add(row)
        await session.commit()

        with pytest.raises(outbox.OutboxPayloadError):
            await outbox.enqueue(
                session,
                user_id=user.id,
                channel=NotificationChannel.webhook,
                payload={"body": {"a": 1}},  # no url
            )
        await session.rollback()

        await _drain(session)
        return await _rows(session, user.id, NotificationChannel.webhook.value)

    rows = _run(body)
    assert len(rows) == 1
    assert rows[0].status == NotificationOutboxStatus.failed.value


# ── the SSRF guard ────────────────────────────────────────────────────────────


def _check(url, **overrides):
    from checkcheckserver.notify.webhooks import check_webhook_target

    return asyncio.run(check_webhook_target(url, _config(**overrides)))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hook",
        "http://localhost:9000/hook",
        "http://169.254.169.254/latest/meta-data/",  # the cloud metadata service
        "http://10.0.0.5/hook",
        "http://192.168.1.10:8080/hook",
        "http://172.16.4.4/hook",
        "http://[::1]/hook",
        "http://[::ffff:127.0.0.1]/hook",  # loopback wearing an IPv6 hat
        "http://0.0.0.0/hook",
    ],
)
def test_private_and_loopback_targets_are_refused_by_default(url):
    from checkcheckserver.notify.webhooks import WebhookUrlRefused

    with pytest.raises(WebhookUrlRefused):
        _check(url)


def test_a_hostname_resolving_to_a_private_address_is_refused_too():
    """The check is on the resolved address, never on the name: a name an
    attacker controls can point anywhere, and checking the string would catch
    nothing."""
    from checkcheckserver.notify.webhooks import WebhookUrlRefused

    # A name every resolver answers with 127.0.0.1, without needing the network:
    # `localhost` is in /etc/hosts on every machine this suite runs on.
    with pytest.raises(WebhookUrlRefused):
        _check("https://localhost/hook")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.org/hook",
        "file:///etc/passwd",
        "https://user:secret@127.0.0.1/hook",
        "https:///hook",
        "not a url at all",
    ],
)
def test_a_url_that_is_not_a_plain_http_target_is_refused(url):
    from checkcheckserver.notify.webhooks import WebhookUrlRefused

    with pytest.raises(WebhookUrlRefused):
        _check(url)


def test_private_targets_are_allowed_once_the_operator_says_so():
    target = _check("http://127.0.0.1:9999/hook", NOTIFY_WEBHOOK_ALLOW_PRIVATE_IPS=True)
    assert target.address == "127.0.0.1"
    assert target.host_header == "127.0.0.1:9999"


def test_the_request_goes_to_the_address_that_was_checked():
    """Pinning is what makes the guard hold: a name that answers "public" to the
    check and "127.0.0.1" to the connection would otherwise walk straight past
    it. The Host header still names what the user configured."""
    from checkcheckserver.notify.webhooks import WebhookTarget, _pinned_url

    target = WebhookTarget(
        url="https://hooks.example.org:8443/a/b?x=1",
        address="203.0.113.7",
        host_header="hooks.example.org:8443",
        hostname="hooks.example.org",
        is_tls=True,
    )
    assert (
        _pinned_url("https://hooks.example.org:8443/a/b?x=1", target)
        == "https://203.0.113.7:8443/a/b?x=1"
    )

    v6 = WebhookTarget(
        url="http://hooks.example.org/a",
        address="2001:db8::1",
        host_header="hooks.example.org",
        hostname="hooks.example.org",
        is_tls=False,
    )
    assert _pinned_url("http://hooks.example.org/a", v6) == "http://[2001:db8::1]/a"


def test_a_real_local_receiver_gets_the_body_it_was_promised():
    """One round trip through the actual HTTP sender, so the pinning, the
    headers and the JSON encoding are exercised rather than mocked.

    Runs against a throwaway server on 127.0.0.1, which needs the private-address
    flag: refusing that address is the default and is asserted above.
    """
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from checkcheckserver.notify.webhooks import HttpxWebhookSender, OutgoingWebhook

    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received["body"] = json.loads(self.rfile.read(length) or b"{}")
            received["host"] = self.headers.get("Host")
            received["content_type"] = self.headers.get("Content-Type")
            received["user_agent"] = self.headers.get("User-Agent")
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):  # keep the test output readable
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        asyncio.run(
            HttpxWebhookSender().send(
                OutgoingWebhook(
                    url=f"http://127.0.0.1:{port}/hook",
                    body={"type": "card_shared", "text": "Anna shared a card."},
                ),
                _config(NOTIFY_WEBHOOK_ALLOW_PRIVATE_IPS=True),
            )
        )
    finally:
        server.shutdown()

    assert received["body"] == {"type": "card_shared", "text": "Anna shared a card."}
    assert received["host"] == f"127.0.0.1:{port}"
    assert received["content_type"] == "application/json"
    assert received["user_agent"].startswith("CheckCheck/")


@pytest.mark.parametrize(
    "status_code, transient",
    [(500, True), (503, True), (429, True), (408, True), (400, False), (404, False), (302, False)],
)
def test_a_receivers_status_code_decides_whether_it_is_retried(status_code, transient):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from checkcheckserver.notify.webhooks import (
        HttpxWebhookSender,
        OutgoingWebhook,
        PermanentWebhookError,
        TransientWebhookError,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
            self.send_response(status_code)
            if 300 <= status_code < 400:
                self.send_header("Location", "http://127.0.0.1/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    expected = TransientWebhookError if transient else PermanentWebhookError
    try:
        with pytest.raises(expected):
            asyncio.run(
                HttpxWebhookSender().send(
                    OutgoingWebhook(url=f"http://127.0.0.1:{port}/hook", body={"a": 1}),
                    _config(NOTIFY_WEBHOOK_ALLOW_PRIVATE_IPS=True),
                )
            )
    finally:
        server.shutdown()


# ── feed retention ────────────────────────────────────────────────────────────


def test_feed_pruning_removes_old_read_rows_and_keeps_everything_else(user_factory):
    """Only rows the user has already read are pruned. An unread notification is
    still somebody's inbox, whatever its age, and its badge is the only sign the
    event ever happened."""
    from checkcheckserver.db.notification import prune_feed_once
    from checkcheckserver.model.notification import Notification, NotificationType

    user = user_factory("pruned")
    now = datetime.datetime(2026, 8, 2, 12, 0, 0)
    old = now - datetime.timedelta(days=400)
    recent = now - datetime.timedelta(days=3)

    async def body(session):
        from sqlmodel import select

        made = {}
        for label, created_at, read_at in (
            ("old_read", old, old),
            ("old_unread", old, None),
            ("recent_read", recent, recent),
            ("recent_unread", recent, None),
        ):
            row = Notification(
                user_id=user.id,
                type=NotificationType.card_shared,
                cl_id=uuid.uuid4(),
                payload={"label": label},
                read_at=read_at,
            )
            session.add(row)
            await session.flush()
            made[label] = row.id
        await session.commit()
        # created_at is stamped by the model, so backdate it in a second pass.
        from sqlmodel import update as sql_update

        for label, created_at in (
            ("old_read", old),
            ("old_unread", old),
            ("recent_read", recent),
            ("recent_unread", recent),
        ):
            await session.exec(
                sql_update(Notification)
                .where(Notification.id == made[label])
                .values(created_at=created_at)
            )
        await session.commit()

        removed = await prune_feed_once(
            session, config=_config(NOTIFY_FEED_RETENTION_DAYS=180), now=now
        )
        surviving = list(
            (
                await session.exec(
                    select(Notification).where(Notification.user_id == user.id)
                )
            ).all()
        )
        return removed, {row.payload["label"] for row in surviving}

    removed, surviving = _run(body)
    assert removed == 1
    assert surviving == {"old_unread", "recent_read", "recent_unread"}


def test_feed_pruning_is_off_at_zero(user_factory):
    from checkcheckserver.db.notification import prune_feed_once

    async def body(session):
        return await prune_feed_once(
            session, config=_config(NOTIFY_FEED_RETENTION_DAYS=0)
        )

    assert _run(body) == 0
