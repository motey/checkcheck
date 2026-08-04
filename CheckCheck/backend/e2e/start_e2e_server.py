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
Default : e2e/e2e_test.sqlite – recreated fresh on every run.
Postgres: set SQL_DATABASE_URL=postgresql+asyncpg://... before calling this
          script (run_e2e_tests_postgres.sh does this automatically).
          The database must already exist and be accessible; migrations are
          run automatically by the backend on startup.
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
# Absolute path so it works regardless of the process working directory.
FRONTEND_DIR = Path(__file__).parent.parent.parent / "frontend" / ".output" / "public"

# If an external SQL_DATABASE_URL is provided (e.g. for Postgres), honour it
# and skip the SQLite file management entirely.
_EXTERNAL_DB_URL: str | None = os.environ.get("SQL_DATABASE_URL")


def _configure_env() -> None:
    if _EXTERNAL_DB_URL:
        # Keep the caller-supplied URL; do not overwrite with SQLite.
        os.environ["SQL_DATABASE_URL"] = _EXTERNAL_DB_URL
    else:
        os.environ["SQL_DATABASE_URL"] = f"sqlite+aiosqlite:///{E2E_DB}"
    os.environ["FRONTEND_FILES_DIR"] = str(FRONTEND_DIR)
    os.environ["ADMIN_USER_NAME"] = "admin3"
    os.environ["ADMIN_USER_PW"] = "password123"
    os.environ["ADMIN_USER_EMAIL"] = "admin@test.de"
    os.environ["AUTH_JWT_SECRET"] = "e2e-test-jwt-secret-checkcheck-placeholder-000000000000000000000000000000"
    os.environ["SERVER_SESSION_SECRET"] = "e2e-test-session-secret-checkcheck-placeholder-0000000000000000000000000"
    os.environ["AUTH_OIDC_TOKEN_STORAGE_SECRET"] = "e2e-test-oidc-storage-secret-checkcheck-placeholder-00000000000000000"
    os.environ["SERVER_BIND_PORT"] = str(PORT)
    # Public URL is plain HTTP on localhost, so the session cookie is derived
    # non-Secure automatically (no SET_SESSION_COOKIE_SECURE override needed).
    os.environ["SERVER_PUBLIC_URL"] = f"http://localhost:{PORT}"
    os.environ["AUTH_BASIC_LOGIN_IS_ENABLED"] = "true"
    os.environ["AUTH_BASIC_USER_DB_REGISTER_ENABLED"] = "false"
    os.environ["AUTH_ACCESS_TOKEN_EXPIRES_MINUTES"] = "1000"
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["APP_PROVISIONING_DATA_YAML_FILES"] = json.dumps([str(PROVISIONING)])
    # Notification settings (chunk E5) only show their email half on an instance
    # that can send mail, so this one can: the `null` transport accepts every
    # message and discards it, which exercises the whole queue-and-dispatch path
    # without a mail server and without anything leaving the process.
    os.environ.setdefault("EMAIL_ENABLED", "true")
    os.environ.setdefault("EMAIL_TRANSPORT", "null")
    os.environ.setdefault("EMAIL_FROM_ADDRESS", "checkcheck-e2e@test.de")
    # One notification type is administrator-disabled, so the dialog's locked
    # state has something real to render. Nothing in the suite depends on
    # `public_link_opened` notifications (opening a public link still works; only
    # the owner's notification about it is suppressed).
    os.environ.setdefault("NOTIFY_DISABLED_TYPES", json.dumps(["public_link_opened"]))
    # Chunk E6: mailing a public link is off in production by default, so the E2E
    # instance switches it on and declares one internal domain, which is what
    # gives the "that address looks like a colleague's" callout something real to
    # fire on. Both are setdefault, so a spec run can override either — an empty
    # domain list is how the "no callout ever appears" case would be driven, and
    # the pure-function version of it lives in tests/unit/publicLinkEmail.spec.ts.
    os.environ.setdefault("SHARING_PUBLIC_LINK_EMAIL_ENABLED", "true")
    os.environ.setdefault(
        "SHARING_INTERNAL_EMAIL_DOMAINS", json.dumps(["internal-e2e.example"])
    )
    # The webhook channel is on here too, so its column and its URL field are
    # real in the settings dialog. Nothing is ever actually POSTed by the suite:
    # a saved URL is only called once a notification of a type the user switched
    # the channel on for happens, and no spec does that. The "instance without
    # webhooks" case is covered by the unit test of `visibleChannels`.
    os.environ.setdefault("NOTIFY_WEBHOOK_ENABLED", "true")
    # The push channel (chunk P2), same reasoning: on here so its column, the
    # "Enable notifications" flow and the device list are real in the settings
    # dialog. This VAPID pair is a throwaway (valid in shape, tied to no real
    # push service) — the same one tests_notification_push.py uses. Nothing in
    # the E2E suite talks to a real push endpoint; a mocked PushManager stands
    # in for the browser API (see tests/e2e/notification-settings.spec.ts), and
    # the resulting fake `endpoint` fails harmlessly in the background dispatcher
    # if a spec ever exercises "send test push".
    os.environ.setdefault("NOTIFY_PUSH_ENABLED", "true")
    os.environ.setdefault(
        "VAPID_PUBLIC_KEY",
        "BH-DWhYfjSH5OVS2sjII4dGEP46ueAfPWQklJ_zITJqoWtfKgjHBTDxE_X5jdPms-zR3R9b43oCqYFnxwmwk_PY",
    )
    os.environ.setdefault("VAPID_PRIVATE_KEY", "Mp6hqDn1uEMxJwMvqchFdkrCiID8zYUIvTwDI-rmLSA")
    os.environ.setdefault("VAPID_CONTACT_EMAIL", "admin@test.de")
    # Invite-mode E2E pass: SHARING_REQUIRE_INVITE_ACCEPT is left untouched here so
    # the caller's environment wins. The default pass leaves it unset (→ False, the
    # production default: shares are accepted instantly). The invite-flow pass —
    # mirroring the backend's second pytest pass — runs:
    #   SHARING_REQUIRE_INVITE_ACCEPT=1 ./run_e2e_tests.sh invites
    # which boots this backend in invite mode (a share becomes a pending invite the
    # target must accept/decline). The `invites` filename filter limits the run to
    # tests/e2e/invites.spec.ts so the other specs don't run in the wrong mode.
    if os.environ.get("SHARING_REQUIRE_INVITE_ACCEPT"):
        print(
            f"ℹ  SHARING_REQUIRE_INVITE_ACCEPT={os.environ['SHARING_REQUIRE_INVITE_ACCEPT']} "
            "(invite-mode E2E pass)",
            file=sys.stderr,
            flush=True,
        )


