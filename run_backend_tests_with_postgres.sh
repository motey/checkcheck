#!/usr/bin/env bash
# Run the CheckCheck backend test suite (pytest) against PostgreSQL.
#
# The pytest conftest starts a throwaway Docker Postgres container, runs the
# suite, then stops and removes the container. Postgres is the primary target.
#
# Prerequisites:
#   - Docker installed and the daemon running
#   - Backend venv set up:  source build_server_dev_env.sh (once; this script
#     uses CheckCheck/backend/.venv directly, no activation needed)
#
# Usage:
#   ./run_backend_tests_with_postgres.sh                 # full suite
#   ./run_backend_tests_with_postgres.sh --dev           # stop at first failure, verbose
#   ./run_backend_tests_with_postgres.sh tests/tests_auth.py   # a single file
#   ./run_backend_tests_with_postgres.sh -k checklist    # filter by name
#
# Environment:
#   PYTEST_JUNIT_XML   Write a JUnit XML report for the main pass to this path.
#                      CI uses it to count tests for the README badge; leave it
#                      unset locally.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

PYTEST_ARGS=("--db=postgres")
if [ -n "${PYTEST_JUNIT_XML:-}" ]; then
    PYTEST_ARGS+=("--junitxml=$PYTEST_JUNIT_XML")
fi
HAS_SELECTION=0
for arg in "$@"; do
    if [ "$arg" = "--dev" ]; then
        PYTEST_ARGS+=("-x" "-s" "--tb=long" "--log-cli-level=DEBUG")
    else
        PYTEST_ARGS+=("$arg")
        HAS_SELECTION=1
    fi
done

# No path is passed: pytest falls back to testpaths=["tests"] from pyproject.toml.
# A path/-k given as an argument therefore narrows the run to just that selection.
# Always run on the pdm-managed backend venv (the same one the dev server and
# the E2E suite use), whatever virtualenv happens to be active in the shell.
PYTHON="$SCRIPT_DIR/CheckCheck/backend/.venv/bin/python"
if ! "$PYTHON" -c "" &>/dev/null; then
    echo "❌ Backend venv not usable at $PYTHON." >&2
    echo "   Set it up from the repo root with: source build_server_dev_env.sh" >&2
    exit 1
fi

cd "$SCRIPT_DIR/CheckCheck/backend"
"$PYTHON" -m pytest "${PYTEST_ARGS[@]}"

# Second pass — the Phase 8 invite/accept flow is gated by a server-side config
# flag, so it needs the test server booted with SHARING_REQUIRE_INVITE_ACCEPT on.
# Run only the invite module in that mode (skipped only when the user narrowed the
# run to a specific path/-k). The flag-off no-regression case runs in the pass
# above; this pass runs the flag-on cases.
if [ "$HAS_SELECTION" -eq 0 ]; then
    echo "=== invite-flow pass (SHARING_REQUIRE_INVITE_ACCEPT=1) ==="
    CHECKCHECK_TEST_SHARING_REQUIRE_INVITE_ACCEPT=1 \
        "$PYTHON" -m pytest --db=postgres \
            tests/tests_sharing_invites.py \
            tests/tests_sharing_groups.py \
            tests/tests_shared_position_orphan.py
fi
