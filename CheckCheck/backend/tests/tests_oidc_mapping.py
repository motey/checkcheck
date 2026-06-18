"""
Integration tests for the OIDC login flow and group→role mapping.

These exercise the full authorization-code flow against an in-process mock OIDC
provider (started by conftest.py). They are skipped automatically when the mock
provider is not available (OIDC_MOCK_SERVER_URL unset — e.g. the optional
``oidc_provider_mock`` dependency is not installed).

Covers:
  - first OIDC login auto-creates the user and applies ROLE_MAPPING
  - the user's OIDC groups are persisted on the user record
  - roles are re-applied (not just set once) on every login — removing the user
    from the mapped group revokes the role on re-login
  - userinfo (display name) is re-synced from the provider on every login
  - the session-based OIDC login path sets a usable session cookie
"""

import os

import pytest
import requests as _requests

from utils import req, oidc_login_get_token, oidc_login_get_session
from statics import (
    OIDC_TEST_PROVIDER_SLUG,
    OIDC_TEST_ROLE_GROUP,
    OIDC_TEST_MAPPED_ROLE,
)


def _require_oidc():
    if not os.environ.get("OIDC_MOCK_SERVER_URL"):
        pytest.skip("OIDC mock server not running — skipping OIDC mapping tests")
    return os.environ["OIDC_MOCK_SERVER_URL"]


def _set_mock_user(mock_url: str, sub: str, groups: list, given_name: str = None):
    """Create or update a user on the mock OIDC provider."""
    _requests.put(
        f"{mock_url}/users/{sub}",
        json={
            "sub": sub,
            "userinfo": {
                "name": sub,
                "email": f"{sub}@test.com",
                "given_name": given_name or sub,
                "groups": groups,
            },
        },
    ).raise_for_status()


def test_oidc_login_creates_user_and_maps_role():
    """First OIDC login auto-creates the user, maps the group to a role, and
    persists the OIDC groups on the user record."""
    _require_oidc()
    sub = "oidc-role-test-user"  # provisioned in conftest with OIDC_TEST_ROLE_GROUP

    token = oidc_login_get_token(OIDC_TEST_PROVIDER_SLUG, sub)
    me = req("api/user/me", access_token=token)

    assert me["user_name"] == sub
    assert OIDC_TEST_MAPPED_ROLE in me["roles"], (
        f"Expected '{OIDC_TEST_MAPPED_ROLE}' in roles after OIDC login, got: {me['roles']}"
    )
    assert OIDC_TEST_ROLE_GROUP in me.get("oidc_groups", []), (
        f"Expected OIDC group to be persisted on the user, got: {me.get('oidc_groups')}"
    )


def test_oidc_role_reapplied_on_relogin():
    """Roles must be re-applied on every login: removing the user from the mapped
    group revokes the role on the next login (not kept from the creation snapshot)."""
    mock_url = _require_oidc()
    sub = "oidc-relogin-test-user"  # provisioned in conftest with OIDC_TEST_ROLE_GROUP

    # First login — has the group → gets the mapped role
    token = oidc_login_get_token(OIDC_TEST_PROVIDER_SLUG, sub)
    me = req("api/user/me", access_token=token)
    assert OIDC_TEST_MAPPED_ROLE in me["roles"], (
        f"Expected '{OIDC_TEST_MAPPED_ROLE}' after first login, got: {me['roles']}"
    )

    # Remove the user from the mapped group on the provider
    _set_mock_user(mock_url, sub, groups=[])

    # Re-login — the role must be gone
    token2 = oidc_login_get_token(OIDC_TEST_PROVIDER_SLUG, sub)
    me2 = req("api/user/me", access_token=token2)
    assert OIDC_TEST_MAPPED_ROLE not in me2["roles"], (
        f"Expected '{OIDC_TEST_MAPPED_ROLE}' to be revoked after group removal, got: {me2['roles']}"
    )


def test_oidc_userinfo_synced_on_relogin():
    """The display name is re-synced from the OIDC provider on every login."""
    mock_url = _require_oidc()
    sub = "oidc-userinfo-sync-test-user"

    _set_mock_user(mock_url, sub, groups=[], given_name="Original Name")
    token = oidc_login_get_token(OIDC_TEST_PROVIDER_SLUG, sub)
    me = req("api/user/me", access_token=token)
    assert me["display_name"] == "Original Name", (
        f"Expected display_name='Original Name' after first login, got: {me['display_name']}"
    )

    # Change the display name on the provider, then re-login
    _set_mock_user(mock_url, sub, groups=[], given_name="Updated Name")
    token2 = oidc_login_get_token(OIDC_TEST_PROVIDER_SLUG, sub)
    me2 = req("api/user/me", access_token=token2)
    assert me2["display_name"] == "Updated Name", (
        f"Expected display_name to update to 'Updated Name' on re-login, got: {me2['display_name']}"
    )


def test_oidc_session_login():
    """The session-based OIDC login path sets a session cookie that authenticates."""
    mock_url = _require_oidc()
    sub = "oidc-session-test-user"
    _set_mock_user(mock_url, sub, groups=[])

    session = oidc_login_get_session(OIDC_TEST_PROVIDER_SLUG, sub)
    me = req("api/user/me", session=session)
    assert me["user_name"] == sub
