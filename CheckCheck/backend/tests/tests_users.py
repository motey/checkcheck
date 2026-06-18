from typing import List, Dict
from utils import (
    req,
    dict_must_contain,
    list_contains_dict_that_must_contain,
    find_first_dict_in_list,
    authorize_for_access_token,
)
from statics import ADMIN_USER_EMAIL, ADMIN_USER_NAME, TEST_USER_NAME, TEST_USER_PW

def _get_user_id(username: str) -> str:
    res = req("api/user", q={"incl_deactivated": True})
    user = find_first_dict_in_list(res["items"], {"user_name": username})
    return user["id"]

def test_user_me():
    res = req("api/user/me")
    dict_must_contain(
        res,
        {"email": ADMIN_USER_EMAIL, "user_name": ADMIN_USER_NAME},
        exception_dict_identifier="user/me",
    )

def test_user_create_with_no_password():
    res = req(
        "api/user",
        "post",
        b={
            "email": "test_user_wo_pw@test.com",
            "display_name": "No PW User",
            "user_name": "nopwuser01",
        },
        tolerated_error_body=[{"detail": "User allready exists"}],
    )
    if res != {"detail": "User allready exists"}:
        dict_must_contain(res, required_keys=["created_at", "roles", "id"])

def test_duplicate_username_rejected():
    import requests as _requests
    try:
        req("api/user", "post", b={"email": "dup@test.com", "user_name": "nopwuser01"})
        raise AssertionError("Expected HTTP error for duplicate username, but got success")
    except _requests.HTTPError as err:
        assert err.response.status_code in (400, 401, 409, 422), (
            f"Expected 4xx for duplicate user, got {err.response.status_code}"
        )

def test_user_list_includes_admin():
    res = req("api/user", q={"incl_deactivated": True})
    list_contains_dict_that_must_contain(
        res["items"],
        {"email": ADMIN_USER_EMAIL, "user_name": ADMIN_USER_NAME},
        exception_dict_identifier="user list (admin)",
    )

def test_set_other_user_password_as_admin():
    """Admin can set another user's password via PUT /user/{id}/password."""
    user_id = _get_user_id(TEST_USER_NAME)
    req(
        f"api/user/{user_id}/password",
        "put",
        f={"new_password": TEST_USER_PW, "new_password_repeated": TEST_USER_PW},
    )

def test_role_update_persists():
    """PATCH /user/{id} must actually persist role changes (regression guard)."""
    user_id = _get_user_id(TEST_USER_NAME)
    res = req(
        f"api/user/{user_id}",
        "patch",
        b={"roles": ["usermanager"], "deactivated": False, "is_email_verified": False},
    )
    assert "usermanager" in res.get("roles", []), (
        f"Role was not persisted, got {res.get('roles')}"
    )
    # Reset
    req(
        f"api/user/{user_id}",
        "patch",
        b={"roles": [], "deactivated": False, "is_email_verified": False},
    )
