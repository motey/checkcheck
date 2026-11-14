"""Indexed env vars set entries of list settings (GitHub issue #6).

``AUTH_OIDC_PROVIDERS__0__CLIENT_SECRET`` used to be rejected at startup:
pydantic-settings built ``{"0": {...}}`` and the list field refused the dict.
``checkcheckserver.config_sources.LayeredSettingsSource`` now turns index keys
into list entries and merges them field by field with what config.yml (or a
JSON value) defines at the same index, so secrets can stay in the environment.
"""

import json
import os

import pytest
from pydantic_settings import SettingsError

import checkcheckserver.config as config_module
from checkcheckserver.config import Config

DISCOVERY = "https://idp.example.com/.well-known/openid-configuration"

TOUCHED_PREFIXES = (
    "AUTH_OIDC_PROVIDERS",
    "NEW_USER_DEFAULT_LABELS",
    "NOTIFY_DEFAULT_MODES",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Start each test without list env vars and with an empty config.yml path.

    The suite's OIDC mock exports AUTH_OIDC_PROVIDERS as JSON for the server
    subprocess, so it is in this process's environment too.
    """
    for name in list(os.environ):
        if name.upper().startswith(TOUCHED_PREFIXES):
            monkeypatch.delenv(name)
    monkeypatch.setattr(
        config_module, "config_file_path", str(tmp_path / "config.yml")
    )
    return tmp_path


def write_yaml(tmp_path, text: str) -> None:
    (tmp_path / "config.yml").write_text(text)


YAML_ONE_PROVIDER = f"""
AUTH_OIDC_PROVIDERS:
  - ENABLED: true
    PROVIDER_DISPLAY_NAME: Company SSO
    CONFIGURATION_ENDPOINT: {DISCOVERY}
    CLIENT_ID: checkcheck
    CLIENT_SECRET: from-the-file
    SCOPES: [openid, profile]
"""


def test_indexed_env_vars_alone_build_the_provider_list(monkeypatch):
    # The exact reproduction from the issue: no config.yml, env vars only.
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__ENABLED", "true")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__CLIENT_ID", "checkcheck")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__CLIENT_SECRET", "secret")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__CONFIGURATION_ENDPOINT", DISCOVERY)
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__1__CLIENT_ID", "second")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__1__CLIENT_SECRET", "secret-2")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__1__CONFIGURATION_ENDPOINT", DISCOVERY)
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__1__PROVIDER_DISPLAY_NAME", "Second")

    providers = Config().AUTH_OIDC_PROVIDERS

    assert [p.CLIENT_ID for p in providers] == ["checkcheck", "second"]
    first, second = providers
    assert first.ENABLED is True
    assert first.CLIENT_SECRET.get_secret_value() == "secret"
    assert first.CONFIGURATION_ENDPOINT == DISCOVERY
    assert second.ENABLED is False  # field default
    assert second.PROVIDER_DISPLAY_NAME == "Second"


def test_nested_values_inside_an_entry_take_json_and_indexes(monkeypatch):
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__CLIENT_ID", "checkcheck")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__CLIENT_SECRET", "secret")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__CONFIGURATION_ENDPOINT", DISCOVERY)
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__SCOPES", '["openid", "email"]')
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__ROLE_MAPPING__admins", '["admin"]')
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__ROLE_MAPPING__managers__0", "usermanager")

    (provider,) = Config().AUTH_OIDC_PROVIDERS

    assert provider.SCOPES == ["openid", "email"]
    assert provider.ROLE_MAPPING == {"admins": ["admin"], "managers": ["usermanager"]}


def test_indexed_env_var_fills_in_one_field_of_a_yaml_entry(monkeypatch, clean_env):
    # The secret-injection pattern: shape in config.yml, secret in the env.
    write_yaml(clean_env, YAML_ONE_PROVIDER)
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__CLIENT_SECRET", "from-the-env")

    (provider,) = Config().AUTH_OIDC_PROVIDERS

    assert provider.CLIENT_SECRET.get_secret_value() == "from-the-env"
    assert provider.PROVIDER_DISPLAY_NAME == "Company SSO"
    assert provider.CLIENT_ID == "checkcheck"
    assert provider.ENABLED is True
    assert provider.SCOPES == ["openid", "profile"]


def test_indexed_env_var_one_past_the_yaml_list_appends(monkeypatch, clean_env):
    write_yaml(clean_env, YAML_ONE_PROVIDER)
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__1__CLIENT_ID", "second")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__1__CLIENT_SECRET", "secret-2")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__1__CONFIGURATION_ENDPOINT", DISCOVERY)

    providers = Config().AUTH_OIDC_PROVIDERS

    assert [p.CLIENT_ID for p in providers] == ["checkcheck", "second"]
    assert providers[0].CLIENT_SECRET.get_secret_value() == "from-the-file"


def test_indexed_scope_patches_one_item_of_a_yaml_scope_list(monkeypatch, clean_env):
    write_yaml(clean_env, YAML_ONE_PROVIDER)
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__SCOPES__2", "offline_access")

    (provider,) = Config().AUTH_OIDC_PROVIDERS

    assert provider.SCOPES == ["openid", "profile", "offline_access"]


def test_plain_string_list_from_indexed_env_vars(monkeypatch):
    monkeypatch.setenv("NEW_USER_DEFAULT_LABELS__0", "Home")
    monkeypatch.setenv("NEW_USER_DEFAULT_LABELS__1", "Garden")
    assert Config().NEW_USER_DEFAULT_LABELS == ["Home", "Garden"]


def test_indexed_env_vars_do_not_merge_with_the_built_in_default(monkeypatch):
    # Same rule as for dict settings: a field default is not a layer, so the
    # default ["Work", "Private", "Inspiration"] is replaced, not patched.
    monkeypatch.setenv("NEW_USER_DEFAULT_LABELS__0", "Home")
    assert Config().NEW_USER_DEFAULT_LABELS == ["Home"]


def test_indexed_env_var_replaces_one_item_of_a_yaml_string_list(
    monkeypatch, clean_env
):
    write_yaml(clean_env, "NEW_USER_DEFAULT_LABELS: [A, B, C]\n")
    monkeypatch.setenv("NEW_USER_DEFAULT_LABELS__1", "X")
    assert Config().NEW_USER_DEFAULT_LABELS == ["A", "X", "C"]


def test_whole_json_list_still_replaces_the_yaml_list(monkeypatch, clean_env):
    write_yaml(clean_env, "NEW_USER_DEFAULT_LABELS: [A, B, C]\n")
    monkeypatch.setenv("NEW_USER_DEFAULT_LABELS", '["only"]')
    assert Config().NEW_USER_DEFAULT_LABELS == ["only"]


def test_whole_json_provider_list_still_works(monkeypatch):
    # The form the test server itself is started with (conftest's OIDC mock).
    monkeypatch.setenv(
        "AUTH_OIDC_PROVIDERS",
        json.dumps(
            [
                {
                    "ENABLED": True,
                    "CLIENT_ID": "json",
                    "CLIENT_SECRET": "s",
                    "CONFIGURATION_ENDPOINT": DISCOVERY,
                }
            ]
        ),
    )
    (provider,) = Config().AUTH_OIDC_PROVIDERS
    assert provider.CLIENT_ID == "json"


def test_indexed_env_vars_patch_a_json_list_from_the_same_source(monkeypatch):
    # pydantic-settings used to drop the indexed var silently here.
    monkeypatch.setenv(
        "AUTH_OIDC_PROVIDERS",
        json.dumps(
            [{"CLIENT_ID": "json", "CONFIGURATION_ENDPOINT": DISCOVERY}]
        ),
    )
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__CLIENT_SECRET", "secret")

    (provider,) = Config().AUTH_OIDC_PROVIDERS

    assert provider.CLIENT_ID == "json"
    assert provider.CLIENT_SECRET.get_secret_value() == "secret"


def test_indexed_env_var_patches_a_json_list_from_the_dot_env_file(
    monkeypatch, tmp_path
):
    dot_env = tmp_path / "list.env"
    dot_env.write_text(
        "AUTH_OIDC_PROVIDERS="
        + json.dumps([{"CLIENT_ID": "dotenv", "CONFIGURATION_ENDPOINT": DISCOVERY}])
        + "\n"
        + "NEW_USER_DEFAULT_LABELS__0=FromDotEnv\n"
    )
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__CLIENT_SECRET", "from-the-env")
    monkeypatch.setenv("NEW_USER_DEFAULT_LABELS__1", "FromEnv")

    # Keep the suite's defaults file for the required secrets, add the list one.
    config = Config(_env_file=(config_module.env_file_path, str(dot_env)))

    (provider,) = config.AUTH_OIDC_PROVIDERS
    assert provider.CLIENT_ID == "dotenv"
    assert provider.CLIENT_SECRET.get_secret_value() == "from-the-env"
    assert config.NEW_USER_DEFAULT_LABELS == ["FromDotEnv", "FromEnv"]


def test_lower_case_env_var_names_still_match(monkeypatch, clean_env):
    write_yaml(clean_env, YAML_ONE_PROVIDER)
    monkeypatch.setenv("auth_oidc_providers__0__client_secret", "lower")
    (provider,) = Config().AUTH_OIDC_PROVIDERS
    assert provider.CLIENT_SECRET.get_secret_value() == "lower"


def test_dict_settings_still_deep_merge_with_the_yaml_file(monkeypatch, clean_env):
    write_yaml(
        clean_env,
        "NOTIFY_DEFAULT_MODES:\n  card_shared:\n    in_app: daily\n",
    )
    monkeypatch.setenv("NOTIFY_DEFAULT_MODES__card_shared__email", "off")
    modes = Config().NOTIFY_DEFAULT_MODES
    assert modes["card_shared"] == {"in_app": "daily", "email": "off"}


# ── Clear errors ──────────────────────────────────────────────────────────────


def _config_error() -> str:
    with pytest.raises(SettingsError) as excinfo:
        Config()
    # pydantic-settings wraps errors raised while reading the env in its own
    # SettingsError; the detail is in the chained cause.
    messages = []
    error: BaseException | None = excinfo.value
    while error is not None:
        messages.append(str(error))
        error = error.__cause__
    return " | ".join(messages)


def test_gap_in_indexes_is_an_error(monkeypatch):
    monkeypatch.setenv("NEW_USER_DEFAULT_LABELS__0", "A")
    monkeypatch.setenv("NEW_USER_DEFAULT_LABELS__2", "C")
    message = _config_error()
    assert "NEW_USER_DEFAULT_LABELS" in message
    assert "entry 2 is set but entry 1 is missing" in message


def test_gap_after_the_yaml_list_is_an_error(monkeypatch, clean_env):
    write_yaml(clean_env, YAML_ONE_PROVIDER)
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__2__CLIENT_ID", "third")
    message = _config_error()
    assert "AUTH_OIDC_PROVIDERS: entry 2 is set but entry 1 is missing" in message
    assert "has 1 entry" in message


def test_non_numeric_index_is_an_error(monkeypatch):
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__first__CLIENT_ID", "x")
    message = _config_error()
    assert "'first' is not a list index" in message


def test_duplicate_index_spelling_is_an_error(monkeypatch):
    monkeypatch.setenv("NEW_USER_DEFAULT_LABELS__1", "A")
    monkeypatch.setenv("NEW_USER_DEFAULT_LABELS__01", "B")
    monkeypatch.setenv("NEW_USER_DEFAULT_LABELS__0", "Z")
    message = _config_error()
    assert "index 1 is given more than once" in message


def test_null_plus_indexed_entries_is_an_error(monkeypatch):
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS", "null")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__CLIENT_ID", "x")
    message = _config_error()
    assert "set to null and also has indexed entries" in message


def test_invalid_json_inside_an_entry_is_an_error(monkeypatch):
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__SCOPES", "openid,email")
    message = _config_error()
    assert "AUTH_OIDC_PROVIDERS__0__SCOPES: expected a JSON list" in message
    # Never echo values: they may be secrets.
    assert "openid,email" not in message


def test_null_list_setting_still_works(monkeypatch):
    # Regression guard for tests_config_env_null.py's list case.
    monkeypatch.setenv("NEW_USER_DEFAULT_LABELS", "null")
    assert Config().NEW_USER_DEFAULT_LABELS is None


def _validation_error() -> str:
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as excinfo:
        Config()
    return str(excinfo.value)


@pytest.mark.parametrize("empty", ["", "   "])
def test_enabled_provider_with_empty_client_secret_is_refused(
    monkeypatch, clean_env, empty
):
    # Typical cause: `AUTH_OIDC_PROVIDERS__0__CLIENT_SECRET: ${UNSET}` in a
    # compose file, interpolated to an empty string.
    write_yaml(clean_env, YAML_ONE_PROVIDER)
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__CLIENT_SECRET", empty)
    message = _validation_error()
    assert "AUTH_OIDC_PROVIDERS entry 0 ('Company SSO')" in message
    assert "CLIENT_SECRET is empty" in message
    assert "AUTH_OIDC_PROVIDERS__0__CLIENT_SECRET" in message


def test_empty_secret_error_names_the_right_entry(monkeypatch, clean_env):
    write_yaml(clean_env, YAML_ONE_PROVIDER)
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__1__ENABLED", "true")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__1__PROVIDER_DISPLAY_NAME", "Second")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__1__CLIENT_ID", "second")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__1__CLIENT_SECRET", "")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__1__CONFIGURATION_ENDPOINT", DISCOVERY)
    message = _validation_error()
    assert "AUTH_OIDC_PROVIDERS entry 1 ('Second')" in message
    assert "AUTH_OIDC_PROVIDERS__1__CLIENT_SECRET" in message
    # The secret of the healthy entry never shows up in the message.
    assert "from-the-file" not in message


def test_disabled_provider_may_have_an_empty_client_secret(monkeypatch, clean_env):
    write_yaml(clean_env, YAML_ONE_PROVIDER)
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__ENABLED", "false")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__CLIENT_SECRET", "")
    (provider,) = Config().AUTH_OIDC_PROVIDERS
    assert provider.ENABLED is False


def test_null_inside_an_entry_sets_that_field_to_null(monkeypatch, clean_env):
    write_yaml(clean_env, YAML_ONE_PROVIDER + "    AUTO_LOGIN: true\n")
    monkeypatch.setenv("AUTH_OIDC_PROVIDERS__0__AUTO_LOGIN", "null")
    (provider,) = Config().AUTH_OIDC_PROVIDERS
    assert provider.AUTO_LOGIN is None
