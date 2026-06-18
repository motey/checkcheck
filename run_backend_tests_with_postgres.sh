#!/usr/bin/env bash
# Run the CheckCheck backend test suite (pytest) against PostgreSQL.
#
# The pytest conftest starts a throwaway Docker Postgres container, runs the
# suite, then stops and removes the container. Postgres is the primary target.
#
# Prerequisites:
#   - Docker installed and the daemon running
#   - Backend venv active:  source build_server_dev_env.sh
#
# Usage:
#   ./run_backend_tests_with_postgres.sh                 # full suite
#   ./run_backend_tests_with_postgres.sh --dev           # stop at first failure, verbose
#   ./run_backend_tests_with_postgres.sh tests/tests_auth.py   # a single file
#   ./run_backend_tests_with_postgres.sh -k checklist    # filter by name
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

PYTEST_ARGS=("--db=postgres")
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
