#!/usr/bin/env bash
# Run the Playwright E2E test suite against a PostgreSQL database.
#
# Starts a temporary Docker Postgres container, runs the full suite, then
# removes the container. The SQLite run is unaffected.
#
# Prerequisites:
#   - Docker installed and daemon running
#   - Backend venv set up: source build_server_dev_env.sh
#   - Playwright browsers installed: cd CheckCheck/frontend && bunx playwright install chromium
#
# Usage:
#   ./run_e2e_tests_postgres.sh              # headless
#   ./run_e2e_tests_postgres.sh --headed     # show browser window
#   ./run_e2e_tests_postgres.sh --ui         # open Playwright UI
#   ./run_e2e_tests_postgres.sh auth         # run only tests matching "auth"

set -euo pipefail

#######################################
# Configuration
#######################################
CONTAINER_NAME="checkcheck-e2e-postgres"
POSTGRES_USER="checkcheck_e2e"
POSTGRES_PW="checkcheck_e2e"
POSTGRES_DB="checkcheck_e2e"
POSTGRES_PORT=5435          # dedicated E2E port — avoids collision with dev (5434) or system postgres

export SQL_DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PW}@localhost:${POSTGRES_PORT}/${POSTGRES_DB}"

#######################################
# Helpers
#######################################
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
    echo "▶  Stopping Postgres container …"
    docker stop  "$CONTAINER_NAME" 2>/dev/null || true
    docker rm    "$CONTAINER_NAME" 2>/dev/null || true
    echo "✔  Postgres container removed"
}

#######################################
# Start Postgres
#######################################
# Remove any leftover container from a previous interrupted run
docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm   "$CONTAINER_NAME" 2>/dev/null || true

echo "▶  Starting Postgres container (port ${POSTGRES_PORT}) …"
docker run -d \
    --name "$CONTAINER_NAME" \
    -e POSTGRES_USER="$POSTGRES_USER" \
    -e POSTGRES_PASSWORD="$POSTGRES_PW" \
    -e POSTGRES_DB="$POSTGRES_DB" \
    -p "${POSTGRES_PORT}:5432" \
    postgres:16-alpine

trap cleanup EXIT SIGINT SIGTERM

wait_for_pg

#######################################
# Run tests
#######################################
echo "▶  Running E2E tests against Postgres …"
cd "$(dirname "$0")/CheckCheck/frontend"
bun run test:e2e "$@"
e2e_exit=$?
echo ""
echo "To open the HTML report, run:"
echo "  cd CheckCheck/frontend && bunx playwright show-report"
exit $e2e_exit
