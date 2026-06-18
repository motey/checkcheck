"""
Pytest harness for the CheckCheck backend integration tests.

The tests talk to a *real* running server over HTTP (see ``utils.req``), so this
conftest is responsible for the whole session lifecycle:

* set the env vars the server reads (test ``.env``, DB URL, admin user, provisioning)
* spin up the database — a fresh SQLite file (default) or a throwaway Docker
  Postgres container (``--db=postgres``)
* boot ``checkcheckserver/main.py`` as a subprocess and wait until ``/api/health``
  reports healthy
* log in as the admin user and set the token as the global default so plain
  ``req(...)`` calls are authenticated
* monitor the server and tear everything down at the end of the session

Run with:
    python -m pytest tests                 # SQLite (default)
    python -m pytest tests --db=postgres   # Docker Postgres

This mirrors the pytest setup used by the sibling DZDMedLog project.
"""

import os
import sys
import json
import time
import threading
import subprocess
import logging
from pathlib import Path

import pytest

logger = logging.getLogger("conftest")

# ── sys.path so checkcheckserver / utils / statics import during collection ────
TESTS_DIR = Path(__file__).parent
BACKEND_DIR = TESTS_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(TESTS_DIR))  # so test modules can import utils/statics

from statics import (
    DB_PATH,
    DOT_ENV_FILE_PATH,
    ADMIN_USER_EMAIL,
    ADMIN_USER_PW,
    ADMIN_USER_NAME,
    OIDC_TEST_PROVIDER_DISPLAY_NAME,
    OIDC_TEST_PROVIDER_SLUG,
    OIDC_TEST_ROLE_GROUP,
    OIDC_TEST_MAPPED_ROLE,
)

PROVISIONING_DATA_PATH = TESTS_DIR / "provisioning_data" / "test_users.yaml"
CHECKCHECK_MAIN = BACKEND_DIR / "checkcheckserver" / "main.py"


def set_config_for_test_env():
    """Inject the env vars the server needs — must run before any test module
    (and ``utils``, which instantiates ``Config()``) is imported."""
    os.environ["CHECKCHECK_DOT_ENV_FILE"] = DOT_ENV_FILE_PATH
    os.environ.setdefault("SQL_DATABASE_URL", f"sqlite+aiosqlite:///{DB_PATH}")
    os.environ["ADMIN_USER_NAME"] = ADMIN_USER_NAME
    os.environ["ADMIN_USER_PW"] = ADMIN_USER_PW
    os.environ["ADMIN_USER_EMAIL"] = ADMIN_USER_EMAIL
    os.environ["APP_PROVISIONING_DATA_YAML_FILES"] = json.dumps(
        [str(PROVISIONING_DATA_PATH)]
    )


# Set at module level so it is in place during pytest's collection phase.
set_config_for_test_env()

# ── Postgres docker constants ─────────────────────────────────────────────────
# Dedicated container name + port so this never collides with the dev server
# (5434) or the Playwright E2E suite (5435).
_PG_CONTAINER = "checkcheck-backend-tests-postgres"
_PG_USER = "checkcheck_test"
_PG_PW = "checkcheck_test"
_PG_PORT = 5436
_PG_DB = "checkcheck_test"
_PG_URL = f"postgresql+asyncpg://{_PG_USER}:{_PG_PW}@localhost:{_PG_PORT}/{_PG_DB}"
# ─────────────────────────────────────────────────────────────────────────────


def pytest_addoption(parser):
    parser.addoption(
        "--db",
        default="sqlite",
        choices=["sqlite", "postgres"],
        help="Database backend: 'sqlite' (default, file) or 'postgres' (Docker)",
    )


# ── OIDC mock provider ────────────────────────────────────────────────────────
# A throwaway OpenID Connect provider (oidc_provider_mock) is started in-process
# so the OIDC login flow can be exercised end-to-end without a real IdP. Its URL
# and provider config are injected into the env *before* the server subprocess
# starts, so the server registers the provider's OAuth client at boot.
# tests_oidc_mapping.py skips itself when OIDC_MOCK_SERVER_URL is unset.

