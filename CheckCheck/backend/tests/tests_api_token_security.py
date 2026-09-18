"""Unit tests for the API token hardening (TOKEN_API_REVIEW.md, chunk 1).

* No secret (API key, OIDC access/refresh token) ends up in any app log at DEBUG
  while a token is validated or an OIDC token is refreshed.
* The plain token is hidden in the repr of the create object.
* A login token whose source login is gone gives a 401, not a 500.
* `get_current_user_by_session` refuses Bearer tokens with a 403.
* A managed key of a user whose last OIDC login is too old is paused (chunk 2).

Like tests_oidc_refresh_race.py, these drive the real auth code directly and fake
the DB CRUDs, so they do not need the HTTP test server. The HTTP counterpart for
the 403 on the key management endpoints lives in tests_auth.py.
"""

import asyncio
import datetime
import logging
import uuid

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import checkcheckserver.api.auth.security as security
from checkcheckserver.api.auth.security import (
    SESSION_COOKIE_NAME,
    get_current_user_auth,
    get_current_user_by_session,
    reject_paused_api_key,
)
from checkcheckserver.model.user_auth import (
    UserAuth,
    UserAuthCreate,
    AllowedAuthSchemeType,
)

from tests_oidc_refresh_race import (
    PROVIDER_SLUG,
    _FakeUserAuthCRUD,
    _FakeUserSessionCRUD,
    _RotatingTokenEndpoint,
    _FakeContainer,
    _FakeRequest,
    _make_expired_oidc_session,
)


class _FakeTokenUserAuthCRUD(_FakeUserAuthCRUD):
    """Adds the two calls the API token path makes on top of the refresh fakes."""

    async def get_api_token_by_id(self, token_id, raise_exception_if_none=None):
        for obj in self.store.values():
            if (
                obj.auth_source_type == AllowedAuthSchemeType.api_token
                and obj.api_token_id == token_id
            ):
                return obj.model_copy(deep=True)
        return None

    async def touch_last_used_at(self, user_auth_id):
        return None


def _make_api_token(source_user_auth_id: uuid.UUID | None = None) -> tuple[UserAuth, str]:
    """A stored API token row plus its plain `id.secret` string."""
    create = UserAuthCreate(
        user_id=uuid.uuid4(),
        auth_source_type=AllowedAuthSchemeType.api_token,
        api_token_source_user_auth_id=source_user_auth_id,
    )
    create.generate_api_token()
    plain = create.get_api_token()
    stored = UserAuth(
        id=uuid.uuid4(),
        user_id=create.user_id,
        auth_source_type=AllowedAuthSchemeType.api_token,
        api_token_id=create.api_token_id,
        api_token_source_user_auth_id=source_user_auth_id,
    )
    stored.set_api_token(create.api_token)
    return stored, plain


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


