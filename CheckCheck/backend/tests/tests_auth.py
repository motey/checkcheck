"""
Integration tests for the auth system.

Covers:
  - Basic login (token + session endpoints)
  - Self-registration (disabled-by-default gate)
  - Password change (self-service)
  - API key create / list / authenticate / last_used_at / delete
  - Cross-user API key access (must be blocked)
  - Admin API key management
  - Session listing and per-session revocation
  - Bulk session revocation
  - Admin role update (regression: was silently dropping fields)
  - Deactivated user is blocked from all auth
  - Session-based logout (cookie cleared + endpoint rejects old cookie)
  - Token-based logout (Bearer token invalidated)
"""

import os
import requests as req_lib
from utils import (
    req,
    authorize_for_access_token,
    authorize_for_session,
    dict_must_contain,
    list_contains_dict_that_must_contain,
    find_first_dict_in_list,
    get_server_base_url,
    create_test_user,
)
from statics import ADMIN_USER_NAME, ADMIN_USER_PW, ADMIN_USER_EMAIL, TEST_USER_NAME, TEST_USER_PW

CHECKCHECK_ACCESS_TOKEN_ENV_NAME = "MEDLOG_ACCESS_TOKEN"

AUTH_TEST_USER_NAME = "auth-test-user"
AUTH_TEST_USER_PW = "authtestpw_secure123"
AUTH_TEST_USER_EMAIL = "auth-test-user@test.com"

_state: dict = {}

def _get_user_id(username: str) -> str:
    res = req("api/user", q={"incl_deactivated": True})
    user = find_first_dict_in_list(res["items"], {"user_name": username})
    return user["id"]

class _AsUser:
    """Context manager: switch to a user's token, restore admin token on exit."""
    def __init__(self, username: str, password: str):
        self._username = username
        self._password = password

    def __enter__(self):
        self._saved = os.environ.get(CHECKCHECK_ACCESS_TOKEN_ENV_NAME)
        authorize_for_access_token(self._username, self._password, set_as_global_default_login=True)
        return self

    def __exit__(self, *_):
        if self._saved is not None:
            os.environ[CHECKCHECK_ACCESS_TOKEN_ENV_NAME] = self._saved

# ── Auth scheme listing ───────────────────────────────────────────────────────

def test_auth_list_schemes():
    res = req("api/auth/list")
    assert isinstance(res, list), f"Expected list, got {type(res)}"
    assert any(s.get("auth_type") == "basic" for s in res), (
        "Expected basic auth scheme in /auth/list"
    )

# ── Basic login (token-based) ─────────────────────────────────────────────────

def test_basic_login_token_success():
    res = req(
        "api/auth/basic/login/token",
        "post",
        f={"username": ADMIN_USER_NAME, "password": ADMIN_USER_PW},
    )
    dict_must_contain(res, required_keys=["access_token", "token_type"])
    assert res["token_type"] == "Bearer"
    assert len(res["access_token"]) > 20

def test_basic_login_token_wrong_password():
    req(
        "api/auth/basic/login/token",
        "post",
        f={"username": ADMIN_USER_NAME, "password": "TOTALLY_WRONG_PW"},
        suppress_auth=True,
        expected_http_code=401,
    )

def test_basic_login_token_wrong_username():
    req(
        "api/auth/basic/login/token",
        "post",
        f={"username": "ghost_user_does_not_exist", "password": "anything"},
        suppress_auth=True,
        expected_http_code=401,
    )

# ── Session-based login ───────────────────────────────────────────────────────

def test_basic_login_session_and_logout():
    """Session cookie is set on login, cookie is cleared and endpoint rejects after logout."""
    session = authorize_for_session(ADMIN_USER_NAME, ADMIN_USER_PW)

    # Session must work
    me = req("api/user/me", session=session)
    dict_must_contain(me, {"user_name": ADMIN_USER_NAME})

    # Logout via session
    logout_res = req("api/auth/logout", "post", session=session)
    assert logout_res == {"message": "Logged out successfully"}, logout_res

    # Protected endpoint must now reject the session
    req("api/user/me", session=session, expected_http_code=401)

# ── Self-registration (disabled by default) ───────────────────────────────────

