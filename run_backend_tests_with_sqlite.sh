#!/usr/bin/env bash
# Run the CheckCheck backend test suite (pytest) against SQLite.
#
# The SQLite DB file persists after the run at
# CheckCheck/backend/tests/testdb.sqlite for inspection (set
# CHECKCHECK_TESTS_RESET_DB=false to keep it across runs).
#
# Postgres is the primary target (run_backend_tests_with_postgres.sh); this is
# the quick, Docker-free path for local development.
#
# Prerequisites:
#   - Backend venv active:  source build_server_dev_env.sh
#
# Usage:
#   ./run_backend_tests_with_sqlite.sh                 # full suite
#   ./run_backend_tests_with_sqlite.sh --dev           # stop at first failure, verbose
#   ./run_backend_tests_with_sqlite.sh tests/tests_auth.py   # a single file
#   ./run_backend_tests_with_sqlite.sh -k checklist    # filter by name
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

PYTEST_ARGS=("--db=sqlite")
for arg in "$@"; do
    if [ "$arg" = "--dev" ]; then
        PYTEST_ARGS+=("-x" "-s" "--tb=long" "--log-cli-level=DEBUG")
    else
        PYTEST_ARGS+=("$arg")
    fi
done

# No path is passed: pytest falls back to testpaths=["tests"] from pyproject.toml.
# A path/-k given as an argument therefore narrows the run to just that selection.
cd "$SCRIPT_DIR/CheckCheck/backend"
python -m pytest "${PYTEST_ARGS[@]}"
