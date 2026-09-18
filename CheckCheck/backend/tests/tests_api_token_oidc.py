"""API keys of OIDC users follow the user, not the credential (TOKEN_API_REVIEW.md, chunk 2).

* A key of a user whose provider has ``RESTRICT_USER_SEARCH_TO_OWN_GROUPS`` is
  restricted in user search and group sharing, like the user's browser session.
* A key stops working (401) when the user's last OIDC login is older than
  ``API_TOKEN_MANAGEMENT_OIDC_LOGIN_MAX_AGE_DAYS`` and works again after the next
  OIDC login. Keys of local users are not affected.

The test OIDC provider (conftest) is configured with the group restriction. The
stale login is simulated by moving ``user.last_oidc_login_at`` back in the
database, from the test process.
"""

import asyncio
import datetime
import os

import pytest
import requests

from utils import (
    req,
    create_test_user,
    authorize_for_session,
    oidc_login_get_session,
    list_contains_dict_that_must_contain,
)
from statics import OIDC_TEST_PROVIDER_SLUG


def _require_oidc() -> str:
    if not os.environ.get("OIDC_MOCK_SERVER_URL"):
        pytest.skip("OIDC mock server not running, skipping OIDC API key tests")
    return os.environ["OIDC_MOCK_SERVER_URL"]


def _oidc_session_with_groups(sub: str, groups: list[str]) -> requests.Session:
    """Register a mock OIDC user with `groups` and log them in via the browser flow."""
    mock_url = _require_oidc()
    requests.put(
        f"{mock_url}/users/{sub}",
        json={
            "sub": sub,
            "userinfo": {
                "name": sub,
                "email": f"{sub}@test.com",
                "given_name": sub,
                "groups": groups,
            },
        },
    ).raise_for_status()
    return oidc_login_get_session(OIDC_TEST_PROVIDER_SLUG, sub)


def _create_key(session: requests.Session) -> str:
    return req(
        "api/user/me/api-keys",
        "post",
        b={"display_name": "script", "expires_in_days": 7},
        session=session,
    )["token"]


def _make_local_user(user_name: str, groups: list[str] | None = None) -> str:
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    session = authorize_for_session(user_name, pw)
    user_id = req("api/user/me", session=session)["id"]
    if groups is not None:
        req(f"api/user/{user_id}", "patch", b={"oidc_groups": groups})
    return user_id


def _run(body):
    """Run `body(session)` against the test database (see tests_reminder_scan.py)."""
    import checkcheckserver.model._tables  # noqa: F401  (registers every table)
    from checkcheckserver.db._engine import db_engine
    from checkcheckserver.db._session import get_async_session_context

    async def _main():
        try:
            async with get_async_session_context() as session:
                return await body(session)
        finally:
            await db_engine.dispose()

    return asyncio.run(_main())


def _set_last_oidc_login(user_id: str, days_ago: int):
    from sqlmodel import update
    from checkcheckserver.model.user import User

    import uuid

    when = datetime.datetime.now(datetime.timezone.utc).replace(
        tzinfo=None
    ) - datetime.timedelta(days=days_ago)

    async def body(session):
        await session.exec(
            update(User)
            .where(User.id == uuid.UUID(user_id))
            .values(last_oidc_login_at=when)
        )
        await session.commit()

    _run(body)


def test_oidc_login_records_provider_and_login_time():
    s = _oidc_session_with_groups("apikey-oidc-record", ["apikey-record-team"])
    me = req("api/user/me", session=s)
    assert me["oidc_provider_slug"] == OIDC_TEST_PROVIDER_SLUG
    assert me["last_oidc_login_at"] is not None


def test_api_key_of_restricted_user_is_restricted_in_user_search():
    _require_oidc()
    team = "apikey-search-team"
    _make_local_user("apikey-search-teammate", [team])
    _make_local_user("apikey-search-stranger", ["some-other-team"])
    s = _oidc_session_with_groups("apikey-search-caller", [team])
    key = _create_key(s)

    for auth in ({"session": s}, {"access_token": key}):
        results = req("api/user/search", q={"q": "apikey-search"}, **auth)
        names = {r["user_name"] for r in results}
        assert "apikey-search-teammate" in names, auth
        assert "apikey-search-stranger" not in names, auth


def test_api_key_of_restricted_user_can_not_share_with_a_foreign_group():
    _require_oidc()
    team = "apikey-share-team"
    s = _oidc_session_with_groups("apikey-share-owner", [team])
    key = _create_key(s)
    checklist_id = req(
        "api/checklist",
        "post",
        b={"name": "KeyGroupRestrict", "color_id": "yellow"},
        access_token=key,
    )["id"]

    req(
        f"api/checklist/{checklist_id}/shares/group/{team}",
        "put",
        b={"permission": "view"},
        access_token=key,
    )
    req(
        f"api/checklist/{checklist_id}/shares/group/apikey-foreign-team",
        "put",
        b={"permission": "view"},
        access_token=key,
        expected_http_code=403,
    )


def test_api_key_is_paused_after_stale_oidc_login_and_reactivated_by_login():
    sub = "apikey-stale-user"
    s = _oidc_session_with_groups(sub, [])
    user_id = req("api/user/me", session=s)["id"]
    key = _create_key(s)
    req("api/user/me", access_token=key)

    _set_last_oidc_login(user_id, days_ago=31)
    res = req("api/user/me", access_token=key, expected_http_code=401)
    assert "paused" in str(res)

    # A fresh OIDC login in the browser reactivates the same key.
    _oidc_session_with_groups(sub, [])
    assert req("api/user/me", access_token=key)["id"] == user_id


def test_api_key_within_oidc_login_max_age_keeps_working():
    s = _oidc_session_with_groups("apikey-recent-user", [])
    user_id = req("api/user/me", session=s)["id"]
    key = _create_key(s)
    _set_last_oidc_login(user_id, days_ago=29)
    assert req("api/user/me", access_token=key)["id"] == user_id


def test_api_key_of_local_user_is_not_paused():
    user_name = "apikey-local-user"
    pw = f"{user_name}_pw_secure1"
    create_test_user(user_name, pw, f"{user_name}@test.de")
    s = authorize_for_session(user_name, pw)
    key = _create_key(s)
    me = req("api/user/me", access_token=key)
    assert me["last_oidc_login_at"] is None
    list_contains_dict_that_must_contain(
        req("api/user/me/api-keys", session=s), {"display_name": "script"}
    )
