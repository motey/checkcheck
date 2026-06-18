#!/usr/bin/env bash
# Set up the CheckCheck frontend development environment using Bun.
# Should be sourced (not executed) so a freshly installed `bun` is on PATH
# in your current shell afterwards.
#
# Usage: source ./build_frontend_dev_env.sh

# Detect if being sourced
(return 0 2>/dev/null) && SOURCED=1 || SOURCED=0

if [[ $SOURCED -eq 0 ]]; then
    echo "❌ Error: This script should be sourced, not executed."
    echo "   Otherwise a freshly installed 'bun' won't be on PATH in this shell."
    echo "   Usage: source $0"
    exit 1
fi

# NOTE: We deliberately do NOT use `set -e`/`set -o pipefail` here.
# This script is sourced, so those options apply to the user's interactive
# shell and any failing command would close their terminal. Instead each step
# checks its own status and returns on failure.

# === CONFIGURATION ===
FRONTEND_DIR="./CheckCheck/frontend"
BUN_INSTALL="${BUN_INSTALL:-$HOME/.bun}"


# === FUNCTIONS ===

install_bun() {
    # Make sure a previously installed bun is visible.
    export PATH="$BUN_INSTALL/bin:$PATH"
    hash -r 2>/dev/null

    if command -v bun &>/dev/null; then
        echo "bun already installed ($(bun --version))."
        return 0
    fi

    if ! command -v curl &>/dev/null; then
        echo "❌ bun not found and 'curl' is unavailable to install it."
        echo "   Install bun manually: https://bun.sh/docs/installation"
        return 1
    fi

    echo "bun not found. Installing via https://bun.sh/install ..."
    if ! curl -fsSL https://bun.sh/install | bash; then
        echo "❌ Failed to install bun."
        echo "   Install it manually: https://bun.sh/docs/installation"
        return 1
    fi

    # Refresh PATH/command cache so the freshly installed bun is found.
    export PATH="$BUN_INSTALL/bin:$PATH"
    hash -r 2>/dev/null

    if ! command -v bun &>/dev/null; then
        echo "❌ bun was installed but is not on PATH."
        echo "   Ensure '$BUN_INSTALL/bin' is on your PATH, then re-run."
        return 1
    fi

    echo "bun installed successfully ($(bun --version))."
}

install_deps() {
    if [[ ! -d "$FRONTEND_DIR" ]]; then
        echo "❌ Frontend directory not found: $FRONTEND_DIR"
        echo "   Run this from the repository root: source ./build_frontend_dev_env.sh"
        return 1
    fi
    echo "📦 Installing dependencies in $FRONTEND_DIR..."
    (cd "$FRONTEND_DIR" && bun install) || {
        echo "❌ 'bun install' failed in $FRONTEND_DIR."
        return 1
    }
}

prepare_nuxt() {
    echo "⚙️  Preparing Nuxt..."
    (cd "$FRONTEND_DIR" && bunx nuxi prepare) || {
        echo "❌ 'bunx nuxi prepare' failed in $FRONTEND_DIR."
        return 1
    }
}


# === MAIN ===

# Run steps in order, stopping on the first failure WITHOUT killing the shell.
_build_frontend_env_main() {
    install_bun     || return 1
    install_deps    || return 1
    prepare_nuxt    || return 1

    echo ""
    echo "✅ Setup complete. Frontend dependencies installed and Nuxt prepared."
    echo ""
    echo "You can now start the dev server with:"
    echo ""
    echo "    ./run_dev_frontend.sh"
    echo ""
}

if _build_frontend_env_main; then
    unset -f _build_frontend_env_main install_bun install_deps prepare_nuxt 2>/dev/null
    return 0 2>/dev/null
else
    echo ""
    echo "⚠️  Setup did not complete. Your shell is still open; fix the issue above and re-run:"
    echo "    source ./build_frontend_dev_env.sh"
    unset -f _build_frontend_env_main install_bun install_deps prepare_nuxt 2>/dev/null
    return 1 2>/dev/null
fi