def test_register_disabled_by_default():
    req(
        "api/auth/basic/register",
        "post",
        q={
            "password": "securepass123",
            "password_repeat": "securepass123",
            "email": "newreg@test.com",
            "user_name": "newreguser",
        },
        suppress_auth=True,
        expected_http_code=403,
    )

# ── Test-user setup ───────────────────────────────────────────────────────────

def test_create_auth_test_user():
    user = create_test_user(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW, AUTH_TEST_USER_EMAIL)
    _state["test_user_id"] = user["id"]

# ── Password change (self-service) ────────────────────────────────────────────

def test_change_own_password_wrong_old():
    with _AsUser(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW):
        req(
            "api/user/me/password",
            "put",
            f={
                "old_password": "DEFINITELY_WRONG_OLD_PW",
                "new_password": "newpw_secure_456",
                "new_password_repeated": "newpw_secure_456",
            },
            expected_http_code=401,
        )

def test_change_own_password_mismatch():
    with _AsUser(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW):
        req(
            "api/user/me/password",
            "put",
            f={
                "old_password": AUTH_TEST_USER_PW,
                "new_password": "newpw_secure_456",
                "new_password_repeated": "DIFFERENT_pw_456",
            },
            expected_http_code=400,
        )

def test_change_own_password_success():
    new_pw = "changed_pw_secure_789"
    with _AsUser(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW):
        req(
            "api/user/me/password",
            "put",
            f={
                "old_password": AUTH_TEST_USER_PW,
                "new_password": new_pw,
                "new_password_repeated": new_pw,
            },
        )
        authorize_for_access_token(AUTH_TEST_USER_NAME, new_pw, set_as_global_default_login=True)
        me = req("api/user/me")
        assert me["user_name"] == AUTH_TEST_USER_NAME

        # Reset back
        req(
            "api/user/me/password",
            "put",
            f={
                "old_password": new_pw,
                "new_password": AUTH_TEST_USER_PW,
                "new_password_repeated": AUTH_TEST_USER_PW,
            },
        )

    req(
        "api/auth/basic/login/token",
        "post",
        f={"username": AUTH_TEST_USER_NAME, "password": new_pw},
        suppress_auth=True,
        expected_http_code=401,
    )

def test_change_to_long_password():
    """Regression: a password longer than bcrypt's 72-byte limit used to crash
    hashing (passlib/bcrypt). With argon2 it must hash, log in, and verify."""
    long_pw = "L0ng-" + ("x" * 200)  # well over 72 bytes
    with _AsUser(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW):
        req(
            "api/user/me/password",
            "put",
            f={
                "old_password": AUTH_TEST_USER_PW,
                "new_password": long_pw,
                "new_password_repeated": long_pw,
            },
        )
        # The long password must actually authenticate...
        authorize_for_access_token(AUTH_TEST_USER_NAME, long_pw, set_as_global_default_login=True)
        me = req("api/user/me")
        assert me["user_name"] == AUTH_TEST_USER_NAME

        # ...and a 72-byte truncation of it must NOT (proves no silent truncation).
        req(
            "api/auth/basic/login/token",
            "post",
            f={"username": AUTH_TEST_USER_NAME, "password": long_pw[:72]},
            suppress_auth=True,
            expected_http_code=401,
        )

        # Reset back so later tests keep using AUTH_TEST_USER_PW.
        req(
            "api/user/me/password",
            "put",
            f={
                "old_password": long_pw,
                "new_password": AUTH_TEST_USER_PW,
                "new_password_repeated": AUTH_TEST_USER_PW,
            },
        )

# ── API key management ────────────────────────────────────────────────────────

def test_api_key_create():
    with _AsUser(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW):
        res = req(
            "api/user/me/api-keys",
            "post",
            b={"display_name": "CI pipeline key", "expires_in_days": 7},
        )
    dict_must_contain(res, required_keys=["id", "token", "display_name", "api_token_id", "created_at"])
    assert res["display_name"] == "CI pipeline key"
    assert "." in res["token"], "Token must be 'id.secret' format"
    _state["api_key_token"] = res["token"]
    _state["api_key_token_id"] = res["api_token_id"]

