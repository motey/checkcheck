from typing import Dict, List, Literal, Any, Optional, TYPE_CHECKING
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
import importlib.util
import types
import random
import datetime
from dataclasses import dataclass

from checkcheckserver.config import Config

CHECKCHECK_ACCESS_TOKEN_ENV_NAME = "MEDLOG_ACCESS_TOKEN"  # keep legacy name for compat


def get_access_token() -> str | None:
    return os.environ.get(CHECKCHECK_ACCESS_TOKEN_ENV_NAME, None)


print("SQL_DATABASE_URL", os.environ.get("SQL_DATABASE_URL"))
server_config = Config()


def get_server_base_url() -> str:
    return f"http://{server_config.SERVER_LISTENING_HOST}:{server_config.SERVER_LISTENING_PORT}"


# ── Authentication helpers ────────────────────────────────────────────────────


def authorize_for_access_token(
    username: str,
    pw: str,
    set_as_global_default_login: bool = False,
) -> str:
    """Log in via the token endpoint and return the Bearer token string.

    Pass *set_as_global_default_login=True* to store it as the default token
    so subsequent bare ``req()`` calls are authenticated automatically.
    """
    response = req(
        "api/auth/basic/login/token",
        "post",
        f={"username": username, "password": pw},
    )
    token = response["access_token"]
    if set_as_global_default_login:
        os.environ[CHECKCHECK_ACCESS_TOKEN_ENV_NAME] = token
    return token


def authorize_for_session(username: str, pw: str) -> requests.Session:
    """Log in via the session endpoint and return a *requests.Session* that
    carries the resulting session cookie.  Pass the session to ``req()`` as
    the *session* keyword argument to make cookie-authenticated requests.
    """
    session = requests.Session()
    # The endpoint replies with a 303 redirect to "/" and sets the session
    # cookie on that response. Don't follow the redirect — "/" 500s in the
    # test setup (no frontend mounted) and the cookie is already captured.
    req(
        "api/auth/basic/login/session",
        "post",
        b={"username": username, "password": pw},
        session=session,
        allow_redirects=False,
        expected_http_code=303,
    )
    # Re-set the cookie with the correct domain so requests sends it back
    for cookie in list(session.cookies):
        session.cookies.set(
            cookie.name,
            cookie.value,
            domain=server_config.SERVER_LISTENING_HOST,
            path="/",
        )
    return session


# ── OIDC login helpers ────────────────────────────────────────────────────────


def oidc_login_get_token(provider_slug: str, sub: str) -> str:
    """Drive the full OIDC authorization-code flow without a browser and return
    the issued API access token.

    The mock provider's authorize page accepts a ``sub`` form field to directly
    issue an auth code, simulating a user picking their account.
    """
    session = requests.Session()
    # Step 1: initiate token login — follow redirects to the mock authorize page.
    resp = session.get(
        f"{get_server_base_url()}/api/auth/oidc/login/{provider_slug}/token",
        allow_redirects=True,
    )
    resp.raise_for_status()
    # Step 2: submit the user selection — follow the redirect through the
    # CheckCheck callback, which returns the access-token JSON.
    resp = session.post(resp.url, data={"sub": sub}, allow_redirects=True)
    resp.raise_for_status()
    return resp.json()["access_token"]


def oidc_login_get_session(provider_slug: str, sub: str) -> requests.Session:
    """Drive the OIDC flow and return a ``requests.Session`` carrying the
    resulting session cookie (mirrors the browser session-login path)."""
    session = requests.Session()
    resp = session.get(
        f"{get_server_base_url()}/api/auth/oidc/login/{provider_slug}/session",
        allow_redirects=True,
    )
    resp.raise_for_status()
    # Submit the account selection, then walk the redirect chain manually. The
    # CheckCheck callback sets the session cookie on its own redirect response;
    # we must capture that cookie but stop before following its final redirect to
    # "/", which 500s in the test setup (no frontend mounted).
    resp = session.post(resp.url, data={"sub": sub}, allow_redirects=False)
    while resp.is_redirect:
        if "/api/auth/oidc/callback/" in resp.url:
            # This response carries the session cookie (captured into the jar
            # already) — do not follow it to the app root.
            break
        resp = session.get(resp.headers["Location"], allow_redirects=False)
    for cookie in list(session.cookies):
        session.cookies.set(
            cookie.name,
            cookie.value,
            domain=server_config.SERVER_LISTENING_HOST,
            path="/",
        )
    return session


