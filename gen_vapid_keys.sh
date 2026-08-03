#!/usr/bin/env bash
# Generate a VAPID key pair for Web Push notifications.
#
# Prints VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY lines ready to paste into
# config.yml or the environment. Run once per instance; see the docstring in
# scripts/gen_vapid_keys.py for why the key pair should not be rotated
# casually.
#
# Usage:
#     ./gen_vapid_keys.sh
set -euo pipefail
cd "$(dirname "$0")"

# `cryptography` lives in the pdm-managed backend venv (the one that also runs
# the dev server). Fall back to whatever python is on PATH if that venv is
# missing.
PYTHON="CheckCheck/backend/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    PYTHON="python"
fi

exec "$PYTHON" scripts/gen_vapid_keys.py
