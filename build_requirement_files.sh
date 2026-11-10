#!/bin/bash
# Lock the backend dependencies and export the pinned requirements*.txt files.
#
# CheckCheck/backend/pyproject.toml declares the dependencies loosely (lower or
# upper bounds only where a reason is documented next to the entry). pdm.lock
# holds the exact versions, and the three requirements*.txt files are exports of
# that lock (the Docker image installs requirements.txt). Commit all of them
# together. See "Updating dependencies" in docs/development.md.
#
# Usage (from the repo root):
#   ./build_requirement_files.sh            # keep existing pins, only resolve
#                                           # what pyproject.toml changes need
#                                           # (use after adding/removing a dep)
#   ./build_requirement_files.sh --upgrade  # re-resolve everything to the newest
#                                           # versions pyproject.toml allows
#                                           # (dependency update sessions only)
#
# Afterwards install the result into the backend venv and run the tests:
#   (cd CheckCheck/backend && PDM_IGNORE_ACTIVE_VENV=1 pdm install -G dev -G test)
#   ./run_backend_tests_with_sqlite.sh && ./run_backend_tests_with_postgres.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/CheckCheck/backend"

case "${1:-}" in
    "")        LOCK_ARGS=(--update-reuse) ;;
    --upgrade) LOCK_ARGS=() ;;
    *) echo "Unknown argument: $1 (expected nothing or --upgrade)" >&2; exit 2 ;;
esac

# The `docs` group (psyplus) is deliberately not part of the lock: pdm resolves
# only the default, dev and test groups here.
pdm lock -G dev -G test "${LOCK_ARGS[@]}"

pdm export --without dev,test -o requirements.txt --without-hashes
pdm export --group dev -o requirements_dev.txt --without-hashes
pdm export --group test -o requirements_test.txt --without-hashes