# ── HTTP client ───────────────────────────────────────────────────────────────


def req(
    endpoint: str,
    method: Literal["get", "post", "put", "patch", "delete"] = "get",
    q: Dict[str, Any] = None,
    b: Dict = None,
    f: Dict = None,
    expected_http_code: int = None,
    suppress_auth: bool = False,
    tolerated_error_codes: List[int] = None,
    tolerated_error_body: List[Dict | str] = None,
    access_token: str = None,
    session: Optional[requests.Session] = None,
    allow_redirects: bool = True,
) -> Dict[str, Any] | str | bytes:
    if tolerated_error_codes is None:
        tolerated_error_codes = []
    if tolerated_error_body is None:
        tolerated_error_body = []

    requestor = session if session is not None else requests
    http_method_func = getattr(requestor, method)
    http_method_func_params: Dict = {}
    http_method_func_headers: Dict = {}

    if q:
        http_method_func_params["params"] = {
            k: ",".join(map(str, v)) if isinstance(v, list) else v
            for k, v in q.items()
        }
    if b:
        http_method_func_params["json"] = b
    if f:
        http_method_func_headers["Content-Type"] = "application/x-www-form-urlencoded"
        http_method_func_params["data"] = f

    if not allow_redirects:
        http_method_func_params["allow_redirects"] = False

    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    url = f"{get_server_base_url()}{endpoint}"
    http_method_func_params["url"] = url

    if session is None:
        resolved_token = access_token if access_token is not None else get_access_token()
        if resolved_token and not suppress_auth:
            http_method_func_headers["Authorization"] = f"Bearer {resolved_token}"
    # When a session is passed, cookies are handled automatically by the
    # requests.Session object — no need to set a Cookie header manually.

    _log_token = access_token or get_access_token()
    if session is not None:
        log_auth = "session"
    elif _log_token and not suppress_auth:
        log_auth = f"Bearer {_log_token[:16]}...(truncated)"
    else:
        log_auth = "none"
    print(
        f"TEST-REQUEST:{method.upper()} {endpoint} "
        f"params={list(http_method_func_params.keys())} auth={log_auth}"
    )

    if http_method_func_headers:
        http_method_func_params["headers"] = http_method_func_headers

    r = http_method_func(**http_method_func_params)

    if expected_http_code is not None:
        assert r.status_code == expected_http_code, (
            f"Expected HTTP {expected_http_code}, got {r.status_code} "
            f"for {method.upper()} {endpoint}\n{r.content}"
        )
    else:
        try:
            r.raise_for_status()
        except requests.HTTPError as err:
            if r.status_code not in tolerated_error_codes:
                try:
                    body = r.json()
                except requests.exceptions.JSONDecodeError:
                    body = r.content
                if body not in tolerated_error_body and body != tolerated_error_body:
                    if body:
                        print("Error body:", body)
                    raise err
    try:
        return r.json()
    except requests.exceptions.JSONDecodeError:
        return r.content


# ── Assertion helpers ─────────────────────────────────────────────────────────


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
        raise TypeError(f"Expected List for required_keys, got {type(required_keys)}")
    for k, v in required_keys_and_val.items():
        try:
            if d[k] != v:
                if raise_if_not_fullfilled:
                    where = f" in dict '{exception_dict_identifier}'" if exception_dict_identifier else ""
                    raise ValueError(f"Key '{k}'{where}: expected '{v}', got '{d[k]}'")
                return False
        except KeyError:
            if raise_if_not_fullfilled:
                where = f" in dict '{exception_dict_identifier}'" if exception_dict_identifier else ""
                raise KeyError(f"Missing key '{k}'{where}")
            return False
    for k in required_keys:
        if k not in d:
            if raise_if_not_fullfilled:
                where = f" in dict '{exception_dict_identifier}'" if exception_dict_identifier else ""
                raise KeyError(f"Missing expected key '{k}'{where}")
            return False
    return True


