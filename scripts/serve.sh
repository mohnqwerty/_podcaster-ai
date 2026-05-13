#!/usr/bin/env bash
# Run the dashboard locally (without docker). Reads .env from the repo root.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found. Copy .env.example to .env first." >&2
    exit 1
fi

HOST="${DASHBOARD_HOST:-127.0.0.1}"
PORT="${DASHBOARD_PORT:-8000}"

exec python -m uvicorn podcaster_ai.web.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --proxy-headers \
    --forwarded-allow-ips='*'
