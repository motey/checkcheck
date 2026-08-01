"""Email transports: the last step before a message leaves the server.

A transport takes an :class:`OutgoingEmail` (recipient, subject, bodies, extra
headers), turns it into a MIME message with the instance's sender identity, and
delivers it somewhere. Which one is used comes from ``EMAIL_TRANSPORT``:

``smtp``
    Hands the message to a real mail server with aiosmtplib. The only
    production choice.
``console``
    Logs the whole message. Local development without a mail server.
``file``
    Writes an ``.eml`` file into ``EMAIL_FILE_TRANSPORT_DIR``, openable in any
    mail client. Local development and manual inspection.
``null``
    Discards the message. Also what you get while ``EMAIL_ENABLED`` is false, so
    the master switch holds even if a caller forgets to check it.

Failures are classified, because the dispatcher (chunk E2) retries one kind and
not the other: :class:`TransientEmailError` is worth another attempt later (the
mail server was down, the connection timed out, a 4xx greylisting response),
:class:`PermanentEmailError` is not (the address was rejected, the server said
5xx). Anything unexpected is treated as transient by the caller, since retrying
a message a few times is cheaper than dropping it.

Tests use :class:`CapturingEmailTransport` through the ``mail_capture`` fixture:
it keeps every message in memory and can be told to fail on demand. It is
deliberately not selectable through config, it is a test seam only.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import message_from_bytes
from email import policy as email_policy
from email.message import EmailMessage as MimeMessage
from email.utils import formataddr, format_datetime, make_msgid, parseaddr
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Tuple, runtime_checkable
from urllib.parse import urlparse

import aiosmtplib

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

log = get_logger()


# ── errors ────────────────────────────────────────────────────────────────────


class EmailDeliveryError(Exception):
    """Base class for a failed delivery attempt."""


class TransientEmailError(EmailDeliveryError):
    """The message may well go through on a later attempt. Retry it."""


class PermanentEmailError(EmailDeliveryError):
    """This message will never go through. Do not retry it."""


# ── the message ───────────────────────────────────────────────────────────────


@dataclass
class OutgoingEmail:
    """One message to one recipient, before it becomes MIME.

    ``message_id`` is passed in by the caller so a retried delivery reuses the
    same id and mail clients recognise the duplicate instead of showing the
    message twice. Left unset, a fresh one is generated. ``headers`` carries the
    extras later chunks need (``List-Unsubscribe``, ``References`` keyed on the
    card id so a mail client threads everything about one card together).
    """

    to: str
    subject: str
    text_body: str
    html_body: Optional[str] = None
    message_id: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)


def message_id_domain(config: Config) -> str:
    """The domain part of generated Message-IDs.

    Taken from the public URL, falling back to the sender address, so the id
    looks like it belongs to this instance rather than to the machine's
    hostname (which is a container id in most deployments).
    """
    host = urlparse(config.SERVER_PUBLIC_URL or "").hostname
    if not host:
        _, address = parseaddr(config.EMAIL_FROM_ADDRESS or "")
        host = address.partition("@")[2] or "localhost"
    return host


def stable_message_id(key: str, config: Config) -> str:
    """A Message-ID derived from *key*, identical across retries of that message.

    *key* must be unique per message (the outbox row id, from chunk E2 on).
    """
    return f"<{key}@{message_id_domain(config)}>"


def build_mime_message(email: OutgoingEmail, config: Config) -> MimeMessage:
    """Render *email* into a MIME message using the instance's sender identity.

    Always multipart/alternative when an HTML body is present, so text-only
    clients still get something readable. ``Auto-Submitted`` marks the message
    as machine-generated (RFC 3834), which keeps out-of-office autoresponders
    from replying to it.
    """
    mime = MimeMessage()
    mime["From"] = formataddr((config.EMAIL_FROM_NAME, config.EMAIL_FROM_ADDRESS or ""))
    mime["To"] = email.to
    mime["Subject"] = email.subject
    mime["Date"] = format_datetime(datetime.now(timezone.utc))
    mime["Message-ID"] = email.message_id or make_msgid(
        domain=message_id_domain(config)
    )
    if config.EMAIL_REPLY_TO:
        mime["Reply-To"] = config.EMAIL_REPLY_TO
    mime["Auto-Submitted"] = "auto-generated"
    for name, value in email.headers.items():
        # A caller-supplied header wins over the defaults above (an explicit
        # Reply-To for a specific message, say), so replace instead of appending
        # a second copy of the same header.
        if name in mime:
            del mime[name]
        mime[name] = value

    mime.set_content(email.text_body)
    if email.html_body:
        mime.add_alternative(email.html_body, subtype="html")
    return mime


# ── the transport interface ───────────────────────────────────────────────────


@runtime_checkable
class EmailTransport(Protocol):
    """How a message physically leaves the server.

    Implementations raise :class:`TransientEmailError` or
    :class:`PermanentEmailError` and return None on success. They never swallow
    a failure: deciding whether to retry is the dispatcher's job.
    """

    name: str

    async def send(self, email: OutgoingEmail) -> None: ...


class NullEmailTransport:
    """Throws the message away. Used when mail is switched off."""

    name = "null"

    def __init__(self, config: Optional[Config] = None):
        self._config = config or Config()

    async def send(self, email: OutgoingEmail) -> None:
        log.debug(
            "Email transport 'null': dropping message to %s (%s)",
            email.to,
            email.subject,
        )


class ConsoleEmailTransport:
    """Logs the rendered message. Local development without a mail server."""

    name = "console"

    def __init__(self, config: Optional[Config] = None):
        self._config = config or Config()

    async def send(self, email: OutgoingEmail) -> None:
        mime = build_mime_message(email, self._config)
        log.info(
            "Email transport 'console': message to %s\n%s",
            email.to,
            mime.as_string(),
        )


class FileEmailTransport:
    """Writes each message as an ``.eml`` file, openable in any mail client.

    The directory comes from ``EMAIL_FILE_TRANSPORT_DIR`` and is created on
    first use. Filenames start with a UTC timestamp so they sort chronologically.
    """

    name = "file"

    def __init__(self, config: Optional[Config] = None):
        self._config = config or Config()

    @property
    def directory(self) -> Path:
        return Path(self._config.EMAIL_FILE_TRANSPORT_DIR)

    async def send(self, email: OutgoingEmail) -> None:
        mime = build_mime_message(email, self._config)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        recipient = "".join(c if c.isalnum() else "_" for c in email.to)[:40]
        path = self.directory / f"{stamp}-{recipient}-{uuid.uuid4().hex[:8]}.eml"
        try:
            # Blocking IO, so keep it off the event loop.
            await asyncio.to_thread(self._write, path, mime.as_bytes())
        except OSError as exc:
            # A missing or unwritable directory is a configuration problem, not
            # something a retry fixes, but it is also not the message's fault:
            # treat it as transient so the operator can fix the mount and the
            # queued mail still goes out.
            raise TransientEmailError(
                f"Could not write message to {path}: {exc}"
            ) from exc
        log.debug("Email transport 'file': wrote message to %s", path)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _is_transient_code(code: int) -> bool:
    """SMTP 4xx means "not now, try later"; everything else is final."""
    return 400 <= code < 500


def _classified(transient: bool, message: str) -> EmailDeliveryError:
    return TransientEmailError(message) if transient else PermanentEmailError(message)


class SmtpEmailTransport:
    """Hands the message to a real mail server over SMTP.

    Connection security follows ``EMAIL_SMTP_SECURITY``: ``starttls`` connects
    in plain text and upgrades (port 587), ``ssl`` is TLS from the first byte
    (port 465), ``none`` stays unencrypted (a mail server on localhost). A
    username without a password, or the other way round, is treated as no
    authentication at all, which is what an open relay on a private network
    wants.
    """

    name = "smtp"

    def __init__(self, config: Optional[Config] = None):
        self._config = config or Config()

    async def send(self, email: OutgoingEmail) -> None:
        config = self._config
        mime = build_mime_message(email, config)
        security = config.EMAIL_SMTP_SECURITY
        password = (
            config.EMAIL_SMTP_PASSWORD.get_secret_value()
            if config.EMAIL_SMTP_PASSWORD is not None
            else None
        )
        try:
            await aiosmtplib.send(
                mime,
                hostname=config.EMAIL_SMTP_HOST,
                port=config.EMAIL_SMTP_PORT,
                username=config.EMAIL_SMTP_USER or None,
                password=password if config.EMAIL_SMTP_USER else None,
                use_tls=security == "ssl",
                start_tls=True if security == "starttls" else False,
                timeout=config.EMAIL_TIMEOUT_SECONDS,
            )
        except aiosmtplib.SMTPRecipientsRefused as exc:
            # Every recipient was rejected. 4xx on all of them is a deferral
            # (greylisting, mailbox busy), anything else means the address is
            # not going to start working.
            codes = [refusal.code for refusal in exc.recipients]
            raise _classified(
                codes and all(_is_transient_code(code) for code in codes),
                f"Mail server rejected the recipient {email.to}: {exc}",
            ) from exc
        except aiosmtplib.SMTPResponseException as exc:
            # Covers the sender, HELO, DATA and auth refusals: 5xx is a refusal,
            # 4xx a "try again later".
            raise _classified(
                _is_transient_code(exc.code),
                f"Mail server refused the message to {email.to}: "
                f"{exc.code} {exc.message}",
            ) from exc
        except (aiosmtplib.SMTPException, OSError, asyncio.TimeoutError) as exc:
            raise TransientEmailError(
                f"Could not deliver the message to {email.to}: {exc}"
            ) from exc
        log.debug("Email transport 'smtp': sent message to %s", email.to)


class CapturingEmailTransport:
    """Keeps every message in memory instead of sending it. Tests only.

    Not selectable through ``EMAIL_TRANSPORT``: tests install it with
    :func:`set_email_transport` (see the ``mail_capture`` fixture). Set
    ``raise_on_send`` to make the next send fail, which is how the retry and
    dead-letter behaviour of the dispatcher gets exercised.
    """

    name = "capturing"

    def __init__(self, config: Optional[Config] = None):
        self._config = config or Config()
        self.sent: List[OutgoingEmail] = []
        self.messages: List[MimeMessage] = []
        self.raise_on_send: Optional[Exception] = None

    async def send(self, email: OutgoingEmail) -> None:
        if self.raise_on_send is not None:
            raise self.raise_on_send
        # Render eagerly, so a message that would blow up on a real transport
        # blows up here too.
        self.messages.append(build_mime_message(email, self._config))
        self.sent.append(email)

    # ── assertion helpers ─────────────────────────────────────────────────────

    def clear(self) -> None:
        self.sent.clear()
        self.messages.clear()

    @property
    def last(self) -> Optional[OutgoingEmail]:
        return self.sent[-1] if self.sent else None

    def to(self, address: str) -> List[OutgoingEmail]:
        return [email for email in self.sent if email.to == address]


_TRANSPORTS = {
    "smtp": SmtpEmailTransport,
    "console": ConsoleEmailTransport,
    "file": FileEmailTransport,
    "null": NullEmailTransport,
}


# ── selection ─────────────────────────────────────────────────────────────────

_override: Optional[EmailTransport] = None


def build_email_transport(config: Optional[Config] = None) -> EmailTransport:
    """Build the transport ``EMAIL_TRANSPORT`` asks for.

    Returns the null transport whenever ``EMAIL_ENABLED`` is false, so the
    master switch cannot be defeated by a caller that forgets to check it.
    """
    config = config or Config()
    if not config.EMAIL_ENABLED:
        return NullEmailTransport(config)
    return _TRANSPORTS[config.EMAIL_TRANSPORT](config)


def get_email_transport(config: Optional[Config] = None) -> EmailTransport:
    """The transport the application should use, honouring a test override."""
    if _override is not None:
        return _override
    return build_email_transport(config)


def set_email_transport(transport: Optional[EmailTransport]) -> None:
    """Install a transport for every later :func:`get_email_transport` call.

    A test seam (and a debugging one). Pass None or call
    :func:`reset_email_transport` to go back to the configured transport.
    """
    global _override
    _override = transport


def reset_email_transport() -> None:
    set_email_transport(None)


def parse_mime_bytes(data: bytes) -> MimeMessage:
    """Parse raw message bytes back into a message. Test helper.

    ``policy=default`` is what makes the result an ``EmailMessage`` with
    ``get_body()`` and friends, rather than the legacy compat32 ``Message``.
    """
    return message_from_bytes(data, policy=email_policy.default)


def parse_eml(path: Path) -> MimeMessage:
    """Read back a file written by :class:`FileEmailTransport`. Test helper."""
    return parse_mime_bytes(path.read_bytes())


def mime_bodies(mime: MimeMessage) -> Tuple[str, Optional[str]]:
    """The plain-text and HTML bodies of a message. Test helper."""
    text_part = mime.get_body(preferencelist=("plain",))
    html_part = mime.get_body(preferencelist=("html",))
    return (
        text_part.get_content() if text_part is not None else "",
        html_part.get_content() if html_part is not None else None,
    )
