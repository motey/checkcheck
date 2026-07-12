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
cd "$SCRIPT_DIR/CheckCheck/backend"
python -m pytest "${PYTEST_ARGS[@]}"

# Second pass — the Phase 8 invite/accept flow is gated by a server-side config
# flag, so it needs the test server booted with SHARING_REQUIRE_INVITE_ACCEPT on.
# Run only the invite module in that mode (skipped only when the user narrowed the
# run to a specific path/-k). The flag-off no-regression case runs in the pass
# above; this pass runs the flag-on cases.
if [ "$HAS_SELECTION" -eq 0 ]; then
    echo "=== invite-flow pass (SHARING_REQUIRE_INVITE_ACCEPT=1) ==="
    CHECKCHECK_TEST_SHARING_REQUIRE_INVITE_ACCEPT=1 \
        python -m pytest --db=postgres \
            tests/tests_sharing_invites.py \
            tests/tests_sharing_groups.py \
            tests/tests_shared_position_orphan.py
fi
