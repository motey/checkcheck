from typing import Dict, List, Literal, TYPE_CHECKING
import os
import requests
import json
import csv
import pydantic
import sqlmodel
import json
import inspect
from pathlib import Path
import csv
import io
import pathlib
from checkcheckserver.config import Config

import importlib.util
import os
import types
import random
import datetime
from dataclasses import dataclass

MEDLOG_ACCESS_TOKEN_ENV_NAME = "MEDLOG_ACCESS_TOKEN"


def get_access_token() -> str | None:
    return os.environ.get(MEDLOG_ACCESS_TOKEN_ENV_NAME, None)


print("SQL_DATABASE_URL", os.environ.get("SQL_DATABASE_URL"))
server_config = Config()


def get_server_base_url():

    return f"http://{server_config.SERVER_LISTENING_HOST}:{server_config.SERVER_LISTENING_PORT}"


def authorize(user, pw):
    response = req(
        "/api/auth/basic/login/token", "post", f={"username": user, "password": pw}
    )
    """response example:
    {
    "access_token": "string",
    "token_type": "Bearer"
    }
    """
    os.environ[MEDLOG_ACCESS_TOKEN_ENV_NAME] = response["access_token"]


def req(
    endpoint: str,
    method: Literal["get", "post", "put", "patch", "delete"] = "get",
    q: Dict = None,  # query params as dict
    b: Dict = None,  # json body as dict
    f: Dict = None,  # form data as dict
    expected_http_code: int = None,
    suppress_auth: bool = False,
    tolerated_error_codes: List[int] = None,
    tolerated_error_body: List[Dict | str] = None,
) -> Dict | str:
    if tolerated_error_codes is None:
        tolerated_error_codes = []
    if tolerated_error_body is None:
        tolerated_error_body = []
    http_method_func = getattr(requests, method)
    http_method_func_params = {}
    http_method_func_headers = {}
    if q:
        # query params
        http_method_func_params["params"] = q
    if b:
        # body
        http_method_func_params["json"] = b
    if f:
        # formdata
        http_method_func_headers["Content-Type"] = "application/x-www-form-urlencoded"
        http_method_func_params["data"] = f
    # url
    if endpoint and not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    url = f"{get_server_base_url()}{endpoint}"
    http_method_func_params["url"] = url

    # auth
    access_token = get_access_token()
    http_method_func_headers_print = None
    if access_token and not suppress_auth:
        http_method_func_headers_print = http_method_func_headers.copy()
        http_method_func_headers["Authorization"] = f"Bearer {access_token}"
        http_method_func_headers_print["Authorization"] = (
            f"Bearer {access_token[:16]}...(truncated)"
        )

    # create log message that documents the whole request
    log_msg_request = f"TEST-REQUEST:{method} - {endpoint} - PARAMS: {({k:v for k,v in http_method_func_params.items() if k != 'url'})} - HEADERS: {http_method_func_headers_print}"

    # attach headers to request params
    if http_method_func_headers:
        http_method_func_params["headers"] = http_method_func_headers
        http_method_func_headers_print = http_method_func_headers.copy()

    print(log_msg_request)

    # fire request
    r = http_method_func(**http_method_func_params)
    if expected_http_code:
        assert (
            r.status_code == expected_http_code
        ), f"Exptected http status {expected_http_code} got {r.status_code} for {log_msg_request}"
    else:
        try:
            r.raise_for_status()
        except requests.HTTPError as err:
            if not r.status_code in tolerated_error_codes:
                try:
                    body = r.json()
                except requests.exceptions.JSONDecodeError:
                    body = r.content
                if not body in tolerated_error_body:
                    if body:
                        print("Error body: ", body)
                    raise err
    try:
        return r.json()
    except requests.exceptions.JSONDecodeError:
        return r.content


def get_dot_env_file_variable(filepath: str, key: str) -> str | None:
    """
    Extracts the value of a specific environment variable from a .env file.

    Args:
        filepath (str): The path to the .env file.
        key (str): The environment variable key to look for.

    Returns:
        str | None: The value of the environment variable, or None if it's not found or empty.
    """
    if not os.path.exists(filepath):
        return None

    with open(filepath) as file:
        for line in file:
            line = line.strip()
            if line and not line.startswith("#"):
                var_key, var_value = map(str.strip, line.split("=", 1))
                if var_key == key:
                    return var_value or None
    return None


def dict_must_contain(
    d: Dict,
    required_keys_and_val: Dict = None,
    required_keys: List = None,
    raise_if_not_fullfilled: bool = True,
    exception_dict_identifier: str = None,
) -> bool:
    if required_keys_and_val is None:
        required_keys_and_val = {}
    if required_keys is None:
        required_keys = []
    if not isinstance(required_keys, list):
        raise TypeError(
            f"Expected List of string for `required_keys`. got {type(required_keys)}"
        )
    for k, v in required_keys_and_val.items():
        try:
            if d[k] != v:
                if raise_if_not_fullfilled:
                    raise ValueError(
                        f"""Expected value following val in key '{k}' {"in dict "+exception_dict_identifier if exception_dict_identifier else ""}'\n'{v}'\n got \n'{d[k]}'"""
                    )
                return False
        except KeyError:
            if raise_if_not_fullfilled:
                raise KeyError(
                    f"""Expected value key '{k}' {"in dict "+exception_dict_identifier if exception_dict_identifier else ""}'"""
                )
            return False
    for k in required_keys:
        if k not in d:
            if raise_if_not_fullfilled:
                raise KeyError(
                    f"""Missing expected value key '{k}' {"in dict "+exception_dict_identifier if exception_dict_identifier else ""}'"""
                )
            return False
    return True


