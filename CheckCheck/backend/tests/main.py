import multiprocessing
import requests
import time
import urllib3
import os
import traceback
import types
from pathlib import Path
import sys, os

if __name__ == "__main__":

    MODULE_DIR = Path(__file__).parent
    MODULE_PARENT_DIR = MODULE_DIR.parent.absolute()
    sys.path.insert(0, os.path.normpath(MODULE_PARENT_DIR))

from statics import (
    DB_PATH,
    DOT_ENV_FILE_PATH,
    ADMIN_USER_EMAIL,
    ADMIN_USER_PW,
    ADMIN_USER_NAME,
)


def set_config_for_test_env():
    os.environ["MEDLOG_DOT_ENV_FILE"] = DOT_ENV_FILE_PATH
    print(f"set SQL_DATABASE_URL to {DB_PATH}")
    os.environ["SQL_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_PATH}"
    os.environ["ADMIN_USER_NAME"] = ADMIN_USER_NAME
    os.environ["ADMIN_USER_PW"] = ADMIN_USER_PW
    os.environ["ADMIN_USER_EMAIL"] = ADMIN_USER_EMAIL


set_config_for_test_env()


from utils import (
    get_server_base_url,
    get_dot_env_file_variable,
    authorize,
    get_test_functions_from_file_or_module,
    import_test_modules,
)


RESET_DB = os.getenv(
    "RESET_TEST_DB_ON_TESTSTART",
    get_dot_env_file_variable(DOT_ENV_FILE_PATH, "RESET_TEST_DB_ON_TESTSTART"),
).lower() in (
    "true",
    "1",
    "t",
    "y",
    "yes",
)

if RESET_DB:
    print(
        f"!!RESET DB AT {DB_PATH}. If you want to have a persisting test db, change the value for env var `RESET_TEST_DB_ON_TESTSTART` to false or remove it."
    )
    Path(DB_PATH).unlink(missing_ok=True)


from checkcheckserver.main import start as checkcheckserver_start


server_process = multiprocessing.Process(
    target=checkcheckserver_start,
    name="DZDserver",
    kwargs={},
)


server_base_url = get_server_base_url()


def wait_for_server_up_and_healthy(timeout_sec=120):
    server_not_available = True
    while server_not_available:
        try:
            r = requests.get(f"{server_base_url}/api/health")
            r.raise_for_status()

            server_not_available = False
        except (
            requests.HTTPError,
            requests.ConnectionError,
            urllib3.exceptions.MaxRetryError,
        ):
            time.sleep(1)
    print(f"SERVER UP FOR TESTING: {r.status_code}")


def shutdown_server_and_backgroundworker():
    print("SHUTDOWN SERVER!")
    server_process.terminate()
    time.sleep(5)
    print("KILL SERVER")

    # YOU ARE HERE! THIS DOES NOT KILL THE BACKGORUND WORKER PROCESS
    server_process.kill()
    server_process.join()
    server_process.close()


def start_server():
    set_config_for_test_env()
    print("START server")
    server_process.start()
    wait_for_server_up_and_healthy()
    print("STARTED server!")


start_server()


def run_single_test_file(
    file_name_or_module: str | types.ModuleType,
    authorize_before: bool = False,
    exit_on_success: bool = False,
    exit_on_fail: bool = True,
):
    all_function_success = True
    print("file_name_or_module", file_name_or_module)
    try:
        if authorize_before:
            authorize(user=ADMIN_USER_NAME, pw=ADMIN_USER_PW)
        for name, test_function in get_test_functions_from_file_or_module(
            file_name_or_module
        ):
            print(f"--------------- RUN test function {name}")
            test_function()
    except Exception as e:
        all_function_success = False
        print("Error in tests")
        print(print(traceback.format_exc()))
        shutdown_server_and_backgroundworker()
        print(f"🚫 TESTS {test_function.__name__} FAILED")
        if exit_on_fail:
            exit(1)
    if exit_on_success:
        shutdown_server_and_backgroundworker()
        print("✅️ TESTS SUCCEDED")
        exit(0)


if __name__ == "__main__":
    # RUN TESTS

    authorize(user=ADMIN_USER_NAME, pw=ADMIN_USER_PW)
    for test_module in import_test_modules(Path(__file__).parent):
        run_single_test_file(test_module)

    # last_interview_intakes()
    # test_do_health()
    # run_all_tests_users()
    # test_do_drugv2()
    # test_do_export()

    shutdown_server_and_backgroundworker()
    print("✅️ TESTS SUCCEDED")
    exit(0)
