#!/usr/bin/env bash
# Regenerate the committed configuration documentation from the config model.
#
# CheckCheck/backend/checkcheckserver/config.py is the single source of truth for configuration.
# Two artifacts are rendered from it with psyplus
# (https://github.com/DZD-eV-Diabetes-Research/pydantic-settings-yaml-plus) and committed, so they
# show up in code review and on GitHub:
#
#     docs/CONFIG_REFERENCE.md   exhaustive per-field reference (type, default, env-var, description)
#     config.example.yml         fully commented, fillable YAML template
#
# Never hand-edit those two files. Edit the Field(...) metadata in config.py and re-run this script.
# A readable introduction to configuration lives in docs/configuration.md.
#
# Usage:
#     ./gen_config_docs.sh            # rewrite both files
#     ./gen_config_docs.sh --check    # verify they match the model; exit 1 on drift (CI/pre-commit)
set -euo pipefail
cd "$(dirname "$0")"

# psyplus and the backend package both live in the pdm-managed backend venv (the one that also runs
# the dev server). Fall back to whatever python is on PATH if that venv is missing.
PYTHON="CheckCheck/backend/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    PYTHON="python"
fi

if ! "$PYTHON" -c "import psyplus" 2>/dev/null; then
    echo "psyplus is not installed in $PYTHON."
    echo "Install it into the backend venv with:"
    echo "    CheckCheck/backend/.venv/bin/python -m pip install psyplus"
    exit 1
fi

# Pin the version and hush setuptools-scm's tag-matching warning while importing the package.
export SETUPTOOLS_SCM_PRETEND_VERSION="${SETUPTOOLS_SCM_PRETEND_VERSION:-0.0.0}"
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore::FutureWarning}"

exec "$PYTHON" scripts/gen_config_docs.py "$@"
