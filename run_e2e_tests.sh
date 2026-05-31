#!/usr/bin/env bash
# Run the Playwright E2E test suite.
#
# Prerequisites (one-time):
#   source build_server_dev_env.sh   # activates the backend Python venv
#   cd CheckCheck/frontend && bun install && bunx playwright install chromium
#
# Usage:
#   ./run_e2e_tests.sh              # headless, all tests
#   ./run_e2e_tests.sh --headed     # show the browser window
#   ./run_e2e_tests.sh --ui         # open Playwright's interactive UI
#   ./run_e2e_tests.sh auth         # run only tests matching "auth"

set -euo pipefail

cd "$(dirname "$0")/CheckCheck/frontend"
exec bun run test:e2e "$@"
