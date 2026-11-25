"""The database URL must never reach the logs with its password.

``SQL_DATABASE_URL`` is a plain ``str`` (the engine and Alembic need it as one),
so the startup log lines and the debug config dump use the redacted form.
"""

import yaml

from checkcheckserver.config import Config

PASSWORD = "s3cret-db-password"
PG_URL = f"postgresql+asyncpg://checkcheck:{PASSWORD}@db:5432/checkcheck"


def test_redacted_url_hides_the_password():
    redacted = Config(SQL_DATABASE_URL=PG_URL).SQL_DATABASE_URL_REDACTED
    assert PASSWORD not in redacted
    assert redacted == "postgresql+asyncpg://checkcheck:***@db:5432/checkcheck"


def test_redacted_url_keeps_a_url_without_password():
    url = "sqlite+aiosqlite:///./local.sqlite"
    assert Config(SQL_DATABASE_URL=url).SQL_DATABASE_URL_REDACTED == url


def test_unparseable_url_is_not_echoed():
    url = f"not a url with {PASSWORD}"
    redacted = Config(SQL_DATABASE_URL=url).SQL_DATABASE_URL_REDACTED
    assert PASSWORD not in redacted


def test_debug_config_dump_hides_the_password():
    config = Config(SQL_DATABASE_URL=PG_URL)
    dumped = yaml.dump(config.dump_for_log(), sort_keys=False)
    assert PASSWORD not in dumped
    assert "checkcheck:***@db" in dumped
    # The real secrets stay masked by SecretStr as before.
    assert config.AUTH_JWT_SECRET.get_secret_value() not in dumped
