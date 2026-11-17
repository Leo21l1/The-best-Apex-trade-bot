#!/usr/bin/env bash
# Starts migrations, a clean e2e database, the worker and the API for Playwright.
set -euo pipefail
cd "$(dirname "$0")/../apps/api"
PORT="${1:-8010}"
uv run alembic upgrade head
uv run python -m app.cli reset-db --yes
uv run python -m app.workers.main &
WORKER=$!
trap 'kill $WORKER 2>/dev/null || true' EXIT INT TERM
uv run uvicorn app.main:app --port "$PORT"
