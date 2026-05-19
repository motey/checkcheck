#!/bin/bash

# Define target directories
NUXT_DIR="./CheckCheck/frontend/.nuxt"
NODE_MODULES_DIR="./CheckCheck/frontend/node_modules"

# Check for the -r or --reset flag
if [[ "$1" == "-r" || "$1" == "--reset" ]]; then
  echo "🔄 Reset flag detected. Removing build and dependency directories..."

  # Remove directories if they exist
  rm -rf "$NUXT_DIR" "$NODE_MODULES_DIR"

  echo "✅ Reset complete."
fi

# Continue with normal setup and dev run
(
  cd CheckCheck/frontend || exit 1
  echo "📦 Installing dependencies..."
  bun install

  echo "⚙️ Preparing Nuxt..."
  bunx nuxi prepare

  echo "🚀 Starting development server..."
  bun --bun run dev
)