_OIDC_TEST_USERS = [
    {
        "sub": "oidc-role-test-user",
        "userinfo": {
            "name": "oidc-role-test-user",
            "email": "oidc-role-test@test.com",
            "given_name": "OIDC Role Test",
            "groups": [OIDC_TEST_ROLE_GROUP],
        },
    },
    {
        "sub": "oidc-relogin-test-user",
        "userinfo": {
            "name": "oidc-relogin-test-user",
            "email": "oidc-relogin-test@test.com",
            "given_name": "OIDC Relogin Test",
            "groups": [OIDC_TEST_ROLE_GROUP],
        },
    },
]

_oidc_mock_ctx = None


def _start_oidc_mock():
    """Start the mock OIDC provider, register the test users, and export the
    provider config the server reads at startup. No-op if the optional
    oidc_provider_mock dependency is not installed."""
    global _oidc_mock_ctx
    try:
        import oidc_provider_mock
        import requests as _requests
    except ImportError:
        logger.warning("oidc_provider_mock not installed — skipping OIDC test setup")
        return

    ctx = oidc_provider_mock.run_server_in_thread(port=0)
    server = ctx.__enter__()
    _oidc_mock_ctx = ctx

    mock_url = f"http://localhost:{server.server_port}"
    os.environ["OIDC_MOCK_SERVER_URL"] = mock_url
    logger.info("OIDC mock server started at %s", mock_url)

    for user in _OIDC_TEST_USERS:
        res = _requests.put(f"{mock_url}/users/{user['sub']}", json=user)
        res.raise_for_status()

    provider_config = {
        "PROVIDER_DISPLAY_NAME": OIDC_TEST_PROVIDER_DISPLAY_NAME,
        "CONFIGURATION_ENDPOINT": f"{mock_url}/.well-known/openid-configuration",
        "CLIENT_ID": "test-client-id",
        "CLIENT_SECRET": "test-client-secret",
        "USER_NAME_ATTRIBUTE": "name",
        "USER_DISPLAY_NAME_ATTRIBUTE": "given_name",
        "USER_MAIL_ATTRIBUTE": "email",
        "USER_GROUPS_ATTRIBUTE": "groups",
        "ROLE_MAPPING": {OIDC_TEST_ROLE_GROUP: [OIDC_TEST_MAPPED_ROLE]},
    }
    os.environ["AUTH_OIDC_TOKEN_STORAGE_SECRET"] = "oidc-test-storage-secret-checkcheck"
    os.environ["AUTH_OIDC_PROVIDERS"] = json.dumps([provider_config])
    logger.info("OIDC provider configured with slug '%s'", OIDC_TEST_PROVIDER_SLUG)


def _stop_oidc_mock():
    global _oidc_mock_ctx
    if _oidc_mock_ctx is not None:
        _oidc_mock_ctx.__exit__(None, None, None)
        _oidc_mock_ctx = None


# ── Database lifecycle helpers ────────────────────────────────────────────────


def _docker(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True)


def _setup_postgres() -> str:
    if _docker("info").returncode != 0:
        pytest.exit(
            "--db=postgres requires Docker. Use --db=sqlite if Docker is unavailable.",
            returncode=3,
        )
    logger.info("Removing any leftover postgres container...")
    _docker("stop", _PG_CONTAINER)
    _docker("rm", _PG_CONTAINER)

    logger.info("Starting postgres container '%s'...", _PG_CONTAINER)
    result = _docker(
        "run", "-d",
        "--name", _PG_CONTAINER,
        "-e", f"POSTGRES_USER={_PG_USER}",
        "-e", f"POSTGRES_PASSWORD={_PG_PW}",
        "-e", f"POSTGRES_DB={_PG_DB}",
        "-p", f"{_PG_PORT}:5432",
        "postgres:16-alpine",
    )
    if result.returncode != 0:
        pytest.exit(
            f"docker run failed: {result.stderr.decode().strip()}", returncode=3
        )

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _docker(
            "exec", _PG_CONTAINER, "pg_isready", "-U", _PG_USER, "-d", _PG_DB
        ).returncode == 0:
            logger.info("Postgres is ready.")
            return _PG_URL
        time.sleep(0.5)

    pytest.exit("Postgres container did not become ready within 30s.", returncode=3)


