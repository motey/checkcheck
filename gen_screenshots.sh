#!/usr/bin/env bash
# Regenerate the images in docs/screenshots/ from the current code.
#
# Boots a throwaway Postgres, seeds it with the deterministic dev dataset,
# builds the frontend, starts the backend, and drives Playwright to write every
# screenshot the docs reference. Everything is torn down afterwards.
#
# This is a manual, pre-release chore — deliberately NOT wired into CI. The
# output is ~10 binary PNGs; regenerating on every push would bloat the repo
# history for no benefit. Run it when the UI has visibly changed, then eyeball
# the diff before committing.
#
# Prerequisites:
#   - Docker installed and daemon running
#   - Backend venv set up:  source build_server_dev_env.sh
#   - Playwright browsers:  cd CheckCheck/frontend && bun run test:e2e:install
#
# Usage:
#   ./gen_screenshots.sh                  # rebuild frontend, regenerate everything
#   ./gen_screenshots.sh --no-build       # reuse the existing .output/public
#   ./gen_screenshots.sh --headed         # watch the browser do it
#   ./gen_screenshots.sh --only menus     # regenerate matching spec files only

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

#######################################
# Configuration
#######################################
CONTAINER_NAME="checkcheck-screenshots-postgres"
POSTGRES_USER="checkcheck_shots"
POSTGRES_PW="checkcheck_shots"
POSTGRES_DB="checkcheck_shots"
# Dedicated port: dev uses 5434, E2E uses 5435. Never collide with a running
# dev database — this script wipes whatever it points at.
POSTGRES_PORT=5436
SERVER_PORT=8183

FRONTEND_DIR="$REPO_ROOT/CheckCheck/frontend"
BACKEND_DIR="$REPO_ROOT/CheckCheck/backend"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python"
SERVER_SCRIPT="$BACKEND_DIR/screenshots/start_screenshot_server.py"

export SQL_DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PW}@localhost:${POSTGRES_PORT}/${POSTGRES_DB}"

# Pin the version stamp rendered in the sidebar. Without this, setuptools-scm
# emits a dev string (v0.2.1.dev5+g251ad1d97...) that changes with every commit,
# so every regeneration would diff all screenshots and leak dev version noise
# into public docs. Bump this deliberately at release time.
export SETUPTOOLS_SCM_PRETEND_VERSION="${SCREENSHOT_VERSION:-0.2.1}"

#######################################
# Parse arguments
#######################################
DO_BUILD=true
ONLY_FILTER=""
PW_EXTRA=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-build) DO_BUILD=false ;;
        --headed)   PW_EXTRA+=(--headed) ;;
        --only)     ONLY_FILTER="$2"; shift ;;
        --only=*)   ONLY_FILTER="${1#*=}" ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
    shift
done

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "❌  Backend venv not found at $VENV_PYTHON"
    echo "    Run: source build_server_dev_env.sh"
    exit 1
fi

#######################################
# Helpers
#######################################
SERVER_PID=""

# The backend rewrites CheckCheck/openapi.json on boot, stamping it with the
# running version. Since we deliberately run under a pinned fake version, that
# would commit "0.2.1" into a tracked file as a side effect of taking pictures.
# Snapshot the bytes now and put them back on the way out.
OPENAPI_FILE="$REPO_ROOT/CheckCheck/openapi.json"
OPENAPI_BACKUP="$(mktemp)"
[[ -f "$OPENAPI_FILE" ]] && cp "$OPENAPI_FILE" "$OPENAPI_BACKUP"

pg_ready() {
    docker exec "$CONTAINER_NAME" \
        pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" &>/dev/null
}

wait_for_pg() {
    local deadline=$(( $(date +%s) + 30 ))
    until pg_ready; do
        if [[ $(date +%s) -gt $deadline ]]; then
            echo "❌  Postgres did not become ready within 30 s"
            docker logs "$CONTAINER_NAME" | tail -20
            exit 1
        fi
        sleep 0.5
    done
    echo "✔  Postgres ready"
}

