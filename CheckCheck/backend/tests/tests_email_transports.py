"""Tests for chunk E1 of the notification sub-project: config, transports, harness.

Nothing in the app sends mail yet, so unlike the sibling modules these tests do
not talk to the live server over HTTP. They exercise the transports in this
process:

* which transport ``EMAIL_TRANSPORT`` selects, and that ``EMAIL_ENABLED=false``
  really means "nothing leaves the server",
* the startup validation that stops a half-configured mailer from booting,
* the MIME message the transports build (multipart, headers, stable Message-ID),
* the ``console`` and ``file`` transports,
* a genuine SMTP round trip against an in-process ``aiosmtpd`` server, so the
  real client code path is covered rather than mocked, including how a refused
  message is classified into retry-worthy and hopeless.

The plan is ``docs/plans/EMAIL_NOTIFICATIONS.md``.
"""

import asyncio
import logging
from typing import List

import pytest
from pydantic import ValidationError

from checkcheckserver.config import Config
from checkcheckserver.notify.transports import (
    CapturingEmailTransport,
    ConsoleEmailTransport,
    FileEmailTransport,
    NullEmailTransport,
    OutgoingEmail,
    PermanentEmailError,
    SmtpEmailTransport,
    TransientEmailError,
    build_email_transport,
    build_mime_message,
    get_email_transport,
    mime_bodies,
    parse_eml,
    parse_mime_bytes,
    reset_email_transport,
    set_email_transport,
    stable_message_id,
)

FROM_ADDRESS = "checkcheck@example.com"


# ── helpers ───────────────────────────────────────────────────────────────────


def _config(**overrides) -> Config:
    """A Config with mail switched on, minimally configured, plus overrides."""
    settings = {
        "EMAIL_ENABLED": True,
        "EMAIL_TRANSPORT": "null",
        "EMAIL_FROM_ADDRESS": FROM_ADDRESS,
    }
    settings.update(overrides)
    return Config(**settings)


def _email(**overrides) -> OutgoingEmail:
    fields = {
        "to": "recipient@example.com",
        "subject": "Anna shared 'Groceries' with you",
        "text_body": "Anna shared a card with you.",
        "html_body": "<p>Anna shared a card with you.</p>",
    }
    fields.update(overrides)
    return OutgoingEmail(**fields)


def _send(transport, email: OutgoingEmail) -> None:
    """Run one send to completion. The suite has no async test support."""
    asyncio.run(transport.send(email))


# ── transport selection ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "transport_name,expected_class",
    [
        ("smtp", SmtpEmailTransport),
        ("console", ConsoleEmailTransport),
        ("file", FileEmailTransport),
        ("null", NullEmailTransport),
    ],
)
def test_transport_selection_follows_config(transport_name, expected_class):
    """EMAIL_TRANSPORT picks the implementation, one for one."""
    config = _config(EMAIL_TRANSPORT=transport_name, EMAIL_SMTP_HOST="localhost")
    assert isinstance(build_email_transport(config), expected_class)


def test_disabled_email_always_yields_the_null_transport():
    """The master switch wins over EMAIL_TRANSPORT: with EMAIL_ENABLED off, even
    a fully configured SMTP setup delivers nowhere."""
    config = _config(
        EMAIL_ENABLED=False,
        EMAIL_TRANSPORT="smtp",
        EMAIL_SMTP_HOST="smtp.example.com",
    )
    assert isinstance(build_email_transport(config), NullEmailTransport)


def test_get_email_transport_honours_the_test_override():
    """set_email_transport replaces the configured transport until it is reset."""
    capturing = CapturingEmailTransport(_config())
    set_email_transport(capturing)
    try:
        assert get_email_transport(_config(EMAIL_TRANSPORT="console")) is capturing
    finally:
        reset_email_transport()
    assert isinstance(
        get_email_transport(_config(EMAIL_TRANSPORT="console")), ConsoleEmailTransport
    )


# ── startup validation ────────────────────────────────────────────────────────


def test_enabled_without_sender_address_fails_at_startup():
    """EMAIL_ENABLED without EMAIL_FROM_ADDRESS must not boot: mail that silently
    fails at send time, once per message, in a background task, is worse."""
    with pytest.raises(ValidationError) as excinfo:
        Config(EMAIL_ENABLED=True, EMAIL_FROM_ADDRESS=None, EMAIL_TRANSPORT="console")
    assert "EMAIL_FROM_ADDRESS" in str(excinfo.value)


def test_smtp_transport_without_host_fails_at_startup():
    with pytest.raises(ValidationError) as excinfo:
        Config(
            EMAIL_ENABLED=True,
            EMAIL_FROM_ADDRESS=FROM_ADDRESS,
            EMAIL_TRANSPORT="smtp",
            EMAIL_SMTP_HOST=None,
        )
    assert "EMAIL_SMTP_HOST" in str(excinfo.value)


