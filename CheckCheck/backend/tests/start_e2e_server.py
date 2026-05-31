"""
Starts the CheckCheck backend in E2E-test mode.

Writes "READY" to stdout when the server accepts requests, then blocks
until killed (SIGTERM/SIGINT from the Playwright global-setup process).

Ports
-----
dev server  : 8181
unit tests  : 8888
e2e tests   : 8182  ← this script

Users (provisioned at startup)
-------------------------------
admin3 / password123
testuser01 / testuserpw_secure1  (via provisioning_data/test_users.yaml)

DB
--
tests/e2e_test.sqlite – recreated fresh on every run.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

import requests  # noqa: E402  (must follow sys.path fixup)

E2E_DB = Path(__file__).parent / "e2e_test.sqlite"
PROVISIONING = Path(__file__).parent / "provisioning_data" / "test_users.yaml"
PORT = 8182


def _configure_env() -> None:
    os.environ["SQL_DATABASE_URL"] = f"sqlite+aiosqlite:///{E2E_DB}"
    os.environ["ADMIN_USER_NAME"] = "admin3"
    os.environ["ADMIN_USER_PW"] = "password123"
    os.environ["ADMIN_USER_EMAIL"] = "admin@test.de"
    os.environ["AUTH_JWT_SECRET"] = "e2e-test-jwt-secret-checkcheck-placeholder-000000000000000000000000000000"
    os.environ["SERVER_SESSION_SECRET"] = "e2e-test-session-secret-checkcheck-placeholder-0000000000000000000000000"
    os.environ["AUTH_OIDC_TOKEN_STORAGE_SECRET"] = "e2e-test-oidc-storage-secret-checkcheck-placeholder-00000000000000000"
    os.environ["SERVER_LISTENING_PORT"] = str(PORT)
    os.environ["SERVER_HOSTNAME"] = "localhost"
    os.environ["SET_SESSION_COOKIE_SECURE"] = "false"
    os.environ["AUTH_BASIC_LOGIN_IS_ENABLED"] = "true"
    os.environ["AUTH_BASIC_USER_DB_REGISTER_ENABLED"] = "false"
    os.environ["AUTH_ACCESS_TOKEN_EXPIRES_MINUTES"] = "1000"
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["APP_PROVISIONING_DATA_YAML_FILES"] = json.dumps([str(PROVISIONING)])


def _server_target() -> None:
    from checkcheckserver.main import start

    start()


def _wait_for_ready(timeout: int = 60) -> None:
    url = f"http://localhost:{PORT}/api/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = requests.get(url, timeout=2)
            if r.status_code == 200 and r.json().get("healthy"):
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"E2E backend not ready after {timeout} s")


if __name__ == "__main__":
    if E2E_DB.exists():
        E2E_DB.unlink()

    _configure_env()
    multiprocessing.set_start_method("fork", force=True)

    proc = multiprocessing.Process(target=_server_target, name="CheckCheckE2EServer")
    proc.start()

    try:
        _wait_for_ready()
    except Exception as exc:
        proc.terminate()
        proc.join(5)
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)

    print("READY", flush=True)

    try:
        proc.join()
    except KeyboardInterrupt:
        pass
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(5)
        if proc.is_alive():
            proc.kill()