def find_first_dict_in_list(
    l: List[Dict],
    required_keys_and_val: Dict = None,
    required_keys: List = None,
    raise_if_not_found: bool = True,
    exception_dict_identifier: str = None,
) -> Dict | bool:
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
            f"No dict matching {required_keys_and_val!r} / keys {required_keys} found in list"
        )
    return False


def list_contains_dict_that_must_contain(
    l: List[Dict],
    required_keys_and_val: Dict = None,
    required_keys: List = None,
    raise_if_not_fullfilled: bool = True,
    exception_dict_identifier: str = None,
) -> bool:
    return bool(
        find_first_dict_in_list(
            l,
            required_keys_and_val=required_keys_and_val,
            required_keys=required_keys,
            raise_if_not_found=raise_if_not_fullfilled,
            exception_dict_identifier=exception_dict_identifier,
        )
    )


# ── Type conversion ───────────────────────────────────────────────────────────


def dictyfy(val: str | sqlmodel.SQLModel | pydantic.BaseModel | dict | list) -> dict | list:
    if isinstance(val, list):
        return [dictyfy(item) for item in val]
    if isinstance(val, dict):
        return {k: dictyfy(v) for k, v in val.items()}
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, (dict, list)):
                return dictyfy(parsed)
            return val
        except json.JSONDecodeError:
            return val
    if isinstance(val, (sqlmodel.SQLModel, pydantic.BaseModel)):
        return dictyfy(json.loads(val.model_dump_json(exclude_unset=True)))
    return val


# ── Test discovery ────────────────────────────────────────────────────────────


def get_test_functions_from_file_or_module(
    file_or_module: str | Path | types.ModuleType,
):
    if isinstance(file_or_module, (str, Path)):
        if not os.path.isfile(file_or_module):
            raise FileNotFoundError(f"No such file: {file_or_module}")
        module_name = os.path.splitext(os.path.basename(file_or_module))[0]
        spec = importlib.util.spec_from_file_location(module_name, file_or_module)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = file_or_module

    return [
        (name, func)
        for name, func in vars(module).items()
        if (name.startswith("test_") or name.startswith("tests_"))
        and isinstance(func, types.FunctionType)
    ]


def import_test_modules(from_dir: pathlib.Path) -> List[types.ModuleType]:
    modules: List[types.ModuleType] = []
    for py_file in sorted(from_dir.glob("tests_*.py")):
        module_name = py_file.stem
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules.append(module)
    return modules


# ── Test data helpers ─────────────────────────────────────────────────────────


def get_dot_env_file_variable(filepath: str, key: str, default: Any = None) -> str | None:
    if not os.path.exists(filepath):
        return default
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                var_key, var_value = line.split("=", 1)
                if var_key.strip() == key:
                    return var_value.strip() or default
    return default


def create_test_user(user_name: str, password: str, email: str) -> Dict:
    """Create a user via the admin API, set their password, and return the user dict."""
    user_raw = req(
        "api/user",
        "post",
        b={"user_name": user_name, "email": email, "display_name": user_name},
        tolerated_error_body=[{"detail": "User allready exists"}],
    )
    if user_raw == {"detail": "User allready exists"}:
        # look up the existing user
        users = req("api/user", q={"incl_deactivated": True})
        user_raw = find_first_dict_in_list(users["items"], {"user_name": user_name})

    user_id = user_raw["id"]
    req(
        f"api/user/{user_id}/password",
        "put",
        f={"new_password": password, "new_password_repeated": password},
    )
    req(f"api/user/{user_id}", "patch", b={"deactivated": False, "roles": []})
    return user_raw


def random_past_date(
    min_date: datetime.date = None, random_gen: random.Random = None
) -> datetime.date:
    today = datetime.date.today()
    if random_gen is None:
        random_gen = random.Random()
    if min_date is None:
        min_date = today - datetime.timedelta(days=730)
    delta_days = (today - min_date).days
    return min_date + datetime.timedelta(days=random_gen.randint(0, delta_days))