def test_non_smtp_transports_need_no_smtp_host():
    """console/file/null are for local development, where there is no mail server."""
    for transport_name in ("console", "file", "null"):
        config = Config(
            EMAIL_ENABLED=True,
            EMAIL_FROM_ADDRESS=FROM_ADDRESS,
            EMAIL_TRANSPORT=transport_name,
            EMAIL_SMTP_HOST=None,
        )
        assert config.EMAIL_TRANSPORT == transport_name


def test_disabled_email_needs_no_settings_at_all():
    """The default (mail off) must never block a boot."""
    config = Config(EMAIL_ENABLED=False, EMAIL_FROM_ADDRESS=None, EMAIL_SMTP_HOST=None)
    assert config.EMAIL_ENABLED is False


def test_sender_display_name_falls_back_to_app_name():
    assert _config(APP_NAME="My Lists").EMAIL_FROM_NAME == "My Lists"
    assert _config(EMAIL_FROM_NAME="Something Else").EMAIL_FROM_NAME == "Something Else"


# ── the MIME message ──────────────────────────────────────────────────────────


def test_message_is_multipart_with_both_bodies_and_the_expected_headers():
    config = _config(EMAIL_FROM_NAME="CheckCheck", EMAIL_REPLY_TO="support@example.com")
    mime = build_mime_message(_email(), config)

    assert mime.get_content_type() == "multipart/alternative"
    text, html = mime_bodies(mime)
    assert "Anna shared a card with you." in text
    assert "<p>Anna shared a card with you.</p>" in html

    assert mime["From"] == f"CheckCheck <{FROM_ADDRESS}>"
    assert mime["To"] == "recipient@example.com"
    assert mime["Subject"] == "Anna shared 'Groceries' with you"
    assert mime["Reply-To"] == "support@example.com"
    assert mime["Date"]
    # Machine-generated, so autoresponders leave it alone (RFC 3834).
    assert mime["Auto-Submitted"] == "auto-generated"
    # A Message-ID is always present, and looks like it belongs to this instance.
    assert mime["Message-ID"].startswith("<") and mime["Message-ID"].endswith(">")
    assert "localhost" in mime["Message-ID"]


def test_message_without_html_stays_plain_text():
    mime = build_mime_message(_email(html_body=None), _config())
    assert mime.get_content_type() == "text/plain"
    text, html = mime_bodies(mime)
    assert "Anna shared a card with you." in text
    assert html is None


def test_reply_to_is_omitted_when_not_configured():
    assert build_mime_message(_email(), _config())["Reply-To"] is None


def test_extra_headers_are_added_and_override_the_defaults():
    """Later chunks attach List-Unsubscribe and References (card threading), and
    must be able to replace a default rather than emit the header twice."""
    email = _email(
        headers={
            "List-Unsubscribe": "<https://example.com/unsubscribe?t=abc>",
            "References": "<card-42@localhost>",
            "Reply-To": "per-message@example.com",
        }
    )
    mime = build_mime_message(email, _config(EMAIL_REPLY_TO="support@example.com"))

    assert mime["List-Unsubscribe"] == "<https://example.com/unsubscribe?t=abc>"
    assert mime["References"] == "<card-42@localhost>"
    assert mime.get_all("Reply-To") == ["per-message@example.com"]


def test_message_id_is_stable_when_supplied():
    """A retried delivery reuses the id, so a mail client can spot the duplicate."""
    config = _config()
    message_id = stable_message_id("outbox-row-1", config)
    first = build_mime_message(_email(message_id=message_id), config)
    second = build_mime_message(_email(message_id=message_id), config)
    assert first["Message-ID"] == second["Message-ID"] == message_id
    # Without one, each build gets its own id.
    assert (
        build_mime_message(_email(), config)["Message-ID"]
        != build_mime_message(_email(), config)["Message-ID"]
    )


# ── console / file / null / capturing ─────────────────────────────────────────


def test_console_transport_logs_the_whole_message(caplog):
    with caplog.at_level(logging.INFO, logger="CheckCheck"):
        _send(ConsoleEmailTransport(_config(EMAIL_TRANSPORT="console")), _email())
    logged = caplog.text
    assert "recipient@example.com" in logged
    assert "Anna shared a card with you." in logged


def test_file_transport_writes_a_readable_eml(tmp_path):
    config = _config(EMAIL_TRANSPORT="file", EMAIL_FILE_TRANSPORT_DIR=str(tmp_path / "mail"))
    _send(FileEmailTransport(config), _email())

    files = sorted((tmp_path / "mail").glob("*.eml"))
    assert len(files) == 1, f"expected exactly one .eml, got {files}"
    mime = parse_eml(files[0])
    assert mime["To"] == "recipient@example.com"
    assert mime["Subject"] == "Anna shared 'Groceries' with you"
    text, html = mime_bodies(mime)
    assert "Anna shared a card with you." in text
    assert "<p>Anna shared a card with you.</p>" in html