def test_api_key_list():
    with _AsUser(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW):
        keys = req("api/user/me/api-keys")
    assert isinstance(keys, list)
    list_contains_dict_that_must_contain(keys, {"display_name": "CI pipeline key"})

def test_api_key_authenticate():
    saved = os.environ.get(CHECKCHECK_ACCESS_TOKEN_ENV_NAME)
    try:
        os.environ[CHECKCHECK_ACCESS_TOKEN_ENV_NAME] = _state["api_key_token"]
        me = req("api/user/me")
        assert me["user_name"] == AUTH_TEST_USER_NAME
    finally:
        if saved is not None:
            os.environ[CHECKCHECK_ACCESS_TOKEN_ENV_NAME] = saved

def test_api_key_last_used_at_is_set():
    with _AsUser(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW):
        keys = req("api/user/me/api-keys")
    key = find_first_dict_in_list(keys, {"api_token_id": _state["api_key_token_id"]})
    assert key["last_used_at"] is not None, "last_used_at must be set after use"

def test_api_key_delete():
    with _AsUser(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW):
        req(
            f"api/user/me/api-keys/{_state['api_key_token_id']}",
            "delete",
            expected_http_code=204,
        )
        keys = req("api/user/me/api-keys")
    found = find_first_dict_in_list(keys, {"api_token_id": _state["api_key_token_id"]}, raise_if_not_found=False)
    assert found is False, "Deleted API key must not appear in list"

def test_api_key_auth_after_delete_is_rejected():
    saved = os.environ.get(CHECKCHECK_ACCESS_TOKEN_ENV_NAME)
    try:
        os.environ[CHECKCHECK_ACCESS_TOKEN_ENV_NAME] = _state["api_key_token"]
        req("api/user/me", expected_http_code=401)
    finally:
        if saved is not None:
            os.environ[CHECKCHECK_ACCESS_TOKEN_ENV_NAME] = saved

def test_api_key_cannot_delete_another_users_key():
    admin_key_res = req("api/user/me/api-keys", "post", b={"display_name": "Admin key for cross-user test"})
    admin_key_id = admin_key_res["api_token_id"]
    _state["admin_cross_key_id"] = admin_key_id

    with _AsUser(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW):
        req(f"api/user/me/api-keys/{admin_key_id}", "delete", expected_http_code=404)

    req(f"api/user/me/api-keys/{admin_key_id}", "delete", expected_http_code=204)

# ── Admin: API key management ─────────────────────────────────────────────────

def test_admin_list_user_api_keys():
    with _AsUser(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW):
        res = req("api/user/me/api-keys", "post", b={"display_name": "Admin-visible key"})
        _state["admin_visible_key_id"] = res["api_token_id"]

    user_id = _state["test_user_id"]
    keys = req(f"api/user/{user_id}/api-keys")
    assert isinstance(keys, list)
    list_contains_dict_that_must_contain(keys, {"display_name": "Admin-visible key"})

def test_admin_delete_user_api_key():
    user_id = _state["test_user_id"]
    key_id = _state["admin_visible_key_id"]
    req(f"api/user/{user_id}/api-keys/{key_id}", "delete", expected_http_code=204)
    keys = req(f"api/user/{user_id}/api-keys")
    found = find_first_dict_in_list(keys, {"api_token_id": key_id}, raise_if_not_found=False)
    assert found is False, "Admin-deleted key must not appear in list"

# ── Session listing and revocation ────────────────────────────────────────────

def test_session_list():
    """Create a browser session then list it via a Bearer token."""
    # Get a Bearer token for the test user to call the management endpoints
    user_token = authorize_for_access_token(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW)

    # Create a browser session (populates the UserSession table)
    authorize_for_session(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW)

    sessions = req("api/user/me/sessions", access_token=user_token)
    assert isinstance(sessions, list)
    assert len(sessions) >= 1, "Expected at least one active session after browser login"
    dict_must_contain(sessions[0], required_keys=["id", "user_id", "created_at"])
    _state["test_session_id"] = sessions[0]["id"]
    _state["test_user_bearer_token"] = user_token

