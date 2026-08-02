"""One-click unsubscribe links: minting and verifying the signed token (chunk E4).

A link in an email cannot carry a session, so it carries a token instead:

    <payload>.<signature>

``payload`` is url-safe base64 of a tiny JSON object naming the user, the
notification type and an expiry; ``signature`` is an HMAC-SHA256 of that exact
payload string under the user's own ``unsubscribe_secret``. The token is signed,
not encrypted: anybody holding it can read whose it is, which is fine, because
they were sent the mail. What they cannot do is make a different one.

**Why a per-user secret** rather than one instance-wide key: rotating one user's
secret invalidates only their own outstanding links, and a leaked token cannot be
turned into a forgery machine for every other account. Reading the secret needs
the user id, which the payload carries, so verification is a two-step: parse the
claims, load that user's row, then check the signature against it.

The token grants exactly one thing, switching **one notification type's email
channel off** for **one user**. It is not a session, it cannot read anything, and
it cannot turn a notification back on.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()


# How long a link in an inbox keeps working. Long, because mail sits around for
# months and a dead unsubscribe link is worse than an old one: the recipient's
# next move is to mark the sender as spam. Not a config setting on purpose, there
# is nothing an operator would sensibly tune here.
TOKEN_TTL_DAYS = 90

UNSUBSCRIBE_PATH = "/api/notifications/unsubscribe"


class InvalidUnsubscribeToken(ValueError):
    """The token is malformed, forged, expired, or names nobody.

    Deliberately one error for all of those: the endpoint shows the same neutral
    page whatever went wrong, so a probe cannot learn whether an account exists.
    """


@dataclass(frozen=True)
class UnsubscribeClaims:
    user_id: uuid.UUID
    type: str
    expires_at: datetime.datetime


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256)
    return _b64encode(digest.digest())


def mint_token(
    *,
    user_id: uuid.UUID,
    type: str,
    secret: str,
    now: Optional[datetime.datetime] = None,
    ttl_days: int = TOKEN_TTL_DAYS,
) -> str:
    """A token that lets its holder switch off *type* on the email channel."""
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    expires_at = now + datetime.timedelta(days=ttl_days)
    payload = _b64encode(
        json.dumps(
            {
                "u": str(user_id),
                "t": type,
                "x": int(expires_at.replace(tzinfo=datetime.timezone.utc).timestamp()),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return f"{payload}.{_sign(payload, secret)}"


def read_claims(token: str) -> UnsubscribeClaims:
    """The claims a token *asserts*, with the signature not yet checked.

    Only for looking up whose secret to verify against. Never act on the result
    of this without calling :func:`verify_token` afterwards.
    """
    if not isinstance(token, str) or token.count(".") != 1:
        raise InvalidUnsubscribeToken("Malformed token.")
    payload, _, _signature = token.partition(".")
    try:
        claims = json.loads(_b64decode(payload))
        user_id = uuid.UUID(str(claims["u"]))
        type = str(claims["t"])
        expires_at = datetime.datetime.fromtimestamp(
            int(claims["x"]), datetime.timezone.utc
        ).replace(tzinfo=None)
    except Exception as exc:
        raise InvalidUnsubscribeToken("Unreadable token.") from exc
    return UnsubscribeClaims(user_id=user_id, type=type, expires_at=expires_at)


def verify_token(
    token: str, *, secret: str, now: Optional[datetime.datetime] = None
) -> UnsubscribeClaims:
    """Check a token against *secret* and return its claims, or raise.

    The signature is compared in constant time, and it covers the payload string
    verbatim rather than the decoded claims, so a re-encoding that happens to
    parse the same way still fails.
    """
    claims = read_claims(token)
    payload, _, signature = token.partition(".")
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        raise InvalidUnsubscribeToken("Bad signature.")
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if claims.expires_at <= now:
        raise InvalidUnsubscribeToken("Expired token.")
    return claims


def unsubscribe_url(
    *,
    user_id: uuid.UUID,
    type: str,
    secret: str,
    config: Config,
    now: Optional[datetime.datetime] = None,
) -> str:
    """The absolute link that goes into the message and its List-Unsubscribe header.

    Built from ``SERVER_PUBLIC_URL`` like every other link the server hands out,
    never from a request header.
    """
    token = mint_token(user_id=user_id, type=type, secret=secret, now=now)
    base = (config.SERVER_PUBLIC_URL or "").rstrip("/")
    return f"{base}{UNSUBSCRIBE_PATH}?token={quote(token)}"
