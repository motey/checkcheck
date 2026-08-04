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
    # Notification channels, all on, mirroring e2e/start_e2e_server.py. Without
    # them the settings dialog photographs as a single "In the app" column above
    # the line "This server does not send email", which is the opposite of what
    # the picture in docs/screenshots.md is for. Nothing leaves the process: the
    # `null` mail transport discards every message, no webhook URL is ever
    # saved, and the VAPID pair is the same throwaway the E2E harness uses (valid
    # in shape, tied to no real push service).
    os.environ.setdefault("EMAIL_ENABLED", "true")
    os.environ.setdefault("EMAIL_TRANSPORT", "null")
    os.environ.setdefault("EMAIL_FROM_ADDRESS", "checkcheck@test.de")
    os.environ.setdefault("NOTIFY_WEBHOOK_ENABLED", "true")
    os.environ.setdefault("NOTIFY_PUSH_ENABLED", "true")
    os.environ.setdefault(
        "VAPID_PUBLIC_KEY",
        "BH-DWhYfjSH5OVS2sjII4dGEP46ueAfPWQklJ_zITJqoWtfKgjHBTDxE_X5jdPms-zR3R9b43oCqYFnxwmwk_PY",
    )
    os.environ.setdefault("VAPID_PRIVATE_KEY", "Mp6hqDn1uEMxJwMvqchFdkrCiID8zYUIvTwDI-rmLSA")
    os.environ.setdefault("VAPID_CONTACT_EMAIL", "admin@test.de")
    # Mailing a public link is off in production by default, so the "send this
    # link by email" field would not exist to photograph. One declared internal
    # domain comes with it, the way the E2E harness does it.
    os.environ.setdefault("SHARING_PUBLIC_LINK_EMAIL_ENABLED", "true")
    os.environ.setdefault(
        "SHARING_INTERNAL_EMAIL_DOMAINS", json.dumps(["internal.example"])
    )
    # NOTIFY_DISABLED_TYPES is deliberately NOT set here, unlike in the E2E
    # harness: a locked row is a state an administrator produces, and the docs
    # walkthrough should show the dialog a normal instance has.

    if not os.environ.get("SETUPTOOLS_SCM_PRETEND_VERSION"):
        raise SystemExit(
            "SETUPTOOLS_SCM_PRETEND_VERSION is required so the sidebar version "
            "stamp is stable across regenerations. Use gen_screenshots.sh."
        )


# The shot of the notification settings needs a device in the push list, and the
# browser side of that is a fake PushManager (tests/screenshots/desktop-settings.spec.ts).
# Its endpoint has to survive the SSRF guard on POST /user/me/push-subscriptions,
# which refuses a host that does not resolve, so the same one-name resolver shim
# the E2E harness installs is installed here. Nothing is ever POSTed to it: no
# notification is generated while the shots are taken.
#
# Kept in step with e2e/start_e2e_server.py by hand, deliberately: the two
# harnesses share no code so neither can break the other, and a drift here fails
# loudly (the device never appears and the shot's assertion fails).
_FAKE_PUSH_SUFFIX = ".push.example"
_FAKE_PUSH_ADDRESS = "93.184.216.34"


def _install_fake_push_resolver() -> None:
    from checkcheckserver.notify import net_guard

    real_async = net_guard.resolve_addresses
    real_sync = net_guard.resolve_addresses_sync

    def _is_fake(host: str) -> bool:
        return host.endswith(_FAKE_PUSH_SUFFIX)

    async def resolve_addresses(host: str, port: int):
        if _is_fake(host):
            return [_FAKE_PUSH_ADDRESS]
        return await real_async(host, port)

    def resolve_addresses_sync(host: str, port: int):
        if _is_fake(host):
            return [_FAKE_PUSH_ADDRESS]
        return real_sync(host, port)

    net_guard.resolve_addresses = resolve_addresses
    net_guard.resolve_addresses_sync = resolve_addresses_sync


def _server_target() -> None:
    _install_fake_push_resolver()
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
