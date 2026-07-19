"""
Starts the CheckCheck backend in *screenshot* mode, or seeds its database.

This is the docs-screenshot sibling of e2e/start_e2e_server.py. It exists as a
separate script (rather than a flag on the E2E one) so the two harnesses can
never collide: different port, different Postgres container, different
lifecycle. gen_screenshots.sh owns both.

Ports
-----
dev server  : 8181
unit tests  : 8888
e2e tests   : 8182
screenshots : 8183  <- this script

Modes
-----
--seed-only   Configure the environment, run the deterministic dev seeder, exit.
              The seeder performs the same schema/migration bootstrap the server
              does, so this is safe to run against an empty database and there
              is no race against a live server.
(default)     Boot the server, print "READY" once /api/health answers, then block
              until killed.

Database
--------
SQL_DATABASE_URL is REQUIRED and must point at a throwaway Postgres. Unlike the
E2E script there is no SQLite fallback: screenshots are generated against the
production database engine so nothing engine-specific leaks into the docs.

Version stamp
-------------
The sidebar renders the running *server* version. Left unpinned it would emit a
setuptools-scm dev string (v0.2.1.dev5+g251ad1d97...) into every screenshot,
producing a docs diff on every regeneration and leaking dev version noise into
public documentation. gen_screenshots.sh pins SETUPTOOLS_SCM_PRETEND_VERSION
before calling us; we only assert it is set.
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

PORT = 8183
# Reuse the E2E user fixtures so the login flow and the admin account match what
# the Playwright auth setup expects.
PROVISIONING = _BACKEND_DIR / "e2e" / "provisioning_data" / "test_users.yaml"
FRONTEND_DIR = _BACKEND_DIR.parent / "frontend" / ".output" / "public"

ADMIN_USER = "admin3"
ADMIN_PW = "password123"

# Fixed seed + profile. Changing either changes every screenshot, so they live
# here rather than in the shell script where they could drift per invocation.
SEED = 1337
SEED_PROFILE = "large"


def _configure_env() -> None:
    db_url = os.environ.get("SQL_DATABASE_URL")
    if not db_url:
        raise SystemExit(
            "SQL_DATABASE_URL is required (screenshots run against Postgres). "
            "Use gen_screenshots.sh, which starts a throwaway container."
        )
    os.environ["SQL_DATABASE_URL"] = db_url
    os.environ["FRONTEND_FILES_DIR"] = str(FRONTEND_DIR)
    os.environ["ADMIN_USER_NAME"] = ADMIN_USER
    os.environ["ADMIN_USER_PW"] = ADMIN_PW
    os.environ["ADMIN_USER_EMAIL"] = "admin@test.de"
    os.environ["AUTH_JWT_SECRET"] = "screenshot-jwt-secret-checkcheck-placeholder-00000000000000000000000000"
    os.environ["SERVER_SESSION_SECRET"] = "screenshot-session-secret-checkcheck-placeholder-000000000000000000000"
    os.environ["AUTH_OIDC_TOKEN_STORAGE_SECRET"] = "screenshot-oidc-storage-secret-checkcheck-placeholder-0000000000000"
    os.environ["SERVER_BIND_PORT"] = str(PORT)
    os.environ["SERVER_PUBLIC_URL"] = f"http://localhost:{PORT}"
    os.environ["AUTH_BASIC_LOGIN_IS_ENABLED"] = "true"
    os.environ["AUTH_BASIC_USER_DB_REGISTER_ENABLED"] = "false"
    os.environ["AUTH_ACCESS_TOKEN_EXPIRES_MINUTES"] = "1000"
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["APP_PROVISIONING_DATA_YAML_FILES"] = json.dumps([str(PROVISIONING)])

    if not os.environ.get("SETUPTOOLS_SCM_PRETEND_VERSION"):
        raise SystemExit(
            "SETUPTOOLS_SCM_PRETEND_VERSION is required so the sidebar version "
            "stamp is stable across regenerations. Use gen_screenshots.sh."
        )


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
    raise TimeoutError(f"Screenshot backend not ready after {timeout} s")


def _seed() -> None:
    """Run the deterministic dev seeder against the configured database."""
    from checkcheckserver.dev.seed_dev_data import main as seed_main

    argv = [
        "--seed", str(SEED),
        "--profile", SEED_PROFILE,
        "--admin-user", ADMIN_USER,
        # Always regenerate: the container is throwaway, but --wipe also makes a
        # re-run against a warm container produce the identical board.
        "--wipe",
    ]
    print(f"[screenshots] seeding (seed={SEED}, profile={SEED_PROFILE}) ...", flush=True)
    seed_main(argv)
    print("[screenshots] seed complete", flush=True)


def main() -> None:
    seed_only = "--seed-only" in sys.argv

    _configure_env()

    if seed_only:
        _seed()
        return

    multiprocessing.set_start_method("fork", force=True)
    proc = multiprocessing.Process(target=_server_target, name="CheckCheckScreenshotServer")
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


if __name__ == "__main__":
    main()
