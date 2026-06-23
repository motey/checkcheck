
# Run the MedLog Backend Server with a OIDC Mockup server
# This mainly intended for Frontend OIDC Development

#exit on error
set -e

# Make every `pdm run` below use the project's own venv ($BACKEND_DIR/.venv)
# rather than reusing whatever virtualenv is active in the caller's shell.
# Otherwise pdm may run against an interpreter that lacks the installed deps
# (e.g. oidc_provider_mock), which surfaces as a ModuleNotFoundError on boot.
export PDM_IGNORE_ACTIVE_VENV=1

# Store process IDs
PIDS=()

#PYTHON_BIN=$(which python)
#echo "Python: $PYTHON_BIN"
PYTHON_BIN="pdm run"

# Function to handle script termination
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
    exit 0
}

kill_processes_by_path() {
    if [[ -z "$1" ]]; then
        echo "Usage: kill_processes_by_path <search_string>"
        return 1
    fi

    local search_string="$1"

    # Find processes matching the search string and extract their PIDs
    local pids=$(ps axo pid,command | grep "$search_string" | grep -v grep | awk '{print $1}')

    if [[ -z "$pids" ]]; then
        echo "No matching processes found."
        return 0
    fi

    # Kill each process
    echo "Killing processes: $pids"
    echo "$pids" | xargs kill -9
}


# Trap SIGINT (Ctrl+C) and SIGTERM
trap cleanup SIGINT SIGTERM

# config
export SQL_DATABASE_URL="sqlite+aiosqlite:///../../muchdata.sqlite"
export FRONTEND_FILES_DIR="../frontend/.output/public"
## config - oidc
export AUTH_OIDC_TOKEN_STORAGE_SECRET=qi3we7gaukb
PROVIDER_DISPLAY_NAME="LocalDevLogin"
CONFIGURATION_ENDPOINT=http://localhost:8884/.well-known/openid-configuration
CLIENT_ID=devdummyid1345
CLIENT_SECRET=devdummysecrect1345
USER_NAME_ATTRIBUTE=name
USER_DISPLAY_NAME_ATTRIBUTE=given_name
USER_MAIL_ATTRIBUTE=email
USER_GROUPS_ATTRIBUTE=groups
TOKEN_STORAGE_SECRET=asuizfqwhj

# using somewhat akward EOF/heredoc for dogding even more akward escaping
export AUTH_OIDC_PROVIDERS=$(cat <<EOF
[{"PROVIDER_DISPLAY_NAME": "${PROVIDER_DISPLAY_NAME}","CONFIGURATION_ENDPOINT":"${CONFIGURATION_ENDPOINT}","CLIENT_ID":"${CLIENT_ID}","CLIENT_SECRET":"${CLIENT_SECRET}","USER_NAME_ATTRIBUTE":"${USER_NAME_ATTRIBUTE}","USER_DISPLAY_NAME_ATTRIBUTE":"${USER_DISPLAY_NAME_ATTRIBUTE}","USER_MAIL_ATTRIBUTE":"${USER_MAIL_ATTRIBUTE}","USER_GROUPS_ATTRIBUTE": "${USER_GROUPS_ATTRIBUTE}"}]
EOF
)
echo "Kill zombie processes..."
kill_processes_by_path oidc_provider_mock_server.py
kill_processes_by_path checkcheckserver/main.py

echo "Start dummy OIDC Provider"
# boot OIDC mockup authenticaion server
# setsid puts the job in its own process group so cleanup() can signal the
# whole tree, and so a terminal Ctrl+C does not hit it directly (only the
# script's trap does).
setsid bash -c 'cd ./CheckCheck/backend/dev_oidc_server && pdm run oidc_provider_mock_server.py' &
mock_server_PID=$!

# Wait up to 3 seconds for oidc mockup server to boot successfull
for i in {1..3}; do
    if ! kill -0 $mock_server_PID 2>/dev/null; then
        # Process has exited, check its exit code
        echo "OIDC mockup server failed to start."
        exit 1
    fi
    sleep 1
done
echo "OIDC mockup server seemed to have booted."
PIDS+=($mock_server_PID)  # Store PID
# Boot CheckCheck Backend

setsid bash -c "cd CheckCheck/backend && pdm run ./checkcheckserver/main.py $1" &
PIDS+=($!)  # Store PID of last background process (== its process-group leader)
wait
