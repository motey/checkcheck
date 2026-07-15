"""Regression test for the periodic "bounced back to /login" bug on OIDC sessions.

Symptom (production): every few minutes an OIDC-authenticated view briefly jumps
to the login dialog. The cadence is the OIDC **access-token lifetime** — on login
``UserSession.expires_at_epoch_time`` is set to the access token's expiry
(``routes_auth.auth_oidc_callback``), so once per lifetime the session is
"expired" and the next request must refresh the token.

Root cause: ``get_current_user_auth`` (api/auth/security.py) refreshes the token
with **no serialization**. A single-page app fires several API calls in parallel
(board, counts, notifications, the ``/api/sync`` reconnect). When the token is
expired, *each* of those requests independently reads the same stored refresh
token and calls the IdP's token endpoint. Virtually every IdP **rotates** refresh
tokens (single-use): the first refresh consumes the token and the concurrent ones
get ``invalid_grant`` → an exception → ``wipe_expired_user_session_or_user_auth``
**deletes the session and the credential** → those requests 401 → the frontend's
global handler bounces to ``/login``. (The wipe even destroys the credential the
winning request just refreshed.) With OIDC auto-login the browser silently
re-authenticates, so the user "only sees it shortly" — exactly the report.

This is a concurrency + refresh-token-rotation defect, so it cannot be proven
reliably through the HTTP integration harness (it depends on the mock provider's
rotation semantics and on winning a real timing race). Instead we drive the real
``get_current_user_auth`` / ``oidc_refresh_access_token`` code directly and fake
the two I/O seams:

* the DB CRUDs — small in-memory fakes whose ``get()`` returns a fresh copy per
  call, exactly like a real per-request DB read (so each concurrent request sees
  its own snapshot of the credential);
* the IdP token endpoint — a fake that models refresh-token **rotation**: the
  first ``refresh_token`` grant for a given token succeeds and hands out a new
  refresh token; any later grant presenting an already-consumed token fails with
  ``invalid_grant``.

We then fire N concurrent ``get_current_user_auth`` calls against one expired
session and assert **all** of them come back authenticated. Before the fix, only
the first survives and the rest are wiped + 401'd (the test fails); after the fix
(serialized refresh + double-checked re-read) every request is authenticated.
"""

import asyncio
import copy
import datetime
import uuid

import pytest

import checkcheckserver.api.auth.security as security
from checkcheckserver.api.auth.security import (
    SESSION_COOKIE_NAME,
    get_current_user_auth,
)
from checkcheckserver.model.user_auth import (
    UserAuth,
    UserAuthUpdate,
    AllowedAuthSchemeType,
)
from checkcheckserver.model.user_session import UserSession


PROVIDER_SLUG = "testprov"


def _epoch(offset_sec: int) -> int:
    return int(datetime.datetime.now(tz=datetime.UTC).timestamp()) + offset_sec


# ── In-memory fakes for the two I/O boundaries ────────────────────────────────


class _FakeUserAuthCRUD:
    """Backs UserAuth reads/writes with a dict. ``get`` returns a deep copy so two
    concurrent requests each hold their own snapshot — like two DB sessions each
    loading the row — which is what makes the rotation race reproducible."""

    def __init__(self, store: dict):
        self.store = store

    async def get(self, id_, raise_exception_if_none=None, include_deleted=False):
        obj = self.store.get(id_)
        if obj is None:
            if raise_exception_if_none:
                raise raise_exception_if_none
            return None
        return obj.model_copy(deep=True)

    async def update(self, user_auth_update: UserAuthUpdate, id_=None, **_):
        stored = self.store.get(id_)
        if stored is None:
            # Mirrors CRUDBase.update setattr-on-None when the row is gone.
            raise RuntimeError(f"UserAuth {id_} vanished before update")
        # Faithful to UserAuthCRUD.update → update_secrets (re-encrypts the token
        # and stamps the new expiry).
        stored.update_secrets(user_auth_update)
        return stored.model_copy(deep=True)

    async def delete(self, id, raise_exception_if_not_exists=None, **_):
        self.store.pop(id, None)
        return True


