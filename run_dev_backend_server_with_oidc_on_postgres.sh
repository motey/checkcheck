#!/usr/bin/env bash
# Run the CheckCheck Backend Server with an OIDC Mockup server and a PostgreSQL database
# Mainly intended for Development
#
# Usage: ./run_dev_backend_server_with_oidc_on_postgres.sh [--reset] [--seed-data [--profile NAME] [--wipe]] [--mail SINK]
#   --reset       Stop and remove the existing PostgreSQL container (wiping all data), then start fresh.
#                 Also removes the Mailpit container, so the dev inbox starts empty.
#   --seed-data   Fill the DB with diverse random dev data (owner = the OIDC 'admin' user) before the
#                 server boots. Idempotent: re-runs skip unless you also pass --wipe. See
#                 CheckCheck/backend/checkcheckserver/dev/seed_dev_data.py for all knobs.
#   --profile     small | medium | large — how much data to generate (default: medium). Implies --seed-data.
#   --wipe        Regenerate the seed's data from scratch (only meaningful with --seed-data).
#   --mail        mailpit | file | console | off — where notification email goes.
#                 Default: mailpit when docker is usable, file otherwise. See the
#                 "Email notification sink" section below and docs/development.md.

set -e

# Make every `pdm run` below use the project's own venv ($BACKEND_DIR/.venv)
# rather than reusing whatever virtualenv is active in the caller's shell.
# Otherwise pdm may run against an interpreter that lacks the installed deps
# (e.g. oidc_provider_mock), which surfaces as a ModuleNotFoundError on boot.
export PDM_IGNORE_ACTIVE_VENV=1


#######################################
# Parse arguments
#######################################
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESET_DB=false
SEED_DATA=false
SEED_PROFILE=medium
SEED_WIPE=false
MAIL_SINK=""  # empty means "decide in resolve_mail_sink"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --reset) RESET_DB=true ;;
        --seed-data) SEED_DATA=true ;;
        --wipe) SEED_DATA=true; SEED_WIPE=true ;;
        --profile) SEED_DATA=true; SEED_PROFILE="$2"; shift ;;
        --profile=*) SEED_DATA=true; SEED_PROFILE="${1#*=}" ;;
        --mail) MAIL_SINK="$2"; shift ;;
        --mail=*) MAIL_SINK="${1#*=}" ;;
    esac
    shift
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
# Email notification sink
#######################################
# Where notification email goes while developing (docs/plans/EMAIL_NOTIFICATIONS.md).
# `mailpit` is the default when docker is usable: it is a real SMTP server, so it
# exercises the same transport production uses, and it comes with a web inbox.
# `file` is the docker-free fallback and drops `.eml` files on disk.
#
# Everything below uses ${VAR:-default}, so exporting any of these before calling
# the script wins over the dev defaults.
MAILPIT_CONTAINER_NAME=checkcheck-dev-mailpit
MAILPIT_SMTP_PORT="${MAILPIT_SMTP_PORT:-1025}"
MAILPIT_UI_PORT="${MAILPIT_UI_PORT:-8025}"
DEV_MAIL_DIR="${DEV_MAIL_DIR:-$SCRIPT_DIR/dev_mail}"


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
    if [[ "$MAIL_SINK" == "mailpit" ]]; then
        echo "Mailpit container '$MAILPIT_CONTAINER_NAME' is still running."
        echo "  Inbox:   http://localhost:$MAILPIT_UI_PORT"
        echo "  Stop:    docker stop $MAILPIT_CONTAINER_NAME"
    fi
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
# Email sink helpers
#######################################
docker_available() {
    command -v docker &>/dev/null && docker info &>/dev/null
}

# Decide which sink to use and reject a value the rest of the script cannot honour.
resolve_mail_sink() {
    if [[ -z "$MAIL_SINK" ]]; then
        if docker_available; then
            MAIL_SINK=mailpit
        else
            MAIL_SINK=file
            echo "No usable docker daemon: falling back to the 'file' mail sink."
        fi
    fi
    case "$MAIL_SINK" in
        mailpit|file|console|off) ;;
        *)
            echo "Unknown --mail sink '$MAIL_SINK' (expected: mailpit, file, console, off)"
            exit 1
            ;;
    esac
    if [[ "$MAIL_SINK" == "mailpit" ]] && ! docker_available; then
        echo "--mail=mailpit needs a running docker daemon. Use --mail=file instead."
        exit 1
    fi
}