class _CollectAll(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


@pytest.fixture
def all_app_logs_at_debug():
    """Collect the output of every logger (app and libraries) at DEBUG."""
    handler = _CollectAll()
    root = logging.getLogger()
    loggers = [root] + [
        logging.getLogger(name) for name in list(logging.root.manager.loggerDict)
    ]
    saved_levels = [(lg, lg.level) for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        yield handler.lines
    finally:
        root.removeHandler(handler)
        for lg, level in saved_levels:
            lg.setLevel(level)


def _assert_absent(lines: list[str], secrets: list[str]):
    assert lines, "log capture saw nothing; the fixture is not wired up"
    joined = "\n".join(lines)
    for secret in secrets:
        assert secret not in joined, f"secret {secret[:6]}... found in DEBUG log output"


def test_api_token_secret_does_not_end_up_in_debug_logs(all_app_logs_at_debug):
    stored, plain = _make_api_token()
    auth_crud = _FakeTokenUserAuthCRUD({stored.id: stored})

    async def scenario():
        logging.getLogger(security.log.name).debug("capture check")
        return await get_current_user_auth(
            request=_FakeRequest({}),
            user_session_crud=_FakeUserSessionCRUD({}),
            user_auth_crud=auth_crud,
            api_token=_bearer(plain),
        )

    result = asyncio.run(scenario())
    assert result.id == stored.id
    _assert_absent(all_app_logs_at_debug, [plain, plain.split(".", 1)[1]])


def test_oidc_tokens_do_not_end_up_in_debug_logs_on_refresh(all_app_logs_at_debug):
    user_auth, user_session = _make_expired_oidc_session()
    auth_crud = _FakeUserAuthCRUD({user_auth.id: user_auth})
    session_crud = _FakeUserSessionCRUD({user_session.id: user_session})
    security.oauth_clients = {PROVIDER_SLUG: _FakeContainer(_RotatingTokenEndpoint())}
    security._oidc_refresh_locks.clear()

    async def scenario():
        return await get_current_user_auth(
            request=_FakeRequest({SESSION_COOKIE_NAME: str(user_session.id)}),
            user_session_crud=session_crud,
            user_auth_crud=auth_crud,
            api_token=None,
        )

    refreshed = asyncio.run(scenario())
    # The fake endpoint issued AT1/RT1 for the old AT0/RT0.
    assert refreshed.get_decrypted_oidc_token()["refresh_token"] == "RT1"
    _assert_absent(all_app_logs_at_debug, ["AT0", "RT0", "AT1", "RT1"])


def test_plain_token_is_hidden_in_the_create_object_repr():
    create = UserAuthCreate(
        user_id=uuid.uuid4(), auth_source_type=AllowedAuthSchemeType.api_token
    )
    create.generate_api_token()
    secret = create.get_api_token().split(".", 1)[1]
    assert secret not in repr(create)
    assert secret not in str(create)
    assert secret not in str(create.model_dump())


def test_token_with_deleted_source_login_is_401():
    source_id = uuid.uuid4()  # never stored: the source login is gone
    stored, plain = _make_api_token(source_user_auth_id=source_id)
    auth_crud = _FakeTokenUserAuthCRUD({stored.id: stored})

    async def scenario():
        return await get_current_user_auth(
            request=_FakeRequest({}),
            user_session_crud=_FakeUserSessionCRUD({}),
            user_auth_crud=auth_crud,
            api_token=_bearer(plain),
        )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(scenario())
    assert exc_info.value.status_code == 401


def test_session_only_dependency_refuses_bearer_tokens():
    async def scenario():
        return await get_current_user_by_session(
            request=_FakeRequest({}),
            user_session_crud=None,
            user_auth_crud=None,
            user_crud=None,
            api_token=_bearer("anything.secret"),
        )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(scenario())
    assert exc_info.value.status_code == 403


# ── Paused keys after a stale OIDC login (chunk 2) ────────────────────────────


def _user(last_oidc_login_days_ago: int | None):
    from checkcheckserver.model.user import User

    last_login = None
    if last_oidc_login_days_ago is not None:
        last_login = datetime.datetime.now(datetime.timezone.utc).replace(
            tzinfo=None
        ) - datetime.timedelta(days=last_oidc_login_days_ago)
    return User(user_name="paused-key-user", last_oidc_login_at=last_login)


@pytest.fixture
def oidc_login_max_age_30(monkeypatch):
    monkeypatch.setattr(
        security.config, "API_TOKEN_MANAGEMENT_OIDC_LOGIN_MAX_AGE_DAYS", 30
    )


def test_managed_key_of_user_with_stale_oidc_login_is_paused(oidc_login_max_age_30):
    key, _ = _make_api_token()
    with pytest.raises(HTTPException) as exc_info:
        reject_paused_api_key(key, _user(last_oidc_login_days_ago=31))
    assert exc_info.value.status_code == 401
    assert "paused" in exc_info.value.detail


def test_managed_key_of_user_with_recent_oidc_login_works(oidc_login_max_age_30):
    key, _ = _make_api_token()
    reject_paused_api_key(key, _user(last_oidc_login_days_ago=29))


def test_managed_key_of_user_without_oidc_login_works(oidc_login_max_age_30):
    key, _ = _make_api_token()
    reject_paused_api_key(key, _user(last_oidc_login_days_ago=None))


def test_login_credentials_are_not_paused(oidc_login_max_age_30):
    # Login tokens resolve to their source login (oidc/basic), which the refresh
    # already checks against the provider. Only managed keys are paused.
    user_auth, _ = _make_expired_oidc_session()
    reject_paused_api_key(user_auth, _user(last_oidc_login_days_ago=365))


def test_paused_keys_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(
        security.config, "API_TOKEN_MANAGEMENT_OIDC_LOGIN_MAX_AGE_DAYS", None
    )
    key, _ = _make_api_token()
    reject_paused_api_key(key, _user(last_oidc_login_days_ago=365))
