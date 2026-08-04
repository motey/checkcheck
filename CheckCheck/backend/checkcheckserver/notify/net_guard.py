"""Whether this server is willing to make an outbound request to a target a
user chose (chunk N2 of ``docs/plans/NOTIFICATIONS_REWORK.md``).

Two channels send where a signed-in user points them: the webhook channel
(``notify/webhooks.py``) and the push channel (``notify/push.py``, whose
endpoint arrives from the browser but is only ever a string in a request body).
Both are server-side request forgery primitives unless the target is judged,
and both have to judge it the same way, so the judgement lives here rather than
in either one.

**Resolve, then judge.** The check is on every address the host name resolves
to, never on the name: ``localtest.me`` resolves to 127.0.0.1, and so does
anything else an attacker controls DNS for. Everything that is not ordinary
routable unicast is refused: loopback, private ranges, link-local (which is
where cloud metadata services live), multicast, reserved blocks and the
unspecified address.

**What this module does not do.** It does not connect, so it cannot pin the
request to the address it approved. Closing that window is the caller's job and
only one caller manages it: ``notify/webhooks.py`` connects to the resolved
address with the original ``Host`` header and refuses redirects, which is what
makes its guard hold against DNS rebinding. The push channel hands the endpoint
to ``pywebpush``, which resolves it again itself; see that module's docstring
for what that costs.

**Note on the module import order.** ``notify/push.py`` is imported from inside
``Config``'s own boot-time validator, so anything it imports (this module
included) must never import ``checkcheckserver.log`` or ``checkcheckserver.config``:
both do ``Config()`` at import time and would recurse into a second,
partially-initialised ``Config`` construction. Plain ``logging.getLogger``
instead, and no config parameter here (an operator switch that relaxes the
judgement, like the webhook channel's, stays with the channel that offers it).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import List, Tuple
from urllib.parse import urlparse


log = logging.getLogger("CheckCheck")


class AddressRefused(Exception):
    """This server will not send to that target.

    The message is written to be safe to hand back to the caller: it says what
    kind of thing was wrong and never which address the name resolved to. The
    resolution result is an information leak in its own right, and an error
    naming it would turn the endpoint into a convenient resolver-with-a-verdict.
    """


class HostResolutionError(Exception):
    """The name did not resolve right now.

    Kept apart from :class:`AddressRefused` because it is not a verdict: DNS
    breaks, and a caller with a retry budget should usually spend one on this
    rather than treat the target as refused for good.
    """


def is_public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether this server is willing to send a user's message to *ip*.

    An IPv4 address tunnelled inside IPv6 is unwrapped first, since
    ``::ffff:127.0.0.1`` is loopback however it is spelled.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _getaddrinfo(host: str, port: int) -> List[str]:
    try:
        infos = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as exc:
        raise HostResolutionError(str(exc)) from exc
    return [info[4][0] for info in infos]


async def resolve_addresses(host: str, port: int) -> List[str]:
    """Every address *host* resolves to, as strings. Off the event loop."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as exc:
        raise HostResolutionError(str(exc)) from exc
    return [info[4][0] for info in infos]


def resolve_addresses_sync(host: str, port: int) -> List[str]:
    """The blocking twin of :func:`resolve_addresses`.

    For a caller that is already off the event loop in a worker thread, which
    is where the push sender re-checks its endpoint.
    """
    return _getaddrinfo(host, port)


def parse_https_target(url: str) -> Tuple[str, int]:
    """The host and port of *url*, or raise :class:`AddressRefused`.

    Stricter than the webhook channel's parser on purpose: this one is for
    targets that must be ``https://``, which every real Web Push service is.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https":
        raise AddressRefused("The endpoint must be an https:// URL.")
    if parsed.username or parsed.password:
        # Credentials in a URL are a way to smuggle a different authority past
        # a careless reader, and nothing needs them here.
        raise AddressRefused("The endpoint must not contain credentials.")
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:  # an unparseable port, for instance
        raise AddressRefused("The endpoint is not a usable URL.") from exc
    if not hostname:
        raise AddressRefused("The endpoint must contain a host name.")
    return hostname, port or 443


def judge_addresses(addresses: List[str], *, what: str) -> None:
    """Raise :class:`AddressRefused` unless every address is public unicast.

    *what* names the target in the log line only, never in the exception.
    """
    if not addresses:
        raise HostResolutionError("the host resolved to no address")
    private = [a for a in addresses if not is_public_address(ipaddress.ip_address(a))]
    if private:
        # The addresses go to the operator's debug log and nowhere else.
        log.debug(
            "[notify] refusing %s: resolved into a non-public range (%s)",
            what,
            ", ".join(private),
        )
        raise AddressRefused(
            "That endpoint points into a private or local network, which this "
            "server does not send to."
        )


async def require_public_https_target(url: str, *, what: str) -> None:
    """Judge *url*, from async code.

    Raises :class:`AddressRefused` when the URL or the addresses behind it are
    not something this server will send to, and :class:`HostResolutionError`
    when the name simply did not resolve right now.
    """
    hostname, port = parse_https_target(url)
    judge_addresses(await resolve_addresses(hostname, port), what=what)


def require_public_https_target_sync(url: str, *, what: str) -> None:
    """The blocking twin of :func:`require_public_https_target`."""
    hostname, port = parse_https_target(url)
    judge_addresses(resolve_addresses_sync(hostname, port), what=what)
