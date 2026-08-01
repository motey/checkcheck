"""Tests for chunk E3 of the notification sub-project: preferences.

Three layers, deliberately:

* The **resolver** (``notify/prefs.py``) is pure, so most of these tests build a
  ``UserNotificationSettings`` instance in memory, never save it, and assert on
  what ``resolve_mode`` says. No database, no HTTP, no fixtures.
* The **endpoints** are exercised over real HTTP with a dedicated user, the way
  the settings dialog will use them.
* The cases the running test instance's own configuration cannot produce (a type
  the administrator capped, an instance with mail switched off) call the endpoint
  function **in this process** with a substituted ``Config``, the same seam
  ``tests_notification_outbox.py`` uses for the test-mail 409s.

The plan is ``docs/plans/EMAIL_NOTIFICATIONS.md`` (sections 3.1, 4, 6, 7 E3).
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from utils import authorize_for_access_token, create_test_user, req


PREFS_USER_NAME = "notificationprefsuser"
PREFS_USER_PW = "prefsuserpw_secure1"
PREFS_USER_EMAIL = "notificationprefsuser@test.de"

# A second account that no test ever writes to, so "a user who never saved
# anything" cannot depend on the order the tests run in.
FRESH_USER_NAME = "notificationprefsfreshuser"
FRESH_USER_PW = "freshuserpw_secure1"
FRESH_USER_EMAIL = "notificationprefsfreshuser@test.de"


# ── helpers ───────────────────────────────────────────────────────────────────


def _run(body):
    """Run *body* with a fresh session against the test database.

    Same shape (and same reasons) as ``tests_notification_outbox._run``: lazy
    imports so nothing binds to the environment before the session fixtures have
    set it up, and the engine is disposed because every test drives its own event
    loop.
    """
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


def _config(**overrides):
    """A Config that looks like a configured instance, plus overrides."""
    from checkcheckserver.config import Config

    from statics import MAIL_CAPTURE_FROM_ADDRESS

    settings = {
        "EMAIL_ENABLED": True,
        "EMAIL_TRANSPORT": "null",
        "EMAIL_FROM_ADDRESS": MAIL_CAPTURE_FROM_ADDRESS,
    }
    settings.update(overrides)
    return Config(**settings)


def _settings(prefs=None, **fields):
    """An unsaved settings row, which is all the resolver ever needs."""
    from checkcheckserver.model.user_notification_settings import (
        UserNotificationSettings,
    )

    return UserNotificationSettings(
        user_id=uuid.uuid4(), prefs=prefs or {}, **fields
    )


def _channel(settings_response: dict, type: str, channel: str) -> dict:
    entry = [t for t in settings_response["types"] if t["type"] == type]
    assert entry, f"{type} missing from the settings response"
    return entry[0]["channels"][channel]


@pytest.fixture(scope="module")
def prefs_user():
    """A dedicated account the write tests own."""
    user = create_test_user(PREFS_USER_NAME, PREFS_USER_PW, PREFS_USER_EMAIL)
    token = authorize_for_access_token(PREFS_USER_NAME, PREFS_USER_PW)
    return SimpleNamespace(id=uuid.UUID(user["id"]), token=token)


@pytest.fixture(scope="module")
def fresh_user():
    """An account nothing in this module ever writes to."""
    user = create_test_user(FRESH_USER_NAME, FRESH_USER_PW, FRESH_USER_EMAIL)
    token = authorize_for_access_token(FRESH_USER_NAME, FRESH_USER_PW)
    return SimpleNamespace(id=uuid.UUID(user["id"]), token=token)


# ── the resolution matrix ─────────────────────────────────────────────────────


def test_an_entry_the_user_never_touched_uses_the_instance_default():
    from checkcheckserver.notify.prefs import NotificationMode, resolve_mode

    config = _config(
        NOTIFY_DEFAULT_MODES={"card_shared": {"email": "daily", "in_app": "off"}}
    )

    # No row at all, and a row that simply says nothing about this entry, must
    # resolve the same way: both mean "whatever the instance says".
    for settings in (None, _settings({"card_invited": {"email": "off"}})):
        assert (
            resolve_mode(settings, "card_shared", "email", config=config)
            == NotificationMode.daily
        )
        assert (
            resolve_mode(settings, "card_shared", "in_app", config=config)
            == NotificationMode.off
        )


def test_the_user_choice_wins_over_the_instance_default():
    from checkcheckserver.notify.prefs import NotificationMode, resolve_mode

    config = _config(
        NOTIFY_DEFAULT_MODES={"card_shared": {"email": "immediate", "in_app": "immediate"}}
    )
    settings = _settings({"card_shared": {"email": "off"}})

    assert (
        resolve_mode(settings, "card_shared", "email", config=config)
        == NotificationMode.off
    )
    # ... and only for the entry they actually chose.
    assert (
        resolve_mode(settings, "card_shared", "in_app", config=config)
        == NotificationMode.immediate
    )


def test_the_admin_cap_beats_the_user_choice():
    """A type in NOTIFY_DISABLED_TYPES is off for everybody, on every channel,
    whatever the user saved earlier (they may have chosen it before the
    administrator capped it)."""
    from checkcheckserver.notify.prefs import (
        NotificationMode,
        PreferenceChannel,
        lock_reason,
        resolve_mode,
    )

    config = _config(
        NOTIFY_DISABLED_TYPES=["public_link_opened"],
        NOTIFY_DEFAULT_MODES={"public_link_opened": {"email": "immediate"}},
    )
    settings = _settings(
        {"public_link_opened": {"email": "immediate", "in_app": "immediate"}}
    )

    for channel in PreferenceChannel:
        assert (
            resolve_mode(settings, "public_link_opened", channel, config=config)
            == NotificationMode.off
        )
        assert lock_reason("public_link_opened", channel, config) is not None

    # Other types are untouched by the cap.
    assert (
        resolve_mode(settings, "card_shared", "in_app", config=config)
        == NotificationMode.immediate
    )
    assert lock_reason("card_shared", PreferenceChannel.in_app, config) is None


def test_a_channel_the_instance_cannot_deliver_is_off_for_everybody():
    """The master switches are the other half of the hard cap: a user cannot opt
    into mail on an instance that does not send mail."""
    from checkcheckserver.notify.prefs import (
        NotificationMode,
        PreferenceChannel,
        lock_reason,
        resolve_mode,
    )

    config = _config(
        EMAIL_ENABLED=False,
        EMAIL_FROM_ADDRESS=None,
        NOTIFY_WEBHOOK_ENABLED=False,
        NOTIFY_DEFAULT_MODES={"card_shared": {"email": "immediate"}},
    )
    settings = _settings(
        {"card_shared": {"email": "immediate", "webhook": "immediate"}}
    )

    assert (
        resolve_mode(settings, "card_shared", "email", config=config)
        == NotificationMode.off
    )
    assert (
        resolve_mode(settings, "card_shared", "webhook", config=config)
        == NotificationMode.off
    )
    assert lock_reason("card_shared", PreferenceChannel.email, config) is not None
    # The bell has no master switch: the in-app feed is the base feature.
    assert lock_reason("card_shared", PreferenceChannel.in_app, config) is None
    assert (
        resolve_mode(settings, "card_shared", "in_app", config=config)
        == NotificationMode.immediate
    )


def test_a_new_notification_type_needs_no_migration_and_no_backfill():
    """The property the JSON column exists for.

    A type that did not exist when a user saved their preferences resolves for
    them anyway: to the code default while nobody has configured it, and to the
    instance default the moment an operator adds one. Their stored row is not
    read for it, not written to, and needs no migration.
    """
    from checkcheckserver.notify.prefs import NotificationMode, resolve_mode

    # A row saved long before the new type was invented.
    settings = _settings({"card_shared": {"email": "daily"}})
    stored_before = dict(settings.prefs)
    new_type = "reminder_due"  # the date-reminder feature, section 8 of the plan

    plain = _config()
    # Code default: the bell is on, mail is off. A type nobody configured must
    # never start writing to somebody's inbox on its own.
    assert resolve_mode(settings, new_type, "in_app", config=plain) == (
        NotificationMode.immediate
    )
    assert resolve_mode(settings, new_type, "email", config=plain) == (
        NotificationMode.off
    )

    # The operator declares a default for it: no migration, no backfill, no
    # restart of anything but the process that reads the config.
    configured = _config(NOTIFY_DEFAULT_MODES={new_type: {"email": "immediate"}})
    assert resolve_mode(settings, new_type, "email", config=configured) == (
        NotificationMode.immediate
    )
    # And the user can still override it, with the same untouched row shape.
    opted_out = _settings({**stored_before, new_type: {"email": "off"}})
    assert resolve_mode(opted_out, new_type, "email", config=configured) == (
        NotificationMode.off
    )
    assert settings.prefs == stored_before


def test_an_unusable_stored_mode_falls_through_instead_of_breaking():
    """Both preference sources are hand-editable, so garbage in one of them must
    cost that one entry, not every notification on the instance."""
    from checkcheckserver.notify.prefs import NotificationMode, resolve_mode

    config = _config(
        NOTIFY_DEFAULT_MODES={
            "card_shared": {"email": "immediate"},
            # A digest makes no sense for the bell: the instance default itself
            # is unusable here and must be ignored.
            "card_invited": {"in_app": "hourly", "email": "immediate"},
        }
    )
    settings = _settings(
        {
            # Modes that do not exist, and one that this channel cannot honour.
            "card_shared": {"email": "sometimes", "in_app": 7},
            "card_invited": {"in_app": "daily"},
        }
    )

    assert resolve_mode(settings, "card_shared", "email", config=config) == (
        NotificationMode.immediate  # fell through to the instance default
    )
    assert resolve_mode(settings, "card_shared", "in_app", config=config) == (
        NotificationMode.immediate  # ... and on to the code default
    )
    assert resolve_mode(settings, "card_invited", "in_app", config=config) == (
        NotificationMode.immediate  # user value and instance default both unusable
    )
    assert resolve_mode(settings, "card_invited", "email", config=config) == (
        NotificationMode.immediate
    )


def test_only_email_offers_a_digest():
    from checkcheckserver.notify.prefs import PreferenceChannel, allowed_modes

    assert allowed_modes(PreferenceChannel.in_app) == ["off", "immediate"]
    assert allowed_modes(PreferenceChannel.webhook) == ["off", "immediate"]
    assert allowed_modes(PreferenceChannel.email) == [
        "off",
        "immediate",
        "hourly",
        "daily",
    ]


# ── the write path, without HTTP ──────────────────────────────────────────────


def test_a_patch_merges_partially_and_a_null_restores_the_default():
    from checkcheckserver.notify.prefs import apply_prefs_patch

    current = {
        "card_shared": {"email": "daily", "in_app": "off"},
        "card_invited": {"email": "off"},
    }

    # One cell changes; every other cell, including the other channel of the same
    # type, is left exactly as it was.
    merged = apply_prefs_patch(current, {"card_shared": {"email": "immediate"}})
    assert merged == {
        "card_shared": {"email": "immediate", "in_app": "off"},
        "card_invited": {"email": "off"},
    }
    # The stored dict is never mutated in place (SQLAlchemy would not notice).
    assert current["card_shared"]["email"] == "daily"

    # Null removes the choice, and an emptied type does not linger as an empty
    # object in the row.
    cleared = apply_prefs_patch(current, {"card_invited": {"email": None}})
    assert "card_invited" not in cleared
    assert cleared["card_shared"] == {"email": "daily", "in_app": "off"}


def test_validation_rejects_what_the_server_will_not_store():
    from checkcheckserver.notify.prefs import (
        LockedPreferenceError,
        UnknownPreferenceError,
        validate_prefs_patch,
    )

    config = _config(NOTIFY_DISABLED_TYPES=["public_link_opened"])

    with pytest.raises(UnknownPreferenceError):
        validate_prefs_patch({"no_such_type": {"email": "off"}}, config=config)
    with pytest.raises(UnknownPreferenceError):
        validate_prefs_patch({"card_shared": {"carrier_pigeon": "off"}}, config=config)
    with pytest.raises(UnknownPreferenceError):
        validate_prefs_patch({"card_shared": {"email": "whenever"}}, config=config)
    with pytest.raises(UnknownPreferenceError):
        # A real mode, but not one the bell can do.
        validate_prefs_patch({"card_shared": {"in_app": "daily"}}, config=config)
    with pytest.raises(LockedPreferenceError):
        validate_prefs_patch(
            {"public_link_opened": {"email": "immediate"}}, config=config
        )

    # Dropping an override is always allowed, even where the administrator has
    # capped the entry: it only ever removes something.
    validate_prefs_patch({"public_link_opened": {"email": None}}, config=config)
    validate_prefs_patch({"card_shared": {"email": "daily"}}, config=config)


def test_a_time_zone_is_checked_against_the_iana_database():
    from checkcheckserver.notify.prefs import UnknownPreferenceError, validate_timezone

    assert validate_timezone("Europe/Berlin") == "Europe/Berlin"
    assert validate_timezone("UTC") == "UTC"
    assert validate_timezone("  Pacific/Auckland  ") == "Pacific/Auckland"

    for bad in ["Europe/Berlim", "CET+2", "", "   ", "x" * 100]:
        with pytest.raises(UnknownPreferenceError):
            validate_timezone(bad)


def test_the_settings_row_is_created_once_and_reused(prefs_user):
    """Lazy creation, and what happens when two requests arrive together."""
    from checkcheckserver.db.user_notification_settings import get_or_create_settings

    async def body(session):
        first = await get_or_create_settings(session, prefs_user.id)
        second = await get_or_create_settings(session, prefs_user.id)
        return first, second

    first, second = _run(body)

    assert first.user_id == second.user_id == prefs_user.id
    # A secret is minted with the row, not left null for chunk E4 to fix up.
    assert first.unsubscribe_secret and first.unsubscribe_secret == (
        second.unsubscribe_secret
    )


# ── the endpoints ─────────────────────────────────────────────────────────────


def test_a_user_who_never_saved_anything_gets_the_defaults_and_no_row(fresh_user):
    from checkcheckserver.db.user_notification_settings import get_settings

    settings = req(
        "api/user/me/notification-settings", access_token=fresh_user.token
    )

    types = {entry["type"] for entry in settings["types"]}
    assert types == {"card_shared", "card_invited", "public_link_opened"}
    assert settings["timezone"] is None
    assert settings["email_enabled"] is True  # the test instance has mail on

    shared_email = _channel(settings, "card_shared", "email")
    assert shared_email["mode"] == "immediate"  # the shipped instance default
    assert shared_email["user_choice"] is None  # inherited, not chosen
    assert shared_email["default_mode"] == "immediate"
    assert shared_email["locked"] is False
    assert shared_email["allowed_modes"] == ["off", "immediate", "hourly", "daily"]

    # The webhook channel is off instance-wide in the test environment, so every
    # webhook entry is locked with a reason the dialog can show.
    shared_webhook = _channel(settings, "card_shared", "webhook")
    assert shared_webhook["locked"] is True
    assert shared_webhook["locked_reason"]
    assert shared_webhook["mode"] == "off"

    # Reading must not have created anything.
    async def body(session):
        return await get_settings(session, fresh_user.id)

    assert _run(body) is None


def test_put_saves_a_partial_change_that_survives_a_reload(prefs_user):
    saved = req(
        "api/user/me/notification-settings",
        "put",
        b={"prefs": {"card_shared": {"email": "daily"}}, "timezone": "Europe/Berlin"},
        access_token=prefs_user.token,
    )
    reloaded = req(
        "api/user/me/notification-settings", access_token=prefs_user.token
    )

    for settings in (saved, reloaded):
        entry = _channel(settings, "card_shared", "email")
        assert entry["mode"] == "daily"
        assert entry["user_choice"] == "daily"
        # The instance default is still reported, so the dialog can offer to go
        # back to it.
        assert entry["default_mode"] == "immediate"
        assert settings["timezone"] == "Europe/Berlin"
        # Nothing else moved.
        assert _channel(settings, "card_shared", "in_app")["user_choice"] is None
        assert _channel(settings, "card_invited", "email")["user_choice"] is None
        assert _channel(settings, "card_invited", "email")["mode"] == "immediate"


def test_put_with_a_null_gives_an_entry_back_to_the_instance_default(prefs_user):
    req(
        "api/user/me/notification-settings",
        "put",
        b={"prefs": {"card_invited": {"email": "off"}}},
        access_token=prefs_user.token,
    )
    assert (
        _channel(
            req("api/user/me/notification-settings", access_token=prefs_user.token),
            "card_invited",
            "email",
        )["mode"]
        == "off"
    )

    restored = req(
        "api/user/me/notification-settings",
        "put",
        b={"prefs": {"card_invited": {"email": None}}},
        access_token=prefs_user.token,
    )
    entry = _channel(restored, "card_invited", "email")
    assert entry["user_choice"] is None
    assert entry["mode"] == "immediate"


def test_put_clears_the_time_zone_with_an_explicit_null(prefs_user):
    req(
        "api/user/me/notification-settings",
        "put",
        b={"timezone": "Pacific/Auckland"},
        access_token=prefs_user.token,
    )
    # A body that does not mention the time zone leaves it alone ...
    kept = req(
        "api/user/me/notification-settings",
        "put",
        b={"prefs": {"card_shared": {"in_app": "immediate"}}},
        access_token=prefs_user.token,
    )
    assert kept["timezone"] == "Pacific/Auckland"

    # ... an explicit null clears it.
    cleared = req(
        "api/user/me/notification-settings",
        "put",
        b={"timezone": None},
        access_token=prefs_user.token,
    )
    assert cleared["timezone"] is None


def test_put_rejects_unknown_types_channels_modes_and_time_zones(prefs_user):
    for body in [
        {"prefs": {"no_such_type": {"email": "off"}}},
        {"prefs": {"card_shared": {"carrier_pigeon": "off"}}},
        {"prefs": {"card_shared": {"email": "whenever"}}},
        {"prefs": {"card_shared": {"in_app": "daily"}}},  # digest on the bell
        {"timezone": "Middle/Earth"},
    ]:
        req(
            "api/user/me/notification-settings",
            "put",
            b=body,
            expected_http_code=400,
            access_token=prefs_user.token,
        )


def test_put_rejects_a_channel_this_instance_does_not_have(prefs_user):
    """Webhooks are off in the test environment, so both the mode and the target
    URL are refused, with 409 rather than 400: the request is well formed, the
    server just does not allow it."""
    req(
        "api/user/me/notification-settings",
        "put",
        b={"prefs": {"card_shared": {"webhook": "immediate"}}},
        expected_http_code=409,
        access_token=prefs_user.token,
    )
    req(
        "api/user/me/notification-settings",
        "put",
        b={"webhook_url": "https://example.com/hook"},
        expected_http_code=409,
        access_token=prefs_user.token,
    )


def test_put_rejects_a_type_the_administrator_capped(prefs_user):
    """Called in this process with a substituted Config: the running test server
    caps nothing, and the cap is an instance-wide setting, not a per-request one."""
    from fastapi import HTTPException

    from checkcheckserver.api.routes import routes_notification_settings as endpoint
    from checkcheckserver.db.user import User

    capped = _config(NOTIFY_DISABLED_TYPES=["public_link_opened"])

    async def call(session, body: dict) -> int:
        user = await session.get(User, prefs_user.id)
        original = endpoint.config
        endpoint.config = capped
        try:
            await endpoint.update_notification_settings(
                update=endpoint.NotificationSettingsUpdate(**body),
                current_user=user,
                session=session,
            )
        except HTTPException as exc:
            return exc.status_code
        finally:
            endpoint.config = original
        return 200

    async def body(session):
        refused = await call(
            session, {"prefs": {"public_link_opened": {"email": "immediate"}}}
        )
        # Dropping an override stays legal even for a capped type.
        dropped = await call(
            session, {"prefs": {"public_link_opened": {"email": None}}}
        )
        # And an uncapped type is unaffected.
        allowed = await call(session, {"prefs": {"card_shared": {"email": "hourly"}}})
        return refused, dropped, allowed

    refused, dropped, allowed = _run(body)

    assert refused == 409
    assert dropped == 200
    assert allowed == 200


def test_a_capped_type_reads_back_as_locked_and_off(prefs_user):
    """What the settings dialog renders when an administrator disabled a type."""
    from checkcheckserver.api.routes import routes_notification_settings as endpoint
    from checkcheckserver.db.user import User

    capped = _config(NOTIFY_DISABLED_TYPES=["public_link_opened"])

    async def body(session):
        user = await session.get(User, prefs_user.id)
        original = endpoint.config
        endpoint.config = capped
        try:
            result = await endpoint.get_notification_settings(
                current_user=user, session=session
            )
        finally:
            endpoint.config = original
        return result.model_dump(mode="json")

    settings = _run(body)

    entry = _channel(settings, "public_link_opened", "email")
    assert entry["locked"] is True
    assert "administrator" in entry["locked_reason"].lower()
    assert entry["mode"] == "off"
    # A type that is not capped stays the user's to decide.
    assert _channel(settings, "card_shared", "email")["locked"] is False


def test_settings_need_a_logged_in_user():
    req(
        "api/user/me/notification-settings",
        expected_http_code=401,
        suppress_auth=True,
    )
    req(
        "api/user/me/notification-settings",
        "put",
        b={"prefs": {}},
        expected_http_code=401,
        suppress_auth=True,
    )