# Boot Mailpit (SMTP sink + web inbox) and wait for the port the server needs.
start_mailpit() {
    if docker inspect "$MAILPIT_CONTAINER_NAME" &>/dev/null; then
        if [[ "$(docker inspect -f '{{.State.Running}}' "$MAILPIT_CONTAINER_NAME")" != "true" ]]; then
            echo "Starting existing Mailpit container '$MAILPIT_CONTAINER_NAME'..."
            docker start "$MAILPIT_CONTAINER_NAME" >/dev/null
        else
            echo "Mailpit container '$MAILPIT_CONTAINER_NAME' is already running."
        fi
    else
        echo "Creating and starting Mailpit container '$MAILPIT_CONTAINER_NAME'..."
        docker run -d \
            --name "$MAILPIT_CONTAINER_NAME" \
            -p "$MAILPIT_SMTP_PORT":1025 \
            -p "$MAILPIT_UI_PORT":8025 \
            docker.io/axllent/mailpit:latest >/dev/null
    fi
    # Poll the SMTP port rather than the web UI: it is the one the server talks
    # to, and bash's /dev/tcp keeps this free of curl.
    local i=0
    while [[ $((i++)) -lt 30 ]]; do
        if (exec 3<>"/dev/tcp/127.0.0.1/$MAILPIT_SMTP_PORT") 2>/dev/null; then
            echo "✓ Mailpit ready"
            return 0
        fi
        sleep 1
    done
    echo "✗ Timeout — Mailpit is not listening on port $MAILPIT_SMTP_PORT"
    return 1
}

# Point the server at the chosen sink and shorten the delivery timings.
#
# The production defaults are deliberately patient: a message waits two minutes
# (NOTIFY_EMAIL_SUPPRESS_WINDOW_SECONDS) and is cancelled altogether if you read
# the notification in the app inside that window. Both are right in production and
# make a hand-driven test look like mail is broken, so dev cuts them to seconds.
# The push channel, independent of the mail sink: push is the one channel a
# developer cannot check by reading a log or a Mailpit inbox, so a dev instance
# has it on. The key pair below is a throwaway, committed on purpose and shared
# with the E2E harness (backend/e2e/start_e2e_server.py): a VAPID key only
# proves to a push service which server sent a message, and this one is never
# used by a real deployment. Override any of these to point a dev instance at
# your own pair.
#
# Note that the service worker only exists in a real build (pwa.devOptions is
# off), so `nuxt dev` never registers one and the Enable button will hang there.
# Push needs the backend serving a built frontend.
configure_push_env() {
    export NOTIFY_PUSH_ENABLED="${NOTIFY_PUSH_ENABLED:-true}"
    export VAPID_PUBLIC_KEY="${VAPID_PUBLIC_KEY:-BH-DWhYfjSH5OVS2sjII4dGEP46ueAfPWQklJ_zITJqoWtfKgjHBTDxE_X5jdPms-zR3R9b43oCqYFnxwmwk_PY}"
    export VAPID_PRIVATE_KEY="${VAPID_PRIVATE_KEY:-Mp6hqDn1uEMxJwMvqchFdkrCiID8zYUIvTwDI-rmLSA}"
    export VAPID_CONTACT_EMAIL="${VAPID_CONTACT_EMAIL:-dev@dev.local}"
}

