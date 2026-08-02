"""Tests for the first half of chunk E6: mailing a public link to somebody.

``POST /api/checklist/{id}/public-share/email`` is the only endpoint in the
application where a signed-in user decides who the server writes to, so most of
what is asserted here is about what it *refuses* to do: send without ownership,
send a link that does not exist or no longer works, send more than the hourly
allowance, repeat the recipient's address in an error, or put a link's passphrase
in the message.

**How they run.** The endpoint is called over HTTP against the live test server,
which queues the message; the message itself is then produced by driving
``drain_once()`` in this process with the capturing transport installed (the same
split, and for the same reasons, as ``tests_notification_wiring.py``). The test
instance sets ``SHARING_PUBLIC_LINK_EMAIL_ENABLED`` in ``conftest``; the
switched-off case is asserted against the gate dependency directly, because that
flag is read once per process.

The plan is ``docs/plans/EMAIL_NOTIFICATIONS.md``, section 7 chunk E6 item 1.
"""

import asyncio
import datetime
import uuid
from types import SimpleNamespace

import pytest

from utils import (
    authorize_for_access_token,
    create_test_user,
    req,
)
from statics import (
    INTERNAL_TEST_EMAIL_DOMAIN,
    MAIL_CAPTURE_FROM_ADDRESS,
    TEST_USER_NAME,
    TEST_USER_PW,
)


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
    """A Config that looks like the running test instance, plus overrides."""
    from checkcheckserver.config import Config

    settings = {
        "EMAIL_ENABLED": True,
        "EMAIL_TRANSPORT": "null",
        "EMAIL_FROM_ADDRESS": MAIL_CAPTURE_FROM_ADDRESS,
        "SHARING_PUBLIC_LINK_EMAIL_ENABLED": True,
    }
    settings.update(overrides)
    return Config(**settings)


async def _drain(session, *, config=None):
    """Deliver everything due now. An invitation has no suppression window."""
    from checkcheckserver.model._base_model import naive_utc_now
    from checkcheckserver.notify.outbox import drain_once

    return await drain_once(
        session,
        now=naive_utc_now() + datetime.timedelta(minutes=5),
        config=config,
    )


def _mail_to(mail_capture, address):
    return [email for email in mail_capture.sent if email.to == address]


def _create_card(name: str, token: str = None) -> dict:
    return req("api/checklist", "post", b={"name": name}, access_token=token)


def _create_link(checklist_id: str, token: str = None, **body) -> dict:
    return req(
        f"api/checklist/{checklist_id}/public-links",
        "post",
        b={"permission": "view", **body},
        access_token=token,
    )


def _send(checklist_id: str, link_id: str, to: str, *, token=None, message=None, expect=202):
    body = {"link_id": link_id, "to": to}
    if message is not None:
        body["message"] = message
    return req(
        f"api/checklist/{checklist_id}/public-share/email",
        "post",
        b=body,
        access_token=token,
        expected_http_code=expect,
        tolerated_error_codes=[expect] if expect >= 400 else None,
    )


@pytest.fixture(scope="module")
def sender():
    """An account of this module's own, so the hourly limit is not shared."""
    name = "e6linkmailer"
    password = f"{name}_pw_secure1"
    created = create_test_user(name, password, f"{name}@test.de")
    return SimpleNamespace(
        id=uuid.UUID(created["id"]),
        name=name,
        password=password,
        token=authorize_for_access_token(name, password),
    )


@pytest.fixture
def recipient():
    """A fresh outside address per test, so captured mail can be told apart."""
    return f"outsider-{uuid.uuid4().hex[:10]}@example.org"


# ── the happy path ────────────────────────────────────────────────────────────


def test_an_edit_link_reaches_the_recipient_and_really_opens_the_card(
    mail_capture, sender, recipient
):
    """The whole point of the feature: somebody with no account gets a message
    whose link lets them work on the card."""
    from checkcheckserver.model.notification_outbox import NotificationOutboxStatus

    card = _create_card("E6-InviteMe-edit", sender.token)
    link = _create_link(card["id"], sender.token, permission="edit")

    result = _send(card["id"], link["id"], recipient, token=sender.token)
    assert result["queued_id"]
    # The address is not echoed even on success: the client typed it, and a
    # response that repeats it is one more place it can leak from.
    assert recipient not in str(result)

    _run(lambda session: _drain(session))
    mail = _mail_to(mail_capture, recipient)
    assert len(mail) == 1

    # The link in the message is the real capability, and it resolves anonymously
    # at the level the link was created with.
    assert f"/p/{link['token']}" in mail[0].text_body
    resolved = req(
        f"api/public/checklist/{link['token']}",
        suppress_auth=True,
    )
    assert resolved["my_permission"] == "edit"

    # The queued row is the sender's, which is what the hourly limit counts.
    rows = _run(lambda session: _rows_for(session, sender.id))
    sent = [r for r in rows if (r.payload or {}).get("to") == recipient]
    assert len(sent) == 1
    assert sent[0].status == NotificationOutboxStatus.sent.value
    assert sent[0].notification_id is None  # not a notification, nobody's feed


