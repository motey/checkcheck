#!/usr/bin/env bash
# Run the CheckCheck Backend Server with an OIDC Mockup server and a PostgreSQL database
# Mainly intended for Development
#
# Usage: ./run_dev_backend_server_with_oidc_on_postgres.sh [--reset]
#   --reset  Stop and remove the existing PostgreSQL container (wiping all data), then start fresh

set -e

# Make every `pdm run` below use the project's own venv ($BACKEND_DIR/.venv)
# rather than reusing whatever virtualenv is active in the caller's shell.
# Otherwise pdm may run against an interpreter that lacks the installed deps
# (e.g. oidc_provider_mock), which surfaces as a ModuleNotFoundError on boot.
export PDM_IGNORE_ACTIVE_VENV=1


#######################################
# Parse arguments
#######################################
RESET_DB=false
for arg in "$@"; do
    case "$arg" in
        --reset) RESET_DB=true ;;
    esac
done


#######################################
# PostgreSQL Configuration
#######################################
POSTGRES_CONTAINER_NAME=checkcheck-dev-postgres
POSTGRES_USER=checkcheck
POSTGRES_PW=checkcheck
POSTGRES_PORT=5434  # use 5434 to avoid conflict with a local postgres

export SQL_DATABASE_URL="postgresql+asyncpg://$POSTGRES_USER:$POSTGRES_PW@localhost:$POSTGRES_PORT/$POSTGRES_USER"


#######################################
# CheckCheck Server Configuration
#######################################
# To overwrite these values create `.env` in `CheckCheck/backend/checkcheckserver/` and set env vars there.
# e.g.:
#   LOG_LEVEL=DEBUG
#   DEBUG_SQL=True

export FRONTEND_FILES_DIR="../frontend/.output/public"


#######################################
# OIDC Configuration
#######################################
export AUTH_OIDC_TOKEN_STORAGE_SECRET=qi3we7gaukb

PROVIDER_DISPLAY_NAME="LocalDevLogin"
CONFIGURATION_ENDPOINT=http://localhost:8884/.well-known/openid-configuration
CLIENT_ID=devdummyid1345
CLIENT_SECRET=devdummysecrect1345
USER_NAME_ATTRIBUTE=name
USER_DISPLAY_NAME_ATTRIBUTE=given_name
USER_MAIL_ATTRIBUTE=email
USER_GROUPS_ATTRIBUTE=groups

export AUTH_OIDC_PROVIDERS=$(cat <<EOF
[{"PROVIDER_DISPLAY_NAME": "${PROVIDER_DISPLAY_NAME}","CONFIGURATION_ENDPOINT":"${CONFIGURATION_ENDPOINT}","CLIENT_ID":"${CLIENT_ID}","CLIENT_SECRET":"${CLIENT_SECRET}","USER_NAME_ATTRIBUTE":"${USER_NAME_ATTRIBUTE}","USER_DISPLAY_NAME_ATTRIBUTE":"${USER_DISPLAY_NAME_ATTRIBUTE}","USER_MAIL_ATTRIBUTE":"${USER_MAIL_ATTRIBUTE}","USER_GROUPS_ATTRIBUTE": "${USER_GROUPS_ATTRIBUTE}"}]
EOF
)

# Store process IDs
PIDS=()


#######################################
# Cleanup function
#######################################
cleanup() {
    # Block re-entry: ignore further signals so repeated Ctrl+C does not
    # restart this handler while it is still tearing things down.
    trap '' SIGINT SIGTERM

    echo "Stopping all processes..."
    # Each job is launched with setsid, so its PID is also its process-group
    # leader. Killing the negative PID signals the whole group (the `pdm run`
    # wrapper AND the uvicorn/python grandchild it spawned), which a plain
    # `kill $PID` on the wrapper would miss.
    for PID in "${PIDS[@]}"; do
        kill -TERM -- "-$PID" 2>/dev/null || kill -TERM "$PID" 2>/dev/null || true
    done

    # Give them a moment to shut down gracefully, then force-kill leftovers.
    sleep 1
    for PID in "${PIDS[@]}"; do
        kill -KILL -- "-$PID" 2>/dev/null || kill -KILL "$PID" 2>/dev/null || true
    done

    # Backstop: nuke anything matching by path in case a PID was missed.
    kill_processes_by_path oidc_provider_mock_server.py
    kill_processes_by_path checkcheckserver/main.py

    echo "Cleanup done."
    echo "PostgreSQL container '$POSTGRES_CONTAINER_NAME' is still running."
    echo "  Connect: $SQL_DATABASE_URL"
    echo "  Stop:    docker stop $POSTGRES_CONTAINER_NAME"
    exit 0
}