configure_mail_env() {
    if [[ "$MAIL_SINK" == "off" ]]; then
        export EMAIL_ENABLED="${EMAIL_ENABLED:-false}"
        echo ""
        echo "# MAIL SINK: off — this instance sends no notification email"
        echo ""
        return 0
    fi

    export EMAIL_ENABLED="${EMAIL_ENABLED:-true}"
    export EMAIL_FROM_ADDRESS="${EMAIL_FROM_ADDRESS:-checkcheck@dev.local}"
    export NOTIFY_EMAIL_SUPPRESS_WINDOW_SECONDS="${NOTIFY_EMAIL_SUPPRESS_WINDOW_SECONDS:-5}"
    export NOTIFY_DISPATCH_TICK_SECONDS="${NOTIFY_DISPATCH_TICK_SECONDS:-5}"
    # Two surfaces that ship off by default, both worth having in dev: mailing a
    # public link, and the webhook channel (which needs private addresses allowed
    # before it can reach anything running on this machine).
    export SHARING_PUBLIC_LINK_EMAIL_ENABLED="${SHARING_PUBLIC_LINK_EMAIL_ENABLED:-true}"
    export SHARING_INTERNAL_EMAIL_DOMAINS="${SHARING_INTERNAL_EMAIL_DOMAINS:-[\"test.com\"]}"
    export NOTIFY_WEBHOOK_ENABLED="${NOTIFY_WEBHOOK_ENABLED:-true}"
    export NOTIFY_WEBHOOK_ALLOW_PRIVATE_IPS="${NOTIFY_WEBHOOK_ALLOW_PRIVATE_IPS:-true}"

    case "$MAIL_SINK" in
        mailpit)
            start_mailpit
            export EMAIL_TRANSPORT="${EMAIL_TRANSPORT:-smtp}"
            export EMAIL_SMTP_HOST="${EMAIL_SMTP_HOST:-localhost}"
            export EMAIL_SMTP_PORT="${EMAIL_SMTP_PORT:-$MAILPIT_SMTP_PORT}"
            export EMAIL_SMTP_SECURITY="${EMAIL_SMTP_SECURITY:-none}"
            echo ""
            echo "# MAIL SINK: mailpit — inbox at http://localhost:$MAILPIT_UI_PORT"
            echo "#   Messages live in the container's memory:"
            echo "#   'docker restart $MAILPIT_CONTAINER_NAME' empties the inbox."
            echo ""
            ;;
        file)
            mkdir -p "$DEV_MAIL_DIR"
            export EMAIL_TRANSPORT="${EMAIL_TRANSPORT:-file}"
            export EMAIL_FILE_TRANSPORT_DIR="${EMAIL_FILE_TRANSPORT_DIR:-$DEV_MAIL_DIR}"
            echo ""
            echo "# MAIL SINK: file — .eml files land in $DEV_MAIL_DIR"
            echo "#   Open one with any mail client, or read it with:"
            echo "#   python -c 'import email,sys;print(email.message_from_binary_file(open(sys.argv[1],\"rb\")))' <file>"
            echo ""
            ;;
        console)
            export EMAIL_TRANSPORT="${EMAIL_TRANSPORT:-console}"
            echo ""
            echo "# MAIL SINK: console — full messages are logged by the server itself"
            echo ""
            ;;
    esac
}

resolve_mail_sink


#######################################
# PostgreSQL container management
#######################################
if [[ "$RESET_DB" == "true" ]]; then
    echo "-- Reset requested: removing existing PostgreSQL container..."
    docker stop "$POSTGRES_CONTAINER_NAME" 2>/dev/null || true
    docker rm   "$POSTGRES_CONTAINER_NAME" 2>/dev/null || true
    if [[ "$MAIL_SINK" == "mailpit" ]]; then
        echo "-- Reset requested: removing existing Mailpit container..."
        docker stop "$MAILPIT_CONTAINER_NAME" 2>/dev/null || true
        docker rm   "$MAILPIT_CONTAINER_NAME" 2>/dev/null || true
    fi
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
# Email notification sink
#######################################
configure_mail_env
configure_push_env


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
# Seed diverse dev data (optional)
#######################################
# Runs before the server: performs the same idempotent schema/migrations + init
# the server does, then writes data, so there is no race against a live server.
if [[ "$SEED_DATA" == "true" ]]; then
    echo ""
    echo "# SEEDING DEV DATA (profile=$SEED_PROFILE)"
    SEED_ARGS=(--profile "$SEED_PROFILE")
    [[ "$SEED_WIPE" == "true" ]] && SEED_ARGS+=(--wipe)
    ( cd CheckCheck/backend && pdm run python -m checkcheckserver.dev.seed_dev_data "${SEED_ARGS[@]}" )
    echo ""
fi


#######################################
# Start CheckCheck Backend
#######################################
setsid bash -c "cd CheckCheck/backend && pdm run ./checkcheckserver/main.py" &
PIDS+=($!)

wait
