#!/usr/bin/env bash
# Set up the CheckCheck backend development environment using PDM.
# Must be sourced (not executed) so the venv activation persists in your shell.
#
# Usage: source ./build_server_dev_env.sh

# Detect if being sourced
(return 0 2>/dev/null) && SOURCED=1 || SOURCED=0

if [[ $SOURCED -eq 0 ]]; then
    echo "❌ Error: This script must be sourced, not executed. Otherwise the virtual env cannot be activated."
    echo "Usage: source $0"
    exit 1
fi

ORIGINAL_OPTS=$(set +o)
set -eo pipefail

# === CONFIGURATION ===
PYTHON_VERSION="3.11"
BACKEND_DIR="./CheckCheck/backend"
VENV_DIR="$BACKEND_DIR/.venv"


# === FUNCTIONS ===

install_pdm() {
    if ! command -v pdm &>/dev/null; then
        echo "pdm not found. Installing via pip..."
        pip install --user pdm
        export PATH="$HOME/.local/bin:$PATH"
        echo "pdm installed successfully."
    else
        echo "pdm already installed ($(pdm --version))."
    fi
}

install_deps() {
    echo "Installing/updating dependencies in $BACKEND_DIR..."
    (cd "$BACKEND_DIR" && pdm install --dev)
}

activate_env() {
    echo "🏎️  Activating virtual environment at $VENV_DIR"
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
}


# === MAIN ===

install_pdm
install_deps
activate_env

echo ""
echo "✅ Setup complete. Virtual environment is active (Python $PYTHON_VERSION)"
echo ""
echo "You can now start the server with:"
echo ""
echo "    ./run_dev_backend_server_with_oidc.sh"
echo "    ./run_dev_backend_server_with_oidc_on_postgres.sh"
echo ""

eval "$ORIGINAL_OPTS"
