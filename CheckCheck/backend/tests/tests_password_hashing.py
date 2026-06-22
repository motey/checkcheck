"""Unit tests for the hashing primitives (``model/_hashing.py``) and the
``UserAuth`` secret helpers.

These import the backend directly (no HTTP) — the conftest already puts the
backend dir on ``sys.path`` and sets the test env during collection. They pin
the behaviour the passlib→pwdlib migration relies on:

  * passwords use argon2 (slow, internally salted, no length limit)
  * API tokens use SHA-256 (fast, fine for high-entropy random tokens)
  * the old per-row ``salt`` column is no longer part of hashing
"""

import uuid

from checkcheckserver.model import _hashing
from checkcheckserver.model.user_auth import UserAuth, AllowedAuthSchemeType


# ── password hashing (argon2) ─────────────────────────────────────────────────

def test_password_roundtrip_is_argon2():
    h = _hashing.hash_password("correct horse battery staple")
    assert h.startswith("$argon2"), f"expected argon2 hash, got {h[:16]!r}"
    assert _hashing.verify_password("correct horse battery staple", h)
    assert not _hashing.verify_password("wrong", h)


def test_password_verify_handles_missing_sides():
    h = _hashing.hash_password("whatever-123")
    assert not _hashing.verify_password(None, h)
    assert not _hashing.verify_password("", h)
    assert not _hashing.verify_password("whatever-123", None)


def test_password_hashes_are_uniquely_salted():
    """argon2 salts internally, so the same password hashes differently each
    time — which is exactly why the old manual add_salt was redundant."""
    pw = "same-password-twice"
    assert _hashing.hash_password(pw) != _hashing.hash_password(pw)


def test_long_password_over_72_bytes():
    """The original bug: bcrypt rejects >72 bytes. argon2 must accept it and
    must not silently truncate (a 72-byte prefix must not verify)."""
    long_pw = "p" * 200
    h = _hashing.hash_password(long_pw)
    assert _hashing.verify_password(long_pw, h)
    assert not _hashing.verify_password(long_pw[:72], h)


# ── API token hashing (sha256) ────────────────────────────────────────────────

def test_api_token_roundtrip_is_sha256():
    token = "abc123-some-high-entropy-token-value-xyz"
    h = _hashing.hash_api_token(token)
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
    assert _hashing.verify_api_token(token, h)
    assert not _hashing.verify_api_token("other", h)


def test_api_token_verify_handles_missing_sides():
    h = _hashing.hash_api_token("token-value")
    assert not _hashing.verify_api_token(None, h)
    assert not _hashing.verify_api_token("token-value", None)


# ── UserAuth no longer depends on the per-row salt ────────────────────────────

def test_userauth_password_ignores_salt_column():
    ua = UserAuth(user_id=uuid.uuid4(), auth_source_type=AllowedAuthSchemeType.basic)
    ua.set_password("s3cure-passphrase")
    # Mutating the legacy salt must not affect verification anymore.
    ua.salt = "totally-different-salt"
    assert ua.verify_password("s3cure-passphrase")
    assert not ua.verify_password("nope")