def _teardown_postgres():
    logger.info("Stopping and removing postgres container...")
    _docker("stop", _PG_CONTAINER)
    _docker("rm", _PG_CONTAINER)


# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def database(request):
    db = request.config.getoption("--db")

    if db == "postgres":
        url = _setup_postgres()
    else:
        from utils import get_dot_env_file_variable

        reset = os.getenv(
            "CHECKCHECK_TESTS_RESET_DB",
            get_dot_env_file_variable(
                DOT_ENV_FILE_PATH, "CHECKCHECK_TESTS_RESET_DB", default="True"
            ),
        ).lower() in ("true", "1", "t", "y", "yes")
        if reset:
            Path(DB_PATH).unlink(missing_ok=True)
            logger.info("Deleted SQLite DB at %s", DB_PATH)
        url = f"sqlite+aiosqlite:///{DB_PATH}"
        logger.info("SQLite DB will persist at %s after the run.", DB_PATH)

    os.environ["SQL_DATABASE_URL"] = url
    logger.info("Database URL: %s", url.replace(_PG_PW, "***"))

    yield

    if db == "postgres":
        _teardown_postgres()


def _start_server() -> subprocess.Popen:
    """Boot the backend exactly as run_dev_backend_server_with_oidc.sh does:
    ``python ./checkcheckserver/main.py`` from the backend dir, so __main__ and
    the CWD are set up the way the provisioner expects."""
    proc = subprocess.Popen(
        [sys.executable, str(CHECKCHECK_MAIN)],
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(BACKEND_DIR),
    )

    def _stream():
        for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                logger.debug("[server] %s", line)

    threading.Thread(target=_stream, daemon=True, name="server-logger").start()
    return proc


@pytest.fixture(scope="session", autouse=True)
def live_server(database):
    import requests
    from utils import get_server_base_url, authorize_for_access_token

    # Start the OIDC mock first so AUTH_OIDC_PROVIDERS is in the env the server
    # subprocess inherits.
    _start_oidc_mock()

    logger.info("Starting CheckCheck server subprocess...")
    server_proc = _start_server()

    base_url = get_server_base_url()
    timeout_sec = 60
    deadline = time.monotonic() + timeout_sec
    last_log = time.monotonic()

    logger.info("Waiting for server at %s ...", base_url)
    while True:
        now = time.monotonic()

        # Detect an early crash so we surface the traceback instead of timing out.
        if server_proc.poll() is not None:
            pytest.exit(
                f"Server process exited unexpectedly with code {server_proc.returncode} "
                f"(see [server] log lines above for the traceback).",
                returncode=3,
            )

        if now > deadline:
            server_proc.terminate()
            pytest.exit(
                f"Server did not become healthy within {timeout_sec}s — aborting. "
                f"Check [server] log lines above for startup errors.",
                returncode=3,
            )

        if now - last_log >= 10:
            logger.info("  still waiting for server... (%ds elapsed)",
                        int(now - (deadline - timeout_sec)))
            last_log = now

        try:
            r = requests.get(f"{base_url}/api/health")
            r.raise_for_status()
            if r.json().get("healthy"):
                break
        except (requests.HTTPError, requests.ConnectionError):
            pass
        time.sleep(1)

    logger.info("Server is up — logging in as admin '%s'.", ADMIN_USER_NAME)
    authorize_for_access_token(
        username=ADMIN_USER_NAME,
        pw=ADMIN_USER_PW,
        set_as_global_default_login=True,
    )

    logger.info("Server ready — starting test run.")

    stop_event = threading.Event()

    def _monitor():
        while not stop_event.is_set():
            if server_proc.poll() is not None:
                logger.error(
                    "Server process died unexpectedly (exit code %s)",
                    server_proc.returncode,
                )
                os._exit(1)
            time.sleep(1)

    monitor_thread = threading.Thread(target=_monitor, daemon=True)
    monitor_thread.start()

    yield

    stop_event.set()
    monitor_thread.join()
    server_proc.terminate()
    try:
        server_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server_proc.kill()
        server_proc.wait()
    _stop_oidc_mock()
    logger.info("Test session complete — server shut down.")
