"""The webhook channel: a small JSON POST to a URL the user chose (chunk E6).

The email channel sends to an address an administrator or an identity provider
put on the account. This one sends wherever a signed-in user says, which makes it
a **server-side request forgery primitive** unless it is guarded: without a
check, any account holder could point a webhook at ``http://127.0.0.1:5432`` or
at a cloud metadata service and use this server as a probe into networks only it
can reach.

Three things make that not so, and all three matter:

1. **Resolve, then judge.** ``NOTIFY_WEBHOOK_ALLOW_PRIVATE_IPS`` is false by
   default, and then every address a hostname resolves to must be a public
   unicast one. Checking the *hostname* would be useless: ``localtest.me``
   resolves to 127.0.0.1, and so does anything else an attacker controls DNS for.
   The judgement itself lives in ``notify/net_guard.py``, shared with the push
   channel so the two cannot drift.
2. **Connect to the address that was judged.** The request goes to the resolved
   IP with the original ``Host`` header (and, over TLS, the original name as SNI,
   so certificate verification is unchanged). Otherwise a name that answers
   "public address" to the check and "127.0.0.1" a millisecond later to the
   connection, which is a DNS rebinding attack and takes no special skill, walks
   straight past step 1.
3. **No redirects.** A 302 to ``http://169.254.169.254/`` would undo both of the
   above, so a redirect is a failed delivery rather than something to follow.

Delivery outcomes are classified like the email transports', because the outbox
applies the same retry policy to both: a timeout, a refused connection or a 5xx
is worth another attempt, a 4xx or a refused URL is not.

There is no request signature. The payload carries nothing secret (the same
sentence the recipient already has in their inbox and their feed), and a shared
secret per user is a feature with a settings surface of its own; a receiver that
needs authenticity today can put a token in the URL, which is why the URL is
never logged.
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse, urlunparse

import httpx

from checkcheckserver import __version__ as server_version
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.notify import net_guard

log = get_logger()


# Short on purpose: a webhook receiver that needs longer than this is doing work
# it should be doing after answering, and a slow one must not hold up the queue
# behind it. The attempt is retried, so a timeout costs a delay, not the message.
WEBHOOK_TIMEOUT_SECONDS = 10

# How much of a failing response is kept for the operator-facing error note.
MAX_ERROR_BODY_BYTES = 512


# ── errors ────────────────────────────────────────────────────────────────────


class WebhookDeliveryError(Exception):
    """Base class for a failed webhook attempt."""


class TransientWebhookError(WebhookDeliveryError):
    """The receiver may well answer on a later attempt. Retry it."""


class PermanentWebhookError(WebhookDeliveryError):
    """This request will never succeed as it stands. Do not retry it."""


class WebhookUrlRefused(PermanentWebhookError):
    """The target is not one this server is willing to call.

    Permanent by design: a URL that resolves into a private range now will
    almost certainly resolve there again, and retrying would turn a rejected
    webhook into a slow port scan.
    """


# ── the delivery ──────────────────────────────────────────────────────────────


@dataclass
class OutgoingWebhook:
    """One POST to one URL, before it becomes an HTTP request."""

    url: str
    body: dict
    headers: Dict[str, str] = field(default_factory=dict)


# ── the guard ─────────────────────────────────────────────────────────────────


# The judgement itself is channel-agnostic and shared with the push channel.
# Re-exported under the names this module has always used, so the guard reads
# the same here as it did before it moved.
_is_public_address = net_guard.is_public_address


async def _resolve(host: str, port: int) -> List[str]:
    """Every address *host* resolves to, as strings. Off the event loop.

    Only translates the shared resolver's failure into this channel's error
    vocabulary: a name that does not resolve is not obviously permanent (DNS
    breaks), so it is worth another attempt.
    """
    try:
        return await net_guard.resolve_addresses(host, port)
    except net_guard.HostResolutionError as exc:
        raise TransientWebhookError(f"Could not resolve the webhook host: {exc}") from exc


@dataclass
class WebhookTarget:
    """A checked URL and the single address the request will actually go to."""

    url: str
    address: str
    host_header: str
    hostname: str
    is_tls: bool


async def check_webhook_target(url: str, config: Config) -> WebhookTarget:
    """Verify a webhook URL and pin it to one resolved address.

    Raises :class:`WebhookUrlRefused` for anything this server will not call, and
    :class:`TransientWebhookError` when the name simply did not resolve right
    now. Returns the connection target, so the caller never has to resolve the
    name a second time (which is the window a rebinding attack needs).
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise WebhookUrlRefused("A webhook URL must be http:// or https://.")
    if parsed.username or parsed.password:
        # Credentials in a URL are a way to smuggle a different authority past a
        # careless reader, and nothing needs them here.
        raise WebhookUrlRefused("A webhook URL must not contain credentials.")
    hostname = parsed.hostname
    if not hostname:
        raise WebhookUrlRefused("A webhook URL must contain a host name.")

    is_tls = parsed.scheme == "https"
    port = parsed.port or (443 if is_tls else 80)
    addresses = await _resolve(hostname, port)
    if not addresses:
        raise TransientWebhookError("The webhook host resolved to no address.")

    if config.NOTIFY_WEBHOOK_ALLOW_PRIVATE_IPS:
        chosen = addresses[0]
    else:
        allowed = [a for a in addresses if _is_public_address(ipaddress.ip_address(a))]
        if not allowed:
            # Deliberately says nothing about which address it was: the answer is
            # the caller's own DNS to look up, and an error naming it would make
            # this endpoint a convenient resolver-with-a-verdict.
            raise WebhookUrlRefused(
                "That webhook URL points into a private or local network, which this "
                "server does not call."
            )
        chosen = allowed[0]

    return WebhookTarget(
        url=url,
        address=chosen,
        # Keep the port when it is not the scheme's default, exactly as a browser
        # would build the header.
        host_header=hostname if parsed.port is None else f"{hostname}:{parsed.port}",
        hostname=hostname,
        is_tls=is_tls,
    )


