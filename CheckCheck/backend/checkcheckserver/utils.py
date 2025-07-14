from typing import List, Literal, Dict, Union, Tuple, Annotated, Optional
from pathlib import Path, PurePath
import uuid
import random
import string
import getversion
import json
import fastapi
import pydantic
import os


def to_path(
    *args: str | Path | PurePath | List[str | Path | PurePath],
    absolute: bool = True,
    expanduser: bool = True,
) -> Path:
    """Combine path fragments into one pathlib.Path

    Args:
        * (str, Path, PurePath): Provide multiple path fragment in any reasonable format that will be combined in one Path
        absolute (bool, optional): _description_. Defaults to True.
        expanduser (bool, optional): _description_. Defaults to True.

    Returns:
        Path: _description_
    """
    result_path_fragments: List[Path] = []
    for arg in args:
        if isinstance(arg, str):
            result_path_fragments.append(Path(arg))
        elif isinstance(arg, PurePath):
            result_path_fragments.append(arg)
        elif isinstance(arg, PurePath):
            result_path_fragments.append(Path(arg))
        elif isinstance(arg, list):
            for sub_arg in arg:
                result_path_fragments.append(
                    to_path(sub_arg, expanduser=False, absolute=False)
                )
    result_path = Path.joinpath(*result_path_fragments)
    if expanduser:
        result_path = result_path.expanduser()
    if absolute:
        result_path = result_path.absolute()
    return result_path


def get_random_string(
    length: int = 32,
    allowed_char_sets: List[str] = [string.ascii_lowercase, string.digits],
) -> str:
    letters = "".join(allowed_char_sets)
    return "".join(random.choice(letters) for i in range(length))


def val_means_true(s: int | str | bool) -> bool:
    if isinstance(s, bool):
        return s
    if isinstance(s, int) and s == 1:
        return True
    if s.lower() in ["true", "yes", "y", "1"]:
        return True
    return False


def set_version_file(base_dir=Path("./")) -> Path:
    import checkcheckserver
    from importlib import reload

    version_file_path = Path(PurePath(base_dir, "__version__.py"))
    if version_file_path.exists():
        print(f"Delete {version_file_path.absolute()}")
        version_file_path.unlink()
    # checkcheckserver = reload(checkcheckserver)
    content = f'__version__="{getversion.get_module_version.__wrapped__(checkcheckserver)[0]}"'
    version_file_path.write_text(content)
    return version_file_path


def sanitize_string(s: str, replace_space_with: str = "_") -> str:
    return "".join(
        char.lower() if char.isalpha() else "_" if char == " " else char
        for char in s
        if char.isalnum() or char == " "
    )


def slugify_string(s: str, spacer_string: str = "-") -> str:
    return "".join(
        char.lower() if char.isalnum() else spacer_string
        for char in s
        if char.isalnum() or char == " "
    )


class JSONEncoderCheckCheckCustom(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, uuid.UUID):
            # if the obj is uuid, we simply return the value of uuid
            return str(obj)
        return json.JSONEncoder.default(self, obj)


def http_exception_to_resp_desc(e: fastapi.HTTPException) -> Dict[int, str]:
    """Translate a fastapi.HTTPException into fastapi OpenAPI response description to be used as a additional response
    https://fastapi.tiangolo.com/advanced/additional-responses/
    """
    return {
        e.status_code: {
            "description": e.detail,
            "model": pydantic.create_model("Error", detail=(Dict[str, str], e.detail)),
        },
    }


def path_is_parent(parent_path: Path | str, child_path: Path | str) -> bool:
    # check if a path is a subpath of another.
    # e.g. "/tmp/parent" is parent of "/tmp/paren/child/file.txt"
    # kudos to: https://stackoverflow.com/a/37095733/12438690
    # Smooth out relative path names, note: if you are concerned about symbolic links, you should use os.path.realpath too
    parent_path = os.path.abspath(parent_path)
    child_path = os.path.abspath(child_path)

    # Compare the common path of the parent and child path with the common path of just the parent path. Using the commonpath method on just the parent path will regularise the path name in the same way as the comparison that deals with both paths, removing any trailing path separator
    return os.path.commonpath([parent_path]) == os.path.commonpath(
        [parent_path, child_path]
    )


def generate_session_name(request: fastapi.Request) -> str:
    # Combine IP and user agent to create a unique identifier
    # Extract the client's IP address (may vary based on your deployment setup)
    client_ip = request.client.host

    # Extract the user agent from the headers
    user_agent = request.headers.get("user-agent", "unknown-user-agent")
    return f"{user_agent} (initial IP:{client_ip})"


def get_version_git_branch_name() -> str:

    from checkcheckserver import __version_git_branch__

    return __version_git_branch__


class Unset:
    pass


def get_value_from_first_dict_with_key(dict_list: List[Dict], key_name: str, default=Unset):
    """Returns the value associated with the given key from the first dictionary in the list
    that contains the key.

    Args:
        dict_list (_type_): _description_
        key_name (_type_): _description_

    Returns:
        _type_: _description_
    """
    res = next((d[key_name] for d in dict_list if key_name in d), default)
    if res == Unset:
        raise ValueError(f"Can not find '{key_name}' in dicts")
    return res