async def _rows_for(session, user_id):
    from sqlmodel import select

    from checkcheckserver.model.notification_outbox import NotificationOutbox

    result = await session.exec(
        select(NotificationOutbox)
        .where(NotificationOutbox.user_id == user_id)
        .order_by(NotificationOutbox.created_at)
        .execution_options(populate_existing=True)
    )
    return list(result.all())


def test_the_message_names_the_card_the_sender_and_the_personal_note(
    mail_capture, sender, recipient
):
    card = _create_card("E6-Weekend groceries", sender.token)
    link = _create_link(card["id"], sender.token, permission="check")

    _send(
        card["id"],
        link["id"],
        recipient,
        token=sender.token,
        message="the one we talked about on Friday",
    )
    _run(lambda session: _drain(session))

    mail = _mail_to(mail_capture, recipient)
    assert len(mail) == 1
    assert "E6-Weekend groceries" in mail[0].subject
    assert sender.name in mail[0].text_body
    assert "the one we talked about on Friday" in mail[0].text_body
    # The level is stated in words the recipient can act on, not as an enum.
    assert "tick items off this list, without signing in" in mail[0].text_body
    # It is an invitation, not a subscription: nothing to unsubscribe from.
    assert "List-Unsubscribe" not in mail[0].headers


def test_a_passphrase_never_appears_in_the_message(mail_capture, sender, recipient):
    """A protected link is announced as protected. Mailing the passphrase with
    the link it protects would make the protection decorative."""
    card = _create_card("E6-Protected", sender.token)
    link = _create_link(
        card["id"], sender.token, permission="view", password="correct-horse-staple"
    )

    _send(card["id"], link["id"], recipient, token=sender.token)
    _run(lambda session: _drain(session))

    mail = _mail_to(mail_capture, recipient)
    assert len(mail) == 1
    assert "correct-horse-staple" not in mail[0].text_body
    assert "correct-horse-staple" not in (mail[0].html_body or "")
    assert "passphrase" in mail[0].text_body


def test_minimal_content_mode_sends_the_link_and_nothing_else(sender, recipient):
    """``NOTIFY_EMAIL_CONTENT_MODE: minimal`` applies here too: mail goes to an
    address somebody typed, so a typo sends it to a stranger."""
    from checkcheckserver.notify.invitation import render_public_link_invitation

    payload = render_public_link_invitation(
        to=recipient,
        token="tok-abc",
        permission="edit",
        checklist_name="Board meeting agenda",
        sender_display_name="Anna Analyst",
        personal_message=None,
        password_protected=False,
        config=_config(NOTIFY_EMAIL_CONTENT_MODE="minimal"),
    )
    whole_message = payload["subject"] + payload["text_body"] + payload["html_body"]
    assert "Board meeting agenda" not in whole_message
    assert "Anna Analyst" not in whole_message
    # The link itself still goes, which is the only part the recipient needs.
    assert "/p/tok-abc" in payload["text_body"]


# ── what it refuses ───────────────────────────────────────────────────────────


def test_a_collaborator_who_is_not_the_owner_cannot_mail_the_link(sender, recipient):
    card = _create_card("E6-NotYours", sender.token)
    link = _create_link(card["id"], sender.token)

    other = authorize_for_access_token(TEST_USER_NAME, TEST_USER_PW)
    other_id = req("api/user/me", access_token=other)["id"]
    req(
        f"api/checklist/{card['id']}/shares/{other_id}",
        "put",
        b={"permission": "edit"},
        access_token=sender.token,
    )

    result = _send(card["id"], link["id"], recipient, token=other, expect=403)
    assert recipient not in str(result)


def test_a_link_belonging_to_another_card_is_not_found(sender, recipient):
    """The link id is checked against the card in the path, so one card's link
    cannot be mailed through another card's endpoint."""
    card = _create_card("E6-CardA", sender.token)
    other_card = _create_card("E6-CardB", sender.token)
    link = _create_link(other_card["id"], sender.token)

    _send(card["id"], link["id"], recipient, token=sender.token, expect=404)
    _send(card["id"], str(uuid.uuid4()), recipient, token=sender.token, expect=404)