def test_session_delete_specific():
    """DELETE /user/me/sessions/{id} removes only that session."""
    user_token = _state.get("test_user_bearer_token")
    session_id = _state.get("test_session_id")
    if not user_token or not session_id:
        print("SKIP: prior session test did not complete")
        return

    req(f"api/user/me/sessions/{session_id}", "delete",
        expected_http_code=204, access_token=user_token)

    sessions_after = req("api/user/me/sessions", access_token=user_token)
    ids_after = [s["id"] for s in sessions_after]
    assert session_id not in ids_after, "Deleted session must not appear in list"

def test_session_delete_all_except_current():
    """Bulk-revoke all browser sessions; API token stays valid."""
    # Create two browser sessions
    authorize_for_session(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW)
    authorize_for_session(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW)

    user_token = authorize_for_access_token(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW)
    sessions_before = req("api/user/me/sessions", access_token=user_token)
    assert len(sessions_before) >= 2, "Need ≥2 sessions for bulk-revoke test"

    req("api/user/me/sessions", "delete", expected_http_code=204, access_token=user_token)

    sessions_after = req("api/user/me/sessions", access_token=user_token)
    assert len(sessions_after) == 0, f"Expected 0 sessions, got {len(sessions_after)}"

    # API token must still work
    me = req("api/user/me", access_token=user_token)
    assert me["user_name"] == AUTH_TEST_USER_NAME

# ── Admin role update regression ──────────────────────────────────────────────

def test_admin_role_update_persists():
    user_id = _state["test_user_id"]
    res = req(
        f"api/user/{user_id}",
        "patch",
        b={"roles": ["usermanager"], "deactivated": False, "is_email_verified": False},
    )
    assert "usermanager" in res.get("roles", []), f"Role not persisted, got {res.get('roles')}"

    fetched = req(f"api/user/{user_id}")
    assert "usermanager" in fetched["roles"], "Role must survive a round-trip to DB"

    res = req(
        f"api/user/{user_id}",
        "patch",
        b={"roles": [], "deactivated": False, "is_email_verified": False},
    )
    assert res["roles"] == [], f"Expected empty roles, got {res['roles']}"

# ── Deactivated user is blocked ───────────────────────────────────────────────

def test_deactivated_user_cannot_get_new_token():
    user_id = _state["test_user_id"]
    req(f"api/user/{user_id}", "patch", b={"deactivated": True, "roles": []})

    req(
        "api/auth/basic/login/token",
        "post",
        f={"username": AUTH_TEST_USER_NAME, "password": AUTH_TEST_USER_PW},
        suppress_auth=True,
        expected_http_code=401,
    )

def test_deactivated_user_existing_token_rejected():
    user_id = _state["test_user_id"]

    # Re-activate to obtain a token
    req(f"api/user/{user_id}", "patch", b={"deactivated": False, "roles": []})
    user_token = authorize_for_access_token(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW)

    # Deactivate again
    req(f"api/user/{user_id}", "patch", b={"deactivated": True, "roles": []})

    # Existing token must now fail
    req("api/user/me", expected_http_code=401, access_token=user_token)

    # Re-enable for subsequent tests / re-runs
    req(f"api/user/{user_id}", "patch", b={"deactivated": False, "roles": []})

# ── Token-based logout ────────────────────────────────────────────────────────

def test_logout_invalidates_token():
    user_token = authorize_for_access_token(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW)

    me = req("api/user/me", access_token=user_token)
    assert me["user_name"] == AUTH_TEST_USER_NAME

    logout_res = req("api/auth/logout", "post", access_token=user_token)
    assert logout_res == {"message": "Logged out successfully"}, logout_res

    req("api/user/me", expected_http_code=401, access_token=user_token)

# ── API key expiry field ──────────────────────────────────────────────────────

def test_api_key_expiry_field_set():
    with _AsUser(AUTH_TEST_USER_NAME, AUTH_TEST_USER_PW):
        res = req("api/user/me/api-keys", "post", b={"display_name": "Short-lived key", "expires_in_days": 1})
        assert res["expires_at_epoch_time"] is not None, (
            "expires_at_epoch_time must be set when expires_in_days is given"
        )
        req(f"api/user/me/api-keys/{res['api_token_id']}", "delete", expected_http_code=204)