# The push endpoints the E2E suite registers are fakes on `fake.push.example`,
# a name that deliberately resolves to nothing. Chunk N2 judges every endpoint
# before it becomes a row (resolve the host, then refuse anything that is not
# public unicast), so an unresolvable name is a 400 and the entire push flow in
# the settings dialog stopped being reachable from the browser suite.
#
# Pointing the suite at a real host instead would put a DNS lookup and,
# eventually, an outbound POST to somebody else's server in the middle of a
# test. So the E2E server process teaches the guard exactly one name family
# instead: `*.push.example` resolves to a fixed public address. Nothing else is
# touched, and no connection is ever made to that address (the guard only
# judges it; the dispatcher hands the URL to `pywebpush`, which resolves the
# real name itself and fails harmlessly). The guard's actual behaviour stays
# covered by the backend's own tests, which do not run through this file.
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

    # `require_public_https_target` reads these through the module globals, so
    # replacing the attributes is enough for both callers.
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
    raise TimeoutError(f"E2E backend not ready after {timeout} s")


if __name__ == "__main__":
    # Only wipe the SQLite file when we own the database.
    # For Postgres the caller (run_e2e_tests_postgres.sh) is responsible for
    # creating a clean database before calling this script.
    if not _EXTERNAL_DB_URL and E2E_DB.exists():
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
