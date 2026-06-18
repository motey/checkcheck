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

# NOTE: We deliberately do NOT use `set -e`/`set -o pipefail` here.
# This script is sourced, so those options apply to the user's interactive
# shell and any failing command would close their terminal. Instead each step
# checks its own status and returns on failure.

# === CONFIGURATION ===
BACKEND_DIR="./CheckCheck/backend"
VENV_DIR="$BACKEND_DIR/.venv"
PYPROJECT="$BACKEND_DIR/pyproject.toml"

# Derive the target Python version from pyproject.toml's `requires-python`
# (e.g. `requires-python = "==3.11.*"` -> "3.11"). Falls back to "unknown".
read_python_version() {
    local line spec
    [[ -f "$PYPROJECT" ]] || { echo "unknown"; return; }
    line=$(grep -E '^[[:space:]]*requires-python[[:space:]]*=' "$PYPROJECT" | head -n1)
    # Strip everything but the version-ish token: digits and dots.
    spec=$(echo "$line" | grep -oE '[0-9]+(\.[0-9]+)*' | head -n1)
    echo "${spec:-unknown}"
}

PYTHON_VERSION=$(read_python_version)


# === FUNCTIONS ===

# Find a usable pip invocation, printing it on stdout. Returns non-zero if none.
find_pip() {
    if command -v python3 &>/dev/null && python3 -m pip --version &>/dev/null; then
        echo "python3 -m pip"
    elif command -v python &>/dev/null && python -m pip --version &>/dev/null; then
        echo "python -m pip"
    elif command -v pip3 &>/dev/null; then
        echo "pip3"
    elif command -v pip &>/dev/null; then
        echo "pip"
    else
        return 1
    fi
}

install_pdm() {
    # Make sure a previously --user-installed pdm is visible.
    export PATH="$HOME/.local/bin:$PATH"
    hash -r 2>/dev/null

    if command -v pdm &>/dev/null; then
        echo "pdm already installed ($(pdm --version))."
        return 0
    fi

    local pip_cmd
    if ! pip_cmd=$(find_pip); then
        echo "❌ pdm not found and no pip available to install it."
        echo "   Install pdm manually (e.g. 'pipx install pdm') and re-run."
        echo "   See https://pdm-project.org/en/latest/#recommended-installation-method for installation advice."
        return 1
    fi

    echo "pdm not found. Installing via: $pip_cmd install --user pdm"
    if ! $pip_cmd install --user pdm; then
        echo "❌ Failed to install pdm via pip."
        echo "   Try manually: $pip_cmd install --user pdm   (or 'pipx install pdm')"
        return 1
    fi

    # Refresh PATH/command cache so the freshly installed pdm is found.
    export PATH="$HOME/.local/bin:$PATH"
    hash -r 2>/dev/null

    if ! command -v pdm &>/dev/null; then
        echo "❌ pdm was installed but is not on PATH."
        echo "   Ensure '$HOME/.local/bin' is on your PATH, then re-run."
        return 1
    fi

    echo "pdm installed successfully ($(pdm --version))."
}

install_deps() {
    if [[ ! -d "$BACKEND_DIR" ]]; then
        echo "❌ Backend directory not found: $BACKEND_DIR"
        echo "   Run this from the repository root: source ./build_server_dev_env.sh"
        return 1
    fi
    echo "Installing/updating dependencies in $BACKEND_DIR..."
    (cd "$BACKEND_DIR" && pdm install --dev) || {
        echo "❌ 'pdm install --dev' failed in $BACKEND_DIR."
        return 1
    }
}

activate_env() {
    if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
        echo "❌ Virtual environment not found at $VENV_DIR/bin/activate"
        echo "   Dependency install may have failed; see messages above."
        return 1
    fi
    echo "🏎️  Activating virtual environment at $VENV_DIR"
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
}


# === MAIN ===

# Run steps in order, stopping on the first failure WITHOUT killing the shell.
_build_dev_env_main() {
    install_pdm     || return 1
    install_deps    || return 1
    activate_env    || return 1

    echo ""
    echo "✅ Setup complete. Virtual environment is active (Python $PYTHON_VERSION)"
    echo ""
    echo "You can now start the server with:"
    echo ""
    echo "    ./run_dev_backend_server_with_oidc.sh"
    echo "    ./run_dev_backend_server_with_oidc_on_postgres.sh"
    echo ""
}

if _build_dev_env_main; then
    unset -f _build_dev_env_main read_python_version find_pip install_pdm install_deps activate_env 2>/dev/null
    return 0 2>/dev/null
else
    echo ""
    echo "⚠️  Setup did not complete. Your shell is still open; fix the issue above and re-run:"
    echo "    source ./build_server_dev_env.sh"
    unset -f _build_dev_env_main read_python_version find_pip install_pdm install_deps activate_env 2>/dev/null
    return 1 2>/dev/null
fi