class _FakeUserSessionCRUD:
    def __init__(self, store: dict):
        self.store = store

    async def get(self, id_, raise_exception_if_none=None, include_deleted=False):
        obj = self.store.get(id_)
        if obj is None:
            if raise_exception_if_none:
                raise raise_exception_if_none
            return None
        return obj.model_copy(deep=True)

    async def get_by_user_auth_id(self, user_auth_id, raise_exception_if_none=None):
        for s in self.store.values():
            if s.user_auth_id == user_auth_id:
                return s.model_copy(deep=True)
        if raise_exception_if_none:
            raise raise_exception_if_none
        return None

    async def update(self, update_obj: UserSession, id_=None, **_):
        key = id_ if id_ is not None else update_obj.id
        if key not in self.store:
            raise RuntimeError(f"UserSession {key} vanished before update")
        self.store[key] = update_obj.model_copy(deep=True)
        return self.store[key].model_copy(deep=True)

    async def delete(self, id, raise_exception_if_not_exists=None, **_):
        self.store.pop(id, None)
        return True


class _RotatingTokenEndpoint:
    """Models an IdP that rotates refresh tokens (single-use). The first grant for
    a given refresh token succeeds and issues a fresh one; presenting an already
    consumed refresh token fails with invalid_grant, exactly like Keycloak/Google
    once ``fetch_access_token`` has rotated it out from under a slower request."""

    def __init__(self):
        self.consumed: set[str] = set()
        self._counter = 0

    async def load_server_metadata(self):
        # A real suspension point so concurrent callers interleave here *after*
        # each has already read the (soon-to-be-stale) refresh token.
        await asyncio.sleep(0)
        return {"token_endpoint": "https://idp.example/token"}

    async def fetch_access_token(self, token_endpoint, refresh_token=None, grant_type=None):
        assert grant_type == "refresh_token"
        if refresh_token in self.consumed:
            raise RuntimeError("invalid_grant: refresh token already used (rotated)")
        self.consumed.add(refresh_token)
        self._counter += 1
        await asyncio.sleep(0)
        return {
            "access_token": f"AT{self._counter}",
            "refresh_token": f"RT{self._counter}",
            "expires_at": _epoch(3600),
            "expires_in": 3600,
        }


class _FakeContainer:
    def __init__(self, client):
        self.client = client


class _FakeRequest:
    def __init__(self, cookies: dict):
        self.cookies = cookies


# ── The test ──────────────────────────────────────────────────────────────────


def _make_expired_oidc_session():
    """An OIDC UserAuth + UserSession whose access token expired 10s ago, so the
    next request triggers a refresh (30s leeway means <30s-to-expiry counts too)."""
    past = _epoch(-10)
    user_auth = UserAuth(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        auth_source_type=AllowedAuthSchemeType.oidc,
        oidc_provider_slug=PROVIDER_SLUG,
        expires_at_epoch_time=past,
    )
    user_auth.set_oidc_token(
        {"access_token": "AT0", "refresh_token": "RT0", "expires_at": past}
    )
    user_session = UserSession(
        id=uuid.uuid4(),
        user_id=user_auth.user_id,
        user_auth_id=user_auth.id,
        expires_at_epoch_time=past,
    )
    return user_auth, user_session


def test_concurrent_oidc_refresh_does_not_log_the_user_out():
    """N parallel requests hit one expired OIDC session at once. Every one must
    come back authenticated — none may be wiped/401'd by losing the refresh race.
    """
    N = 4

    async def scenario():
        user_auth, user_session = _make_expired_oidc_session()
        auth_store = {user_auth.id: user_auth}
        session_store = {user_session.id: user_session}

        auth_crud = _FakeUserAuthCRUD(auth_store)
        session_crud = _FakeUserSessionCRUD(session_store)

        endpoint = _RotatingTokenEndpoint()
        security.oauth_clients = {PROVIDER_SLUG: _FakeContainer(endpoint)}
        # Refresh serialization (the fix) keys locks per user_auth.id in a module
        # dict — clear it so the test is independent of prior state.
        if hasattr(security, "_oidc_refresh_locks"):
            security._oidc_refresh_locks.clear()

        cookies = {SESSION_COOKIE_NAME: str(user_session.id)}

        async def one_request():
            return await get_current_user_auth(
                request=_FakeRequest(cookies),
                user_session_crud=session_crud,
                user_auth_crud=auth_crud,
                api_token=None,
            )

        return await asyncio.gather(
            *[one_request() for _ in range(N)], return_exceptions=True
        )

    results = asyncio.run(scenario())

    failures = [r for r in results if not isinstance(r, UserAuth)]
    assert not failures, (
        f"{len(failures)}/{N} concurrent requests were logged out during a single "
        f"OIDC token refresh (the periodic bounce-to-/login bug). Outcomes: {results}"
    )