def test_a_disabled_or_expired_link_is_refused(sender, recipient):
    """This endpoint never enables or extends a link, so a dead one is a 409
    rather than a message nobody can open."""
    card = _create_card("E6-DeadLinks", sender.token)

    disabled = _create_link(card["id"], sender.token)
    req(
        f"api/checklist/{card['id']}/public-links/{disabled['id']}",
        "patch",
        b={"enabled": False},
        access_token=sender.token,
    )
    _send(card["id"], disabled["id"], recipient, token=sender.token, expect=409)

    expired = _create_link(
        card["id"],
        sender.token,
        expires_at="2001-01-01T00:00:00Z",
    )
    _send(card["id"], expired["id"], recipient, token=sender.token, expect=409)


def test_a_bad_address_and_an_overlong_note_are_rejected_without_an_echo(
    sender, recipient
):
    """Neither error body may contain what was sent: an error that repeats the
    address is an oracle, and one that repeats the note is reflected content."""
    card = _create_card("E6-BadInput", sender.token)
    link = _create_link(card["id"], sender.token)

    for bad in ("not-an-address", "a@b@c.de", "  ", "someone@"):
        result = _send(card["id"], link["id"], bad, token=sender.token, expect=400)
        if bad.strip():
            assert bad.strip() not in str(result)

    note = "x" * 5000
    result = _send(
        card["id"], link["id"], recipient, token=sender.token, message=note, expect=400
    )
    assert note not in str(result)
    assert recipient not in str(result)


def test_the_hourly_limit_blocks_the_next_send(mail_capture, recipient):
    """Rate-limited per sender, against the outbox, so it survives a restart.

    Its own account with its own allowance: the shared ``sender`` fixture would
    make this test depend on how many other tests ran first.
    """
    name = "e6ratelimited"
    password = f"{name}_pw_secure1"
    create_test_user(name, password, f"{name}@test.de")
    token = authorize_for_access_token(name, password)

    card = _create_card("E6-RateLimit", token)
    link = _create_link(card["id"], token)

    from checkcheckserver.config import Config

    limit = Config().SHARING_PUBLIC_LINK_EMAIL_MAX_PER_HOUR
    assert limit > 0, "the test instance is expected to enforce a limit"

    for index in range(limit):
        _send(card["id"], link["id"], f"ok-{index}-{recipient}", token=token)

    blocked = _send(
        card["id"], link["id"], recipient, token=token, expect=429
    )
    assert recipient not in str(blocked)


def test_the_gate_is_shut_on_an_instance_that_did_not_enable_it():
    """A process-level flag, so this is asserted against the dependency rather
    than over HTTP (same reason as the invite-accept flag)."""
    import checkcheckserver.api.routes.routes_checklist_share as share_routes
    from fastapi import HTTPException

    original = share_routes.config
    try:
        share_routes.config = _config(SHARING_PUBLIC_LINK_EMAIL_ENABLED=False)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(share_routes.require_public_link_email_enabled())
        assert exc.value.status_code == 404

        share_routes.config = _config(SHARING_PUBLIC_LINKS_ENABLED=False)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(share_routes.require_public_link_email_enabled())
        assert exc.value.status_code == 404

        # Enabled and configured: no exception.
        share_routes.config = _config()
        asyncio.run(share_routes.require_public_link_email_enabled())
    finally:
        share_routes.config = original


# ── what the client is told ───────────────────────────────────────────────────


def test_the_options_endpoint_reports_the_limits_and_the_internal_domains(sender):
    options = req("api/sharing/public-link-email-options", access_token=sender.token)
    assert options["enabled"] is True
    assert options["internal_email_domains"] == [INTERNAL_TEST_EMAIL_DOMAIN]
    assert options["max_message_length"] > 0
    assert options["max_per_hour"] >= 0


def test_the_options_endpoint_needs_a_session():
    """The domain list is the operator's information about their organisation,
    which is why it is not on the unauthenticated bootstrap endpoint."""
    req(
        "api/sharing/public-link-email-options",
        suppress_auth=True,
        expected_http_code=401,
        tolerated_error_codes=[401],
    )


def test_public_config_advertises_the_feature():
    config = req("api/public-config", suppress_auth=True)
    assert config["sharing_public_link_email_enabled"] is True
    # …and not the domains, which stay behind a session.
    assert "sharing_internal_email_domains" not in config
