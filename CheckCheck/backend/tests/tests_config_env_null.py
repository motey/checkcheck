"""Env vars can set nullable settings to null (``env_parse_none_str="null"``).

Without it pydantic-settings has no way to: an int setting fails validation
(the server does not start), a str setting gets the text "null", and a list
setting silently keeps its default. docs/CONFIG_REFERENCE.md tells admins to
set these settings to null, so the env var route must work too.
"""

from checkcheckserver.config import Config


def test_env_null_sets_int_setting_to_null(monkeypatch):
    monkeypatch.setenv("API_TOKEN_MANAGEMENT_OIDC_LOGIN_MAX_AGE_DAYS", "null")
    monkeypatch.setenv("API_TOKEN_MANAGEMENT_MAX_TOKENS_PER_USER", "null")
    config = Config()
    assert config.API_TOKEN_MANAGEMENT_OIDC_LOGIN_MAX_AGE_DAYS is None
    assert config.API_TOKEN_MANAGEMENT_MAX_TOKENS_PER_USER is None


def test_env_null_sets_list_setting_to_null(monkeypatch):
    monkeypatch.setenv("NEW_USER_DEFAULT_LABELS", "null")
    assert Config().NEW_USER_DEFAULT_LABELS is None


def test_env_number_still_parses(monkeypatch):
    monkeypatch.setenv("API_TOKEN_MANAGEMENT_OIDC_LOGIN_MAX_AGE_DAYS", "90")
    assert Config().API_TOKEN_MANAGEMENT_OIDC_LOGIN_MAX_AGE_DAYS == 90


def test_email_transport_null_stays_the_null_transport(monkeypatch):
    # `null` is a transport name, not "unset": it must survive env_parse_none_str.
    monkeypatch.setenv("EMAIL_TRANSPORT", "null")
    assert Config().EMAIL_TRANSPORT == "null"
    assert Config(EMAIL_TRANSPORT=None).EMAIL_TRANSPORT == "null"