trap cleanup SIGINT SIGTERM


#######################################
# Kill processes matching a given string
#######################################
kill_processes_by_path() {
    if [[ -z "$1" ]]; then
        echo "Usage: kill_processes_by_path <search_string>"
        return 1
    fi
    local pids=$(ps axo pid,command | grep "$1" | grep -v grep | awk '{print $1}')
    if [[ -z "$pids" ]]; then
        echo "No matching processes found."
        return 0
    fi
    echo "Killing processes: $pids"
    echo "$pids" | xargs kill -9
}


#######################################
# Wait for PostgreSQL to be ready
#######################################
pg_docker_ready() {
    local attempts="${1:-30}" i=0
    echo "Waiting for PostgreSQL in '$POSTGRES_CONTAINER_NAME'..."
    while [[ $((i++)) -lt $attempts ]]; do
        docker exec "$POSTGRES_CONTAINER_NAME" pg_isready -U "$POSTGRES_USER" &>/dev/null && echo "✓ Postgres ready" && return 0
        sleep 1
    done
    echo "✗ Timeout after $attempts attempts — PostgreSQL did not become ready"
    return 1
}


#######################################
# PostgreSQL container management
#######################################
if [[ "$RESET_DB" == "true" ]]; then
    echo "-- Reset requested: removing existing PostgreSQL container..."
    docker stop "$POSTGRES_CONTAINER_NAME" 2>/dev/null || true
    docker rm   "$POSTGRES_CONTAINER_NAME" 2>/dev/null || true
fi

if docker inspect "$POSTGRES_CONTAINER_NAME" &>/dev/null; then
    if [[ "$(docker inspect -f '{{.State.Running}}' "$POSTGRES_CONTAINER_NAME")" != "true" ]]; then
        echo "Starting existing PostgreSQL container '$POSTGRES_CONTAINER_NAME'..."
        docker start "$POSTGRES_CONTAINER_NAME"
    else
        echo "PostgreSQL container '$POSTGRES_CONTAINER_NAME' is already running."
    fi
else
    echo "Creating and starting PostgreSQL container '$POSTGRES_CONTAINER_NAME'..."
    docker run -d \
        --name "$POSTGRES_CONTAINER_NAME" \
        -e POSTGRES_PASSWORD="$POSTGRES_PW" \
        -e POSTGRES_USER="$POSTGRES_USER" \
        -e POSTGRES_DB="$POSTGRES_USER" \
        -p "$POSTGRES_PORT":5432 \
        docker.io/library/postgres:16-alpine
fi

pg_docker_ready 30
echo ""
echo "# POSTGRES BOOTED — $SQL_DATABASE_URL"
echo ""


#######################################
# Kill zombie processes from former runs
#######################################
echo "Kill zombie processes..."
kill_processes_by_path oidc_provider_mock_server.py
kill_processes_by_path checkcheckserver/main.py


#######################################
# Start OIDC mockup server
#######################################
echo "Start dummy OIDC Provider"
# setsid puts the job in its own process group so cleanup() can signal the
# whole tree, and so a terminal Ctrl+C does not hit it directly (only the
# script's trap does).
setsid bash -c 'cd ./CheckCheck/backend/dev_oidc_server && pdm run oidc_provider_mock_server.py' &
mock_server_PID=$!

for i in {1..3}; do
    if ! kill -0 $mock_server_PID 2>/dev/null; then
        echo "OIDC mockup server failed to start."
        exit 1
    fi
    sleep 1
done
echo "OIDC mockup server seemed to have booted."
PIDS+=($mock_server_PID)


#######################################
# Start CheckCheck Backend
#######################################
setsid bash -c "cd CheckCheck/backend && pdm run ./checkcheckserver/main.py $1" &
PIDS+=($!)

wait
