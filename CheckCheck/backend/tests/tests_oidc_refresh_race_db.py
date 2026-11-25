"""The OIDC refresh race again, this time against a real database.

``tests_oidc_refresh_race.py`` proves the refresh is serialized, but it fakes the
CRUDs with copies, so every ``get()`` there is a fresh read. The real CRUDs read
through a SQLAlchemy session: ``session.get()`` hands back the object already in
the session's identity map without asking the database. A request that loaded the
expired credential before it waited for the refresh lock therefore "re-read" its
own stale copy after the wait, refreshed a second time with the refresh token the
winner had already used, got ``invalid_grant`` from a rotating IdP and wiped the
session. Production symptom: some time after the last visit the app opens on the
login page for a moment and auto-login sends the browser straight back.

Here every simulated request gets its own ``AsyncSession`` (configured like
``db/_session.py``) and the real ``UserAuthCRUD`` / ``UserSessionCRUD`` on a
throwaway SQLite file. Only the IdP token endpoint is faked (with rotation).
"""

import asyncio
import datetime
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import checkcheckserver.api.auth.security as security
from checkcheckserver.api.auth.security import (
    SESSION_COOKIE_NAME,
    get_current_user_auth,
)
from checkcheckserver.db.user_auth import UserAuthCRUD
from checkcheckserver.db.user_session import UserSessionCRUD
from checkcheckserver.model._base_model import SYNC_SEQ_ROW_ID
from checkcheckserver.model.user import User
from checkcheckserver.model.user_auth import UserAuth, AllowedAuthSchemeType
from checkcheckserver.model.user_session import UserSession

import checkcheckserver.model._tables  # noqa: F401  (register every table on the metadata)

PROVIDER_SLUG = "testprov"


def _epoch(offset_sec: int) -> int:
    return int(datetime.datetime.now(tz=datetime.UTC).timestamp()) + offset_sec


class _RotatingTokenEndpoint:
    """Each refresh token works once; a reused one fails like ``invalid_grant``."""

    def __init__(self):
        self.consumed: set[str] = set()
        self.grants = 0

    async def load_server_metadata(self):
        return {"token_endpoint": "https://idp.invalid/token"}

    async def fetch_access_token(self, url, refresh_token=None, grant_type=None):
        assert grant_type == "refresh_token"
        if refresh_token in self.consumed:
            raise RuntimeError("invalid_grant: refresh token already used (rotated)")
        self.consumed.add(refresh_token)
        self.grants += 1
        # Give the other requests time to queue up on the lock, like a real
        # round trip to the IdP would.
        await asyncio.sleep(0.05)
        return {
            "access_token": f"AT{self.grants}",
            "refresh_token": f"RT{self.grants}",
            "expires_at": _epoch(3600),
            "expires_in": 3600,
        }


class _FakeContainer:
    def __init__(self, client):
        self.client = client


class _FakeRequest:
    def __init__(self, cookies: dict):
        self.cookies = cookies


def test_concurrent_oidc_refresh_with_real_db_sessions(tmp_path):
    N = 5

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.sqlite'}")
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
            # The sync-sequence counter row, seeded like db/_init_db.py does.
            await conn.execute(
                text("INSERT INTO sync_seq (id, value) VALUES (:row_id, 0)"),
                {"row_id": SYNC_SEQ_ROW_ID},
            )
        make_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        past = _epoch(-10)
        user = User(id=uuid.uuid4(), user_name="racer")
        user_auth = UserAuth(
            id=uuid.uuid4(),
            user_id=user.id,
            auth_source_type=AllowedAuthSchemeType.oidc,
            oidc_provider_slug=PROVIDER_SLUG,
            expires_at_epoch_time=past,
        )
        user_auth.set_oidc_token(
            {"access_token": "AT0", "refresh_token": "RT0", "expires_at": past}
        )
        user_session = UserSession(
            id=uuid.uuid4(),
            user_id=user.id,
            user_auth_id=user_auth.id,
            expires_at_epoch_time=past,
        )
        async with make_session() as s:
            s.add(user)
            await s.commit()
            s.add(user_auth)
            await s.commit()
            s.add(user_session)
            await s.commit()

        endpoint = _RotatingTokenEndpoint()
        security.oauth_clients = {PROVIDER_SLUG: _FakeContainer(endpoint)}
        security._oidc_refresh_locks.clear()
        cookies = {SESSION_COOKIE_NAME: str(user_session.id)}

        async def one_request():
            # One DB session per request, shared by both CRUDs like FastAPI's
            # per-request dependency cache does.
            async with make_session() as s:
                return await get_current_user_auth(
                    request=_FakeRequest(cookies),
                    user_session_crud=UserSessionCRUD(session=s),
                    user_auth_crud=UserAuthCRUD(session=s),
                    api_token=None,
                )

        results = await asyncio.gather(
            *[one_request() for _ in range(N)], return_exceptions=True
        )

        async with make_session() as s:
            session_left = await s.get(UserSession, user_session.id)
            auth_left = await s.get(UserAuth, user_auth.id)
        await engine.dispose()
        return results, endpoint.grants, session_left, auth_left

    results, grants, session_left, auth_left = asyncio.run(scenario())

    failures = [r for r in results if not isinstance(r, UserAuth)]
    assert not failures, (
        f"{len(failures)}/{N} concurrent requests were logged out during one OIDC "
        f"token refresh. Outcomes: {results}"
    )
    assert grants == 1, f"expected exactly one refresh at the IdP, got {grants}"
    assert session_left is not None, "the session was wiped"
    assert auth_left is not None and not auth_left.is_expired()
