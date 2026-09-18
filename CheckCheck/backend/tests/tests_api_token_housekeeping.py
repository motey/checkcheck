"""Housekeeping for API keys (TOKEN_API_REVIEW.md, chunk 4).

* ``last_used_at`` is written at most once per ``LAST_USED_AT_WRITE_INTERVAL``.
* An admin can list the keys of a deactivated user.
* Migration 0020 builds the unique index on ``user_auth.api_token_id`` and stops
  with a readable error when duplicate ids exist.

The first two run against the test server; the migration test runs the revision
against a throwaway in-memory SQLite database.
"""

import datetime
import importlib.util
import pathlib
import uuid

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from checkcheckserver.model.user_auth import UserAuth

from utils import req, authorize_for_session, create_test_user
from tests_api_token_oidc import _run


def _local_user_session(user_name: str):
    pw = f"{user_name}_pw_secure1"
    user = create_test_user(user_name, pw, f"{user_name}@test.de")
    return user, authorize_for_session(user_name, pw)


def _last_used_at(session, api_token_id: str) -> datetime.datetime | None:
    keys = req("api/user/me/api-keys", session=session)
    value = next(k for k in keys if k["api_token_id"] == api_token_id)["last_used_at"]
    return None if value is None else datetime.datetime.fromisoformat(value)


def _backdate_last_used_at(api_token_id: str, minutes: int):
    from sqlmodel import update

    when = datetime.datetime.now(datetime.timezone.utc).replace(
        tzinfo=None
    ) - datetime.timedelta(minutes=minutes)

    async def body(session):
        await session.exec(
            update(UserAuth)
            .where(UserAuth.api_token_id == api_token_id)
            .values(last_used_at=when)
        )
        await session.commit()

    _run(body)


# ── last_used_at throttle ─────────────────────────────────────────────────────


def test_last_used_at_is_written_at_most_once_per_minute():
    _, s = _local_user_session("housekeeping_last_used")
    key = req("api/user/me/api-keys", "post", b={"display_name": "busy script"}, session=s)
    key_id = key["api_token_id"]
    assert _last_used_at(s, key_id) is None

    req("api/user/me", access_token=key["token"])
    first = _last_used_at(s, key_id)
    assert first is not None

    # Within the interval the stored value stays as it is.
    req("api/user/me", access_token=key["token"])
    assert _last_used_at(s, key_id) == first

    # Once the stored value is older than the interval, the next use writes again.
    _backdate_last_used_at(key_id, minutes=2)
    backdated = _last_used_at(s, key_id)
    req("api/user/me", access_token=key["token"])
    refreshed = _last_used_at(s, key_id)
    assert refreshed > backdated


# ── Admin listing of a deactivated user ───────────────────────────────────────


def test_admin_lists_keys_of_deactivated_user():
    user, s = _local_user_session("housekeeping_deactivated")
    key = req("api/user/me/api-keys", "post", b={"display_name": "left over"}, session=s)
    user_id = user["id"]

    req(f"api/user/{user_id}", "patch", b={"deactivated": True, "roles": []})
    try:
        keys = req(f"api/user/{user_id}/api-keys")
        assert [k["api_token_id"] for k in keys] == [key["api_token_id"]]
        req(
            f"api/user/{user_id}/api-keys/{key['api_token_id']}",
            "delete",
            expected_http_code=204,
        )
        assert req(f"api/user/{user_id}/api-keys") == []
    finally:
        req(f"api/user/{user_id}", "patch", b={"deactivated": False, "roles": []})


def test_admin_list_of_unknown_user_is_404():
    req(f"api/user/{uuid.uuid4()}/api-keys", expected_http_code=404)


# ── Migration 0020 ────────────────────────────────────────────────────────────

_MIGRATION = (
    pathlib.Path(__file__).parent.parent
    / "migrations"
    / "versions"
    / "0020_user_auth_api_token_id_unique.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0020", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _db_with_token_ids(*token_ids):
    """An in-memory user_auth table as a pre-0020 database has it (no index)."""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text("CREATE TABLE user_auth (id INTEGER PRIMARY KEY, api_token_id VARCHAR)")
        )
        for token_id in token_ids:
            conn.execute(
                sa.text("INSERT INTO user_auth (api_token_id) VALUES (:t)"),
                {"t": token_id},
            )
    return engine


def _upgrade(engine, migration):
    with engine.begin() as conn:
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()


def _indexes(engine):
    return {ix["name"]: ix for ix in sa.inspect(engine).get_indexes("user_auth")}


def test_migration_creates_unique_index_and_is_idempotent():
    migration = _load_migration()
    # Many NULLs (non-token logins) are fine in a unique index.
    engine = _db_with_token_ids("a", "b", None, None)

    _upgrade(engine, migration)
    index = _indexes(engine)["ix_user_auth_api_token_id"]
    assert index["unique"]
    assert index["column_names"] == ["api_token_id"]

    # A second run (fresh DB where create_all built the index) is a no-op.
    _upgrade(engine, migration)

    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO user_auth (api_token_id) VALUES ('a')"))


def test_migration_refuses_duplicate_token_ids():
    migration = _load_migration()
    engine = _db_with_token_ids("dup", "dup", "other")

    with pytest.raises(RuntimeError, match="'dup' \\(2 rows\\)"):
        _upgrade(engine, migration)
    assert "ix_user_auth_api_token_id" not in _indexes(engine)