def find_first_dict_in_list(
    l: List[Dict],
    required_keys_and_val: Dict = None,
    required_keys: List = None,
    raise_if_not_found: bool = True,
    exception_dict_identifier: str = None,
) -> Dict:
    for obj in l:
        if dict_must_contain(
            obj,
            required_keys_and_val=required_keys_and_val,
            required_keys=required_keys,
            raise_if_not_fullfilled=False,
        ):
            return obj
    if raise_if_not_found:
        raise ValueError(
            f"Obj with '{required_keys_and_val}' and/or keys {required_keys} not found in {l}"
        )
    return False


def list_contains_dict_that_must_contain(
    l: List[Dict],
    required_keys_and_val: Dict = None,
    required_keys: List = None,
    raise_if_not_fullfilled: bool = True,
    exception_dict_identifier: str = None,
) -> bool:
    if find_first_dict_in_list(
        l,
        required_keys_and_val=required_keys_and_val,
        required_keys=required_keys,
        raise_if_not_found=raise_if_not_fullfilled,
        exception_dict_identifier=exception_dict_identifier,
    ):
        return True
    return False


def dictyfy(val: str | sqlmodel.SQLModel | pydantic.BaseModel | dict) -> dict:
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        return json.loads(val)
    if isinstance(val, (sqlmodel.SQLModel, pydantic.BaseModel)):
        return json.loads(val.model_dump_json(exclude_unset=True))
    raise ValueError("Dont know how to inteprete value as dict")


def get_test_functions_from_file_or_module(
    file_or_module: str | Path | types.ModuleType,
):
    if isinstance(file_or_module, (str, Path)):

        if not os.path.isfile(file_or_module):
            raise FileNotFoundError(f"No such file: {file_or_module}")

        # Derive a module name from the file path
        module_name = os.path.splitext(os.path.basename(file_or_module))[0]

        # Load the module from the file
        spec = importlib.util.spec_from_file_location(module_name, file_or_module)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = file_or_module

    # Extract all functions starting with "test_"
    # print("vars(module).items()", vars(module).items())
    for name, func in vars(module).items():
        print(name, type(func), func)
    test_functions = [
        (name, func)
        for name, func in vars(module).items()
        if name.startswith("test_")
        or name.startswith("tests_")
        and isinstance(func, types.FunctionType)
    ]

    return test_functions


def random_value_from_csv_column(
    file_path, column_name, random_gen: random.Random = None, delimiter: str = ";"
):
    if random_gen is None:
        random_gen = random.Random()
    with open(file_path, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile, delimiter=delimiter)
        values = [
            row[column_name]
            for row in reader
            if column_name in row and row[column_name]
        ]

    if not values:
        raise ValueError(f"No data found in column '{column_name}'.")

    return random_gen.choice(values)


def random_past_date(
    min_date: datetime.date = None, random_gen: random.Random = None
) -> datetime.date:
    """
    Generate a random date between a minimum date (default: two years ago) and today.

    Args:
        min_date (date, optional): The earliest allowable date. Defaults to two years ago from today.

    Returns:
        date: A random date between min_date and today.
    """
    today = datetime.date.today()
    if random_gen is None:
        random_gen = random.Random()
    if min_date is None:
        min_date = today - datetime.timedelta(days=730)  # Approx. 2 years
    if min_date > today:
        raise ValueError("min_date cannot be in the future.")

    delta_days = (today - min_date).days
    random_days = random_gen.randint(0, delta_days)
    return min_date + datetime.timedelta(days=random_days)


def is_valid_csv_with_rows(
    csv_string: str, expected_row_count: int, has_header: bool = True
) -> bool:
    """
    Validates if a string is a valid CSV with the expected number of data rows.

    Args:
        csv_string (str): The CSV content as a string.
        expected_rows (int): The expected number of data rows (excluding header if has_header=True).
        has_header (bool): Whether the CSV includes a header row.

    Returns:
        bool: True if the string is a valid CSV with the expected number of rows, else False.
    """
    try:
        reader = csv.reader(io.StringIO(csv_string.strip()))
        rows = list(reader)

        if not rows:
            return expected_row_count == 0

        if has_header:
            data_rows = rows[1:]  # Exclude header
        else:
            data_rows = rows
        row_count = len(data_rows)
        if row_count != expected_row_count:
            print(f"CSV has {row_count} rows. Expected {expected_row_count}")
        return row_count == expected_row_count
    except Exception:
        print(f"Failed parsing CSV.")
        return False


def import_test_modules(from_dir: pathlib.Path) -> List[types.ModuleType]:
    """
    Imports all Python modules in the current directory whose names start with 'tests_'.

    Returns:
        A list of imported modules as instances of types.ModuleType.
    """
    modules: List[types.ModuleType] = []

    for py_file in from_dir.glob("tests_*.py"):
        module_name: str = py_file.stem
        module_spec: importlib.machinery.ModuleSpec | None = (
            importlib.util.spec_from_file_location(module_name, py_file)
        )

        if module_spec and module_spec.loader:
            module: types.ModuleType = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)
            modules.append(module)

    return modules
