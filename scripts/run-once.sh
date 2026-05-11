#!/usr/bin/env bash
# Convenience wrapper for a single manual run on the VPS.
# Usage: ./scripts/run-once.sh [--dry-run]
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found. Copy .env.example to .env and fill in your keys." >&2
    exit 1
fi

exec docker compose run --rm podcaster "$@"
