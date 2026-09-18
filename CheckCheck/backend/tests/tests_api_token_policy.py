"""Key policy for the token manager (TOKEN_API_REVIEW.md, chunk 3).

* A key without a chosen lifetime gets ``API_TOKEN_MANAGEMENT_DEFAULT_EXPIRY_DAYS``.
* A lifetime above ``API_TOKEN_MANAGEMENT_MAX_EXPIRY_DAYS`` is refused with 422.
* Never-expiring keys are refused unless ``API_TOKEN_ALLOW_NEVER_EXPIRE`` is set.
* A user holds at most ``API_TOKEN_MANAGEMENT_MAX_TOKENS_PER_USER`` unexpired keys
  from the token manager (409 beyond it). Login tokens and expired keys do not count.

The HTTP tests run against the test server with the default settings. The paths
that need other settings call the route function directly with a fake CRUD, like
tests_api_token_security.py.
"""

import asyncio
import datetime
import uuid

import pytest
from fastapi import HTTPException

import checkcheckserver.api.routes.routes_user as routes_user
from checkcheckserver.api.routes.routes_user import (
    APIKeyCreateRequest,
    create_my_api_key,
)
from checkcheckserver.model.user import User
from checkcheckserver.model.user_auth import UserAuth

from utils import (
    req,
    server_config,
    create_test_user,
    authorize_for_session,
    authorize_for_access_token,
)
from tests_api_token_oidc import _run


def _local_user_session(user_name: str):
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    return authorize_for_session(user_name, pw), pw


def _create(session, expected_http_code: int = 201, **body):
    return req(
        "api/user/me/api-keys",
        "post",
        b={"display_name": "policy", **body},
        expected_http_code=expected_http_code,
        session=session,
    )


def _now_epoch() -> int:
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp())


# ── HTTP, default settings ────────────────────────────────────────────────────


def test_key_without_lifetime_gets_the_management_default():
    s, _ = _local_user_session("apikey-policy-default")
    key = _create(s)
    expected = _now_epoch() + server_config.API_TOKEN_MANAGEMENT_DEFAULT_EXPIRY_DAYS * 86400
    assert abs(key["expires_at_epoch_time"] - expected) < 300


def test_lifetime_above_the_maximum_is_refused():
    s, _ = _local_user_session("apikey-policy-max")
    max_days = server_config.API_TOKEN_MANAGEMENT_MAX_EXPIRY_DAYS
    _create(s, expires_in_days=max_days)
    res = _create(s, expected_http_code=422, expires_in_days=max_days + 1)
    assert f"at most {max_days} days" in str(res)


def test_key_limit_counts_only_unexpired_managed_keys():
    max_keys = server_config.API_TOKEN_MANAGEMENT_MAX_TOKENS_PER_USER
    if max_keys is None:
        pytest.skip("no key limit configured")
    user_name = "apikey-policy-limit"
    s, pw = _local_user_session(user_name)
    # Login tokens are no managed keys and must not use up the quota.
    authorize_for_access_token(user_name, pw)

    keys = [_create(s, expires_in_days=1) for _ in range(max_keys)]
    res = _create(s, expected_http_code=409, expires_in_days=1)
    assert "Revoke one" in str(res)

    # Revoking a key frees a slot.
    req(
        f"api/user/me/api-keys/{keys[0]['api_token_id']}",
        "delete",
        expected_http_code=204,
        session=s,
    )
    _create(s, expires_in_days=1)
    _create(s, expected_http_code=409, expires_in_days=1)

    # So does a key that expired but was not deleted.
    _expire_key(keys[1]["id"])
    _create(s, expires_in_days=1)


def _expire_key(user_auth_id: str):
    from sqlmodel import update

    async def body(session):
        await session.exec(
            update(UserAuth)
            .where(UserAuth.id == uuid.UUID(user_auth_id))
            .values(expires_at_epoch_time=_now_epoch() - 3600)
        )
        await session.commit()

    _run(body)


# ── Route function, other settings ────────────────────────────────────────────


class _FakeKeyCRUD:
    def __init__(self, active_keys: int = 0):
        self.active_keys = active_keys
        self.created: list[UserAuth] = []

    async def count_active_managed_api_tokens_by_user_id(self, user_id):
        return self.active_keys

    async def create(self, user_auth_create):
        row = UserAuth.from_update_or_create_object(user_auth_create)
        row.id = uuid.uuid4()
        self.created.append(row)
        return row


def _user() -> User:
    return User(id=uuid.uuid4(), user_name="policy-unit", email="policy@test.de")


def _call(crud: _FakeKeyCRUD, **body):
    return asyncio.run(
        create_my_api_key(
            body=APIKeyCreateRequest(display_name="unit", **body),
            current_user=_user(),
            user_auth_crud=crud,
        )
    )


def test_never_expiring_key_when_allowed(monkeypatch):
    monkeypatch.setattr(routes_user.config, "API_TOKEN_ALLOW_NEVER_EXPIRE", True)
    res = _call(_FakeKeyCRUD(), never_expires=True)
    assert res.expires_at_epoch_time is None


def test_never_expiring_key_when_not_allowed(monkeypatch):
    monkeypatch.setattr(routes_user.config, "API_TOKEN_ALLOW_NEVER_EXPIRE", False)
    crud = _FakeKeyCRUD()
    with pytest.raises(HTTPException) as exc:
        _call(crud, never_expires=True)
    assert exc.value.status_code == 422
    assert crud.created == []


def test_custom_default_and_maximum(monkeypatch):
    monkeypatch.setattr(routes_user.config, "API_TOKEN_MANAGEMENT_DEFAULT_EXPIRY_DAYS", 7)
    monkeypatch.setattr(routes_user.config, "API_TOKEN_MANAGEMENT_MAX_EXPIRY_DAYS", 10)
    res = _call(_FakeKeyCRUD())
    assert abs(res.expires_at_epoch_time - (_now_epoch() + 7 * 86400)) < 60
    _call(_FakeKeyCRUD(), expires_in_days=10)
    with pytest.raises(HTTPException) as exc:
        _call(_FakeKeyCRUD(), expires_in_days=11)
    assert exc.value.status_code == 422


def test_custom_key_limit(monkeypatch):
    monkeypatch.setattr(routes_user.config, "API_TOKEN_MANAGEMENT_MAX_TOKENS_PER_USER", 2)
    _call(_FakeKeyCRUD(active_keys=1), expires_in_days=1)
    with pytest.raises(HTTPException) as exc:
        _call(_FakeKeyCRUD(active_keys=2), expires_in_days=1)
    assert exc.value.status_code == 409


def test_no_key_limit(monkeypatch):
    monkeypatch.setattr(routes_user.config, "API_TOKEN_MANAGEMENT_MAX_TOKENS_PER_USER", None)
    _call(_FakeKeyCRUD(active_keys=10_000), expires_in_days=1)


def test_default_above_maximum_is_a_config_error():
    from pydantic import ValidationError
    from checkcheckserver.config import Config

    with pytest.raises(ValidationError, match="must not exceed"):
        Config(
            API_TOKEN_MANAGEMENT_DEFAULT_EXPIRY_DAYS=100,
            API_TOKEN_MANAGEMENT_MAX_EXPIRY_DAYS=90,
        )
