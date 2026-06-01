#!/usr/bin/env bash
# Run the Playwright E2E test suite.
#
# Prerequisites (one-time):
#   source build_server_dev_env.sh   # activates the backend Python venv
#   cd CheckCheck/frontend && bun install && bunx playwright install chromium
#
# Usage:
#   ./run_e2e_tests.sh                     # headless, all tests
#   ./run_e2e_tests.sh --headed            # show the browser window
#   ./run_e2e_tests.sh --ui               # Playwright interactive UI (separate Chromium)
#   ./run_e2e_tests.sh --ui --ui-port 9323 # UI served in your browser at localhost:9323
#   ./run_e2e_tests.sh --pick             # pick a test interactively → runs with --debug
#   ./run_e2e_tests.sh --pick --headed    # pick a test → runs headed (no step-through)
#   ./run_e2e_tests.sh auth               # run only tests matching "auth"
#
# UI mode tip: run headless once first (builds frontend), then --ui skips the
# 2-3 min nuxt generate.  Force a frontend rebuild: FORCE_BUILD=1 ./run_e2e_tests.sh --ui
#
# Tip: install fzf for a fuzzy-searchable picker (apt install fzf / brew install fzf)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/CheckCheck/frontend"

#######################################
# --pick: interactive test selector
#######################################
if [[ "${1:-}" == "--pick" ]]; then
  shift

  # Remaining args are forwarded to playwright (e.g. --headed overrides --debug)
  RUN_MODE="--debug"
  if [[ "${1:-}" == "--headed" ]]; then
    RUN_MODE="--headed"
    shift
  fi

  echo "▶  Fetching test list…"
  RAW=$(cd "$FRONTEND_DIR" && bunx playwright test --list 2>&1 | grep "›") || true

  if [[ -z "$RAW" ]]; then
    echo "❌  No tests found. Is the Python venv active?"
    exit 1
  fi

  if command -v fzf &>/dev/null; then
    SELECTED=$(echo "$RAW" | fzf \
      --prompt="Pick a test (type to filter): " \
      --height=60% \
      --layout=reverse \
      --border \
      --info=inline) || true
  else
    echo ""
    echo "  (install fzf for fuzzy search: apt install fzf)"
    echo ""
    IFS=$'\n' mapfile -t TESTS <<< "$RAW"
    for i in "${!TESTS[@]}"; do
      printf "  %3d)  %s\n" $((i + 1)) "${TESTS[$i]}"
    done
    echo ""
    read -rp "Enter test number (Enter to cancel): " NUM
    if [[ -z "$NUM" ]]; then
      echo "Cancelled."
      exit 0
    fi
    SELECTED="${TESTS[$((NUM - 1))]}"
  fi

  if [[ -z "$SELECTED" ]]; then
    echo "Cancelled."
    exit 0
  fi

  # Extract the test title: everything after the last ›
  TEST_NAME=$(echo "$SELECTED" | sed 's/.*›[[:space:]]*//')
  # Escape regex metacharacters so --grep treats it as a literal string
  TEST_GREP=$(printf '%s' "$TEST_NAME" | sed 's/[.^$*+?()[\]{}|\\]/\\&/g')

  echo ""
  echo "▶  $RUN_MODE  →  $TEST_NAME"
  echo ""

  cd "$FRONTEND_DIR"
  bun run test:e2e "$RUN_MODE" --grep "$TEST_GREP" "$@"
  e2e_exit=$?
  echo ""
  echo "To open the HTML report, run:"
  echo "  cd CheckCheck/frontend && bunx playwright show-report"
  exit $e2e_exit
fi

#######################################
# Normal run
#######################################
cd "$FRONTEND_DIR"
bun run test:e2e "$@"
e2e_exit=$?
echo ""
echo "To open the HTML report, run:"
echo "  cd CheckCheck/frontend && bunx playwright show-report"
exit $e2e_exit