def test_file_transport_reports_an_unwritable_directory_as_retryable(tmp_path):
    """A broken mail directory is the operator's problem to fix, so the message
    is kept for a later attempt rather than discarded."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    config = _config(
        EMAIL_TRANSPORT="file", EMAIL_FILE_TRANSPORT_DIR=str(blocker / "mail")
    )
    with pytest.raises(TransientEmailError):
        _send(FileEmailTransport(config), _email())


def test_null_transport_swallows_the_message():
    _send(NullEmailTransport(_config(EMAIL_TRANSPORT="null")), _email())


def test_mail_capture_fixture_records_messages(mail_capture):
    """The harness every later chunk asserts on."""
    _send(get_email_transport(), _email())

    assert len(mail_capture.sent) == 1
    assert mail_capture.last.to == "recipient@example.com"
    assert mail_capture.to("recipient@example.com")
    assert mail_capture.to("someone-else@example.com") == []
    # The message is rendered on capture, so a broken template fails here too.
    assert mail_capture.messages[0].get_content_type() == "multipart/alternative"

    mail_capture.clear()
    assert mail_capture.sent == [] and mail_capture.last is None


def test_mail_capture_can_simulate_a_failing_transport(mail_capture):
    """How the dispatcher's retry and dead-letter paths get exercised in E2."""
    mail_capture.raise_on_send = TransientEmailError("mail server is down")
    with pytest.raises(TransientEmailError):
        _send(mail_capture, _email())
    assert mail_capture.sent == []

    mail_capture.raise_on_send = None
    _send(mail_capture, _email())
    assert len(mail_capture.sent) == 1


# ── real SMTP round trip ──────────────────────────────────────────────────────


class _RecordingHandler:
    """aiosmtpd handler that keeps every accepted message."""

    def __init__(self):
        self.envelopes: List = []

    async def handle_DATA(self, server, session, envelope):
        self.envelopes.append(envelope)
        return "250 Message accepted for delivery"


class _RefusingHandler:
    """aiosmtpd handler that refuses every recipient with a fixed status."""

    def __init__(self, response: str):
        self._response = response

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        return self._response


def _free_port() -> int:
    """Grab a port the OS just handed out. aiosmtpd 1.4 cannot bind port 0: its
    startup self-test connects to the port it was given, so it has to be real."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _LocalSmtpServer:
    """An in-process SMTP server on a free port, for the duration of a test."""

    def __init__(self, handler):
        from aiosmtpd.controller import Controller

        self._controller = Controller(handler, hostname="127.0.0.1", port=_free_port())
        self.handler = handler

    def __enter__(self) -> "_LocalSmtpServer":
        self._controller.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._controller.stop()

    @property
    def port(self) -> int:
        return self._controller.port

    def config(self, **overrides) -> Config:
        return _config(
            EMAIL_TRANSPORT="smtp",
            EMAIL_SMTP_HOST="127.0.0.1",
            EMAIL_SMTP_PORT=self.port,
            # The test server speaks no TLS, which is also the only sane setting
            # for a mail server on localhost.
            EMAIL_SMTP_SECURITY="none",
            EMAIL_TIMEOUT_SECONDS=10,
            **overrides,
        )


def test_smtp_transport_delivers_to_a_real_server():
    """The genuine SMTP path: aiosmtplib to an in-process server, message parsed
    back out of the envelope."""
    with _LocalSmtpServer(_RecordingHandler()) as server:
        _send(SmtpEmailTransport(server.config()), _email())

        assert len(server.handler.envelopes) == 1
        envelope = server.handler.envelopes[0]
        assert envelope.mail_from == FROM_ADDRESS
        assert envelope.rcpt_tos == ["recipient@example.com"]

        mime = parse_mime_bytes(envelope.content)
        assert mime["Subject"] == "Anna shared 'Groceries' with you"
        assert mime["From"] == f"CheckCheck <{FROM_ADDRESS}>"
        text, html = mime_bodies(mime)
        assert "Anna shared a card with you." in text
        assert "<p>Anna shared a card with you.</p>" in html


def test_smtp_transport_treats_a_rejected_recipient_as_permanent():
    """5xx on the recipient: the address is not going to start working, so the
    dispatcher must not retry it."""
    with _LocalSmtpServer(_RefusingHandler("550 No such user here")) as server:
        with pytest.raises(PermanentEmailError):
            _send(SmtpEmailTransport(server.config()), _email())


def test_smtp_transport_treats_a_deferred_recipient_as_transient():
    """4xx is greylisting or a busy mailbox: worth another attempt later."""
    with _LocalSmtpServer(_RefusingHandler("451 Try again later")) as server:
        with pytest.raises(TransientEmailError):
            _send(SmtpEmailTransport(server.config()), _email())


def test_smtp_transport_treats_an_unreachable_server_as_transient():
    """Mail server down or the wrong port: keep the message and retry."""
    with _LocalSmtpServer(_RecordingHandler()) as server:
        dead_port = server.port
    # The server is stopped, so nothing listens on that port any more.
    config = _config(
        EMAIL_TRANSPORT="smtp",
        EMAIL_SMTP_HOST="127.0.0.1",
        EMAIL_SMTP_PORT=dead_port,
        EMAIL_SMTP_SECURITY="none",
        EMAIL_TIMEOUT_SECONDS=5,
    )
    with pytest.raises(TransientEmailError):
        _send(SmtpEmailTransport(config), _email())