def _pinned_url(url: str, target: WebhookTarget) -> str:
    """The same URL with the host replaced by the address it resolved to."""
    parsed = urlparse(url)
    literal = target.address
    if ":" in literal:  # IPv6 literals need brackets in a URL
        literal = f"[{literal}]"
    netloc = literal if parsed.port is None else f"{literal}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


# ── the sender ────────────────────────────────────────────────────────────────


@runtime_checkable
class WebhookSender(Protocol):
    """How a webhook physically leaves the server.

    Mirrors ``EmailTransport``: raises a transient or a permanent error and
    returns None on success, leaving the retry decision to the outbox.
    """

    name: str

    async def send(self, webhook: OutgoingWebhook, config: Config) -> None: ...


class HttpxWebhookSender:
    """Posts the JSON body over HTTP, to the address the guard approved."""

    name = "httpx"

    async def send(self, webhook: OutgoingWebhook, config: Config) -> None:
        target = await check_webhook_target(webhook.url, config)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"{config.APP_NAME}/{server_version}",
            "Accept": "application/json, */*;q=0.5",
            "Host": target.host_header,
            **webhook.headers,
        }
        content = json.dumps(webhook.body).encode("utf-8")
        try:
            async with httpx.AsyncClient(
                timeout=WEBHOOK_TIMEOUT_SECONDS,
                # A redirect would leave the address the guard approved, so it is
                # an outcome rather than a step.
                follow_redirects=False,
            ) as client:
                request = client.build_request(
                    "POST",
                    _pinned_url(webhook.url, target),
                    content=content,
                    headers=headers,
                    # Verify the certificate against the *name* the user gave, not
                    # against the address we pinned to, so pinning changes nothing
                    # about TLS.
                    extensions=(
                        {"sni_hostname": target.hostname} if target.is_tls else {}
                    ),
                )
                response = await client.send(request, stream=True)
                try:
                    detail = await _error_detail(response)
                finally:
                    await response.aclose()
        except httpx.HTTPError as exc:
            raise TransientWebhookError(
                f"Could not deliver the webhook: {exc.__class__.__name__}: {exc}"
            ) from exc

        status_code = response.status_code
        if 200 <= status_code < 300:
            log.debug("[notify] webhook delivered, %s", status_code)
            return
        if status_code in (408, 425, 429) or status_code >= 500:
            raise TransientWebhookError(
                f"The webhook receiver answered {status_code}. {detail}".strip()
            )
        if 300 <= status_code < 400:
            raise PermanentWebhookError(
                f"The webhook receiver answered {status_code}; redirects are not "
                "followed. Point the webhook at the final URL."
            )
        raise PermanentWebhookError(
            f"The webhook receiver answered {status_code}. {detail}".strip()
        )


async def _error_detail(response: httpx.Response) -> str:
    """A short, bounded excerpt of a failing response, for the operator's log.

    Bounded because the body comes from an endpoint a user chose: reading all of
    whatever it decides to send would let one account holder spend this server's
    memory.
    """
    if 200 <= response.status_code < 300:
        return ""
    chunks = bytearray()
    try:
        async for chunk in response.aiter_bytes():
            chunks.extend(chunk)
            if len(chunks) >= MAX_ERROR_BODY_BYTES:
                break
    except httpx.HTTPError:
        return ""
    text = bytes(chunks[:MAX_ERROR_BODY_BYTES]).decode("utf-8", errors="replace").strip()
    return text.replace("\n", " ")[:MAX_ERROR_BODY_BYTES]


# ── selection ─────────────────────────────────────────────────────────────────

_default_sender = HttpxWebhookSender()
_override: Optional[WebhookSender] = None


def get_webhook_sender() -> WebhookSender:
    """The sender the application should use, honouring a test override."""
    return _override if _override is not None else _default_sender


def set_webhook_sender(sender: Optional[WebhookSender]) -> None:
    """Install a sender for every later :func:`get_webhook_sender` call.

    The webhook twin of ``transports.set_email_transport``: a test seam, and a
    debugging one.
    """
    global _override
    _override = sender


def reset_webhook_sender() -> None:
    set_webhook_sender(None)


class CapturingWebhookSender:
    """Keeps every webhook in memory instead of sending it. Tests only."""

    name = "capturing"

    def __init__(self):
        self.sent: List[OutgoingWebhook] = []
        self.raise_on_send: Optional[Exception] = None

    async def send(self, webhook: OutgoingWebhook, config: Config) -> None:
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.sent.append(webhook)

    def clear(self) -> None:
        self.sent.clear()

    @property
    def last(self) -> Optional[OutgoingWebhook]:
        return self.sent[-1] if self.sent else None
