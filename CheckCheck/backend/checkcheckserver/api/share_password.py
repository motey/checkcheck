"""Passphrase protection for public share links (Phase 7 of card sharing).

A public link may carry an optional bcrypt-hashed passphrase. An anonymous
visitor proves knowledge of it once at ``POST /public/checklist/{token}/unlock``
and receives a short-lived **signed grant** (not the password) that they replay
on subsequent calls. Keeping the raw passphrase out of every request — and out of
URLs / SSE query strings / access logs — is the whole point of the grant; only
``/unlock`` ever sees the plaintext (in a POST body).

The grant is bound to both the link ``token`` and a fingerprint of the current
``password_hash``, so rotating or clearing the passphrase invalidates every
outstanding grant. It also carries its own signed timestamp (TTL below).

This module deliberately owns its **own** bcrypt context rather than importing the
one in ``model.user_auth`` — the dependency stays one-way (sharing depends on
nothing auth-internal).
"""

import hashlib

from itsdangerous import URLSafeTimedSerializer, BadData
from passlib.context import CryptContext

from checkcheckserver.config import Config

config = Config()

# Share passphrases are user-chosen secrets, so use a slow hash (bcrypt), same as
# local account passwords — but via a context this module owns (one-way dep).
_crypt_context_share_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

# A grant is valid for an hour; after that the visitor unlocks again.
GRANT_TTL_SECONDS = 60 * 60

_GRANT_SALT = "checklist-public-share-grant"
_serializer = URLSafeTimedSerializer(
    config.SERVER_SESSION_SECRET.get_secret_value(), salt=_GRANT_SALT
)


def hash_share_password(password: str) -> str:
    """Hash a plaintext share passphrase. Never store or log the plaintext."""
    return _crypt_context_share_pwd.hash(password)


def verify_share_password(password: str, password_hash: str) -> bool:
    """Constant-time check of a passphrase against its bcrypt hash."""
    if not password or not password_hash:
        return False
    return _crypt_context_share_pwd.verify(password, password_hash)


def _password_fingerprint(password_hash: str) -> str:
    """Short, non-reversible fingerprint of the stored hash. Embedded in a grant
    so changing/clearing the passphrase invalidates previously issued grants."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def make_share_grant(token: str, password_hash: str) -> str:
    """Mint a signed grant proving the holder unlocked ``token``'s passphrase."""
    return _serializer.dumps(
        {"t": token, "f": _password_fingerprint(password_hash)}
    )


def verify_share_grant(
    grant: str | None, token: str, password_hash: str
) -> bool:
    """True iff ``grant`` is a valid, unexpired grant for this token *and* the
    current passphrase (a rotated/cleared passphrase fails the fingerprint check)."""
    if not grant or not password_hash:
        return False
    try:
        data = _serializer.loads(grant, max_age=GRANT_TTL_SECONDS)
    except BadData:
        return False
    return (
        isinstance(data, dict)
        and data.get("t") == token
        and data.get("f") == _password_fingerprint(password_hash)
    )