cleanup() {
    trap '' SIGINT SIGTERM
    if [[ -n "$SERVER_PID" ]]; then
        echo "▶  Stopping screenshot backend …"
        # The server runs under setsid, so its PID leads its process group;
        # signalling the negative PID reaches the uvicorn child too.
        kill -TERM -- "-$SERVER_PID" 2>/dev/null || kill -TERM "$SERVER_PID" 2>/dev/null || true
        sleep 1
        kill -KILL -- "-$SERVER_PID" 2>/dev/null || true
    fi
    echo "▶  Removing Postgres container …"
    docker stop "$CONTAINER_NAME" &>/dev/null || true
    docker rm   "$CONTAINER_NAME" &>/dev/null || true

    # Undo the server's openapi.json version stamp (see OPENAPI_BACKUP above).
    if [[ -s "$OPENAPI_BACKUP" ]]; then
        if ! cmp -s "$OPENAPI_BACKUP" "$OPENAPI_FILE"; then
            cp "$OPENAPI_BACKUP" "$OPENAPI_FILE"
            echo "✔  Restored CheckCheck/openapi.json"
        fi
        rm -f "$OPENAPI_BACKUP"
    fi

    echo "✔  Cleanup done"
}
trap cleanup EXIT SIGINT SIGTERM

#######################################
# 1. Throwaway Postgres
#######################################
# Remove any leftover container from an interrupted run.
docker stop "$CONTAINER_NAME" &>/dev/null || true
docker rm   "$CONTAINER_NAME" &>/dev/null || true

echo "▶  Starting Postgres (port ${POSTGRES_PORT}) …"
docker run -d \
    --name "$CONTAINER_NAME" \
    -e POSTGRES_USER="$POSTGRES_USER" \
    -e POSTGRES_PASSWORD="$POSTGRES_PW" \
    -e POSTGRES_DB="$POSTGRES_DB" \
    -p "${POSTGRES_PORT}:5432" \
    postgres:16-alpine >/dev/null

wait_for_pg

#######################################
# 2. Build the frontend
#######################################
if [[ "$DO_BUILD" == "true" ]]; then
    echo "▶  Building frontend …"
    ( cd "$FRONTEND_DIR" && bunx nuxt generate )
    echo "✔  Frontend built"
else
    if [[ ! -d "$FRONTEND_DIR/.output/public" ]]; then
        echo "❌  --no-build given but $FRONTEND_DIR/.output/public does not exist"
        exit 1
    fi
    echo "✔  Reusing existing frontend build"
fi

#######################################
# 3. Seed the deterministic dataset
#######################################
# Runs before the server: the seeder performs the same schema/migration
# bootstrap the server does, so there is no race against a live server.
echo "▶  Seeding screenshot dataset …"
"$VENV_PYTHON" "$SERVER_SCRIPT" --seed-only
echo "✔  Seeded"

#######################################
# 4. Start the backend
#######################################
echo "▶  Starting screenshot backend (port ${SERVER_PORT}) …"
setsid "$VENV_PYTHON" "$SERVER_SCRIPT" &
SERVER_PID=$!

# The script prints READY once /api/health answers; poll the endpoint directly
# so a crashed server fails fast instead of hanging.
deadline=$(( $(date +%s) + 90 ))
until curl -sf "http://localhost:${SERVER_PORT}/api/health" >/dev/null 2>&1; do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "❌  Screenshot backend exited during startup"
        exit 1
    fi
    if [[ $(date +%s) -gt $deadline ]]; then
        echo "❌  Screenshot backend not ready within 90 s"
        exit 1
    fi
    sleep 0.5
done
echo "✔  Backend ready"

#######################################
# 5. Generate
#######################################
echo "▶  Generating screenshots …"
cd "$FRONTEND_DIR"
# The local @playwright/test CLI via bun — bare `bunx playwright` can resolve a
# different cached version and break test collection.
bun x playwright test \
    --config=playwright.screenshots.config.ts \
    "${PW_EXTRA[@]+"${PW_EXTRA[@]}"}" \
    ${ONLY_FILTER:+"$ONLY_FILTER"}

echo ""
echo "✔  Screenshots written to docs/screenshots/"
echo "   Review the diff before committing:  git diff --stat docs/screenshots/"
