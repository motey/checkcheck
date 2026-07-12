#!/usr/bin/env python
"""Generate the committed config reference + YAML template from the config model.

The single source of truth for configuration is the pydantic-settings model in
``CheckCheck/backend/checkcheckserver/config.py``. This script renders two artifacts from it with
`psyplus <https://github.com/DZD-eV-Diabetes-Research/pydantic-settings-yaml-plus>`_:

* ``docs/CONFIG_REFERENCE.md`` - the exhaustive per-field reference (type, default, env-var,
  description, allowed values).
* ``config.example.yml`` - a fully commented, fillable YAML template.

Both are committed so they show up in code review and on GitHub. To avoid drift they are generated,
never hand-edited: change the ``Field(...)`` metadata in ``config.py`` and re-render.

    ./gen_config_docs.sh          # regenerate both files (root wrapper around this script)
    ./gen_config_docs.sh --check  # verify they are current; exit 1 on drift (CI / pre-commit)

Two small adaptations are made for CheckCheck's model:

* ``SecretStr`` fields need a placeholder for the YAML template (psyplus only knows plain ``str``),
  and ruamel needs to know how to serialise a ``SecretStr`` value.
* Two settings default to an absolute path derived from the checkout location. Those are rewritten
  to a repo-relative path so the committed files are identical on every machine (and the --check
  drift guard is meaningful).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "CheckCheck" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from pydantic import SecretStr  # noqa: E402
from ruamel.yaml.representer import RoundTripRepresenter  # noqa: E402

import psyplus.yaml_generator as _yg  # noqa: E402

# A required SecretStr has no default, so psyplus' placeholder builder produces None and validation
# fails. Hand it a clearly-marked placeholder that also satisfies the 64-char minimum on the two
# signing secrets.
_PLACEHOLDER_SECRET = (
    "CHANGE_ME_generate_a_long_random_string_at_least_64_characters_long"
)
_orig_placeholder_for = _yg._placeholder_for


def _placeholder_for(annotation: object) -> object:
    if annotation is SecretStr:
        return _PLACEHOLDER_SECRET
    return _orig_placeholder_for(annotation)


_yg._placeholder_for = _placeholder_for
RoundTripRepresenter.add_representer(
    SecretStr, lambda r, d: r.represent_str(d.get_secret_value())
)

from psyplus import YamlSettingsPlus  # noqa: E402

from checkcheckserver.config import Config  # noqa: E402

MARKDOWN_PATH = REPO_ROOT / "docs" / "CONFIG_REFERENCE.md"
YAML_PATH = REPO_ROOT / "config.example.yml"

_MARKDOWN_BANNER = (
    "<!-- GENERATED FILE - do not edit by hand.\n"
    "     Regenerate with `./gen_config_docs.sh` after changing config.py.\n"
    "     A readable introduction to configuration lives in docs/configuration.md. -->\n\n"
)
_YAML_BANNER = (
    "# CheckCheck configuration template - GENERATED from config.py.\n"
    "# Regenerate with `./gen_config_docs.sh`; do not edit by hand.\n"
    "# Copy this to config.yml and fill in the required (Required: True) values. Every setting can\n"
    "# also be supplied via its environment variable (shown per field below); env vars win over the\n"
    "# file. A readable walkthrough lives in docs/configuration.md.\n"
)


def _normalise(text: str) -> str:
    """Make the rendered text machine-independent and free of stray typography.

    * Absolute paths that embed the checkout location become repo-relative.
    * The unicode em dash psyplus puts in its heading becomes a plain hyphen.
    * Per-line trailing whitespace is dropped so a fresh render matches the committed file even
      after whitespace-trimming pre-commit hooks.
    """
    text = text.replace(str(REPO_ROOT) + "/", "./").replace(str(REPO_ROOT), ".")
    text = text.replace("—", "-")
    return "\n".join(line.rstrip() for line in text.splitlines())


def render_markdown() -> str:
    body = _normalise(YamlSettingsPlus(Config).render_markdown())
    return _MARKDOWN_BANNER + body.rstrip() + "\n"


def render_yaml() -> str:
    body = _normalise(YamlSettingsPlus(Config).render_yaml())
    return _YAML_BANNER + "\n" + body.rstrip() + "\n"


def _check(path: Path, expected: str) -> bool:
    actual = path.read_text(encoding="utf-8") if path.exists() else ""
    if actual == expected:
        print(f"OK    {path.relative_to(REPO_ROOT)}")
        return True
    print(f"DRIFT {path.relative_to(REPO_ROOT)} - run ./gen_config_docs.sh")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed files match the model instead of writing them (exit 1 on drift).",
    )
    args = parser.parse_args(argv)

    targets = [(MARKDOWN_PATH, render_markdown()), (YAML_PATH, render_yaml())]

    if args.check:
        ok = all(_check(path, content) for path, content in targets)
        return 0 if ok else 1

    for path, content in targets:
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
