from pathlib import Path, PurePath


def try_find_git_root(start_path: Path) -> Path | None:
    """
    Traverses up from the given path (or the current file's directory)
    until a .git directory is found. Returns the Path to the git root.
    Returns None if no git root found.
    """
    for path in [start_path] + list(start_path.parents):
        possible_dot_git_path = Path(path, ".git")
        if possible_dot_git_path.is_dir():
            return possible_dot_git_path

    raise None


try:
    from checkcheckserver.__version__ import __version__
except ModuleNotFoundError:
    # get version from git
    from setuptools_scm import get_version
    from os import path

    __version__ = get_version(try_find_git_root(Path(__file__).parent).parent)


try:
    from checkcheckserver.__version__ import __version_git_branch__
except ModuleNotFoundError:
    # get version from git
    from setuptools_scm import get_version
    from os import path

    dot_git_dir = try_find_git_root(Path(__file__).parent)

    head_path = Path(dot_git_dir, "HEAD")
    with head_path.open("r") as f:
        content = f.read().splitlines()
    branch = None
    for line in content:
        if line[0:4] == "ref:":
            branch = line.partition("refs/heads/")[2]
        if branch is not None:
            break
    __version_git_branch__ = branch
