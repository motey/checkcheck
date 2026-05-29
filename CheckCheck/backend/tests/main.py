"""
CheckCheck integration-test runner.

Usage:
    cd CheckCheck/backend
    python tests/main.py              # run all tests_*.py files
    python tests/tests_auth.py        # run a single file (via _single_test_file_runner)

Architecture (mirrors DZDMedLog pattern):
- Sets env vars so the server reads the test .env file and test SQLite DB.
- Forks a server subprocess (inherits env vars) and polls /api/health until ready.
- A daemon thread monitors the subprocess; if it dies, the runner exits with code 1.
- After all tests pass, terminates the server and exits with code 0.
"""

from typing import List
import multiprocessing
import requests
import time
import urllib3
import os
import traceback
import types
from pathlib import Path
import sys
import threading
import json

if __name__ == "__main__":
    MODULE_DIR = Path(__file__).parent
    MODULE_PARENT_DIR = MODULE_DIR.parent.absolute()
    sys.path.insert(0, os.path.normpath(MODULE_PARENT_DIR))
    sys.path.insert(0, str(MODULE_DIR))  # so test files can import each other

from statics import (
    DB_PATH,
    DOT_ENV_FILE_PATH,
    ADMIN_USER_EMAIL,
    ADMIN_USER_PW,
    ADMIN_USER_NAME,
)

PROVISIONING_DATA_PATH = Path(__file__).parent / "provisioning_data" / "test_users.yaml"


def set_config_for_test_env():
    """Inject all env vars the CheckCheck server needs for tests before the subprocess starts."""
    os.environ["CHECKCHECK_DOT_ENV_FILE"] = DOT_ENV_FILE_PATH
    os.environ["SQL_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_PATH}"
    os.environ["ADMIN_USER_NAME"] = ADMIN_USER_NAME
    os.environ["ADMIN_USER_PW"] = ADMIN_USER_PW
    os.environ["ADMIN_USER_EMAIL"] = ADMIN_USER_EMAIL
    os.environ["APP_PROVISIONING_DATA_YAML_FILES"] = json.dumps(
        [str(PROVISIONING_DATA_PATH)]
    )
    print(f"set SQL_DATABASE_URL to {os.environ['SQL_DATABASE_URL']}")


set_config_for_test_env()

# fork inherits the parent process (including __main__.__file__), which is
# required for the provisioner.  Must be called before any Process is created.
multiprocessing.set_start_method("fork", force=True)

from utils import (
    get_server_base_url,
    get_dot_env_file_variable,
    authorize_for_access_token,
    get_test_functions_from_file_or_module,
    import_test_modules,
)

RESET_DB = get_dot_env_file_variable(
    DOT_ENV_FILE_PATH, "CHECKCHECK_TESTS_RESET_DB", default="True"
).lower() in ("true", "1", "t", "y", "yes")

if RESET_DB:
    print(
        f"!! RESET DB at {DB_PATH}. "
        "Set CHECKCHECK_TESTS_RESET_DB=false to keep a persistent test DB."
    )
    Path(DB_PATH).unlink(missing_ok=True)

from checkcheckserver.main import start as checkcheckserver_start

server_process = multiprocessing.Process(
    target=checkcheckserver_start,
    name="CheckCheckServer",
    kwargs={},
)

server_base_url = get_server_base_url()


def wait_for_server_up(timeout_sec: int = 60):
    deadline = time.monotonic() + timeout_sec
    while True:
        if time.monotonic() > deadline:
            shutdown_server()
            raise TimeoutError(f"Server did not come up within {timeout_sec}s")
        try:
            r = requests.get(f"{server_base_url}/api/health")
            r.raise_for_status()
            data = r.json()
            if data.get("healthy"):
                print(f"SERVER READY FOR TESTING: {data}")
                return
        except Exception:
            pass
        time.sleep(1)


def shutdown_server():
    print("SHUTDOWN server …")
    server_process.terminate()
    server_process.join(timeout=5)
    if server_process.is_alive():
        server_process.kill()
        server_process.join()
    print("Server stopped.")


def start_server():
    set_config_for_test_env()
    print("START server")
    server_process.start()
    wait_for_server_up()
    print("STARTED server!")


def monitor_server(stop_event: threading.Event):
    try:
        while not stop_event.is_set():
            if not server_process.is_alive():
                print("❌ server_process died unexpectedly")
                shutdown_server()
                os._exit(1)
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown_server()
        stop_event.set()
        sys.exit(0)


start_server()

monitor_stop_event = threading.Event()
monitor_thread = threading.Thread(
    target=monitor_server,
    args=(monitor_stop_event,),
    daemon=True,
)
monitor_thread.start()

successful_test_files: List[str] = []


def run_single_test_file(
    file_name_or_module: str | types.ModuleType,
    authorize_before: bool = False,
    exit_on_success: bool = False,
    exit_on_fail: bool = True,
):
    all_functions_succeeded = True
    module_id = (
        str(file_name_or_module.__file__)
        if isinstance(file_name_or_module, types.ModuleType)
        else str(file_name_or_module)
    )
    test_function = None
    print(f"\n{'='*60}\nRUN MODULE: {module_id}\n{'='*60}")
    try:
        if authorize_before:
            authorize_for_access_token(
                username=ADMIN_USER_NAME,
                pw=ADMIN_USER_PW,
                set_as_global_default_login=True,
            )
        for name, test_function in get_test_functions_from_file_or_module(
            file_name_or_module
        ):
            print(f"  --- RUN {name}")
            test_function()
            print(f"  ✅ {name}")
    except Exception:
        all_functions_succeeded = False
        print(traceback.format_exc())
        fn_name = test_function.__name__ if test_function else "?"
        print(f"🚫 MODULE '{module_id}' FAILED at '{fn_name}'")
        if exit_on_fail:
            monitor_stop_event.set()
            shutdown_server()
            sys.exit(1)
    successful_test_files.append(module_id)
    if exit_on_success:
        monitor_stop_event.set()
        shutdown_server()
        print("✅️ TESTS SUCCEEDED")
        sys.exit(0)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))

    authorize_for_access_token(
        username=ADMIN_USER_NAME,
        pw=ADMIN_USER_PW,
        set_as_global_default_login=True,
    )

    for test_module in import_test_modules(Path(__file__).parent):
        run_single_test_file(test_module)

    monitor_stop_event.set()
    monitor_thread.join()
    shutdown_server()

    print("\n✅️ ALL TESTS SUCCEEDED")
    for f in successful_test_files:
        print(f"  ✅ {f}")
    sys.exit(0)
