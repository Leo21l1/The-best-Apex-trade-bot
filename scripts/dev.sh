#!/usr/bin/env bash
# Run the full stack locally without Docker (needs PostgreSQL + Redis running).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/apps/api"
uv sync
uv run alembic upgrade head
uv run python -m app.workers.main &
WORKER_PID=$!
uv run uvicorn app.main:app --reload --port 8000 &
API_PID=$!
trap 'kill "$WORKER_PID" "$API_PID" 2>/dev/null || true' EXIT INT TERM
cd "$ROOT"
npm run dev:web
