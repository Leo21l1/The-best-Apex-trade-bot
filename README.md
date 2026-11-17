# PROJECT APEX

An analysis-only futures trading intelligence workstation for an Apex Trader Funding
$50K EOD Evaluation traded manually on Tradovate.

> **Execution policy.** PROJECT APEX never places, cancels or modifies live orders and contains no
> live order-routing path. That is enforced by `apps/api/app/core/execution_guard.py` and by
> automated tests (`apps/api/tests/test_execution_safety.py`). Every order is placed manually by
> the trader. Risk figures are advisory and cannot guarantee protection against account failure.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Monorepo, API + web foundations, database & migrations, auth, health checks, dashboard shell, tests, CI | **Done** (see [phase report](docs/phase-reports/phase-1.md)) |
| 2 | Market-data adapters, historical storage, streaming, charts, data-quality monitoring | **Done** with simulated data; real-data adapter unverified (see [phase report](docs/phase-reports/phase-2.md)) |
| 3 | Instrument specs, strategies A–H, scanner, opportunity scoring, alerts | **Done** ([report](docs/phase-reports/phase-3.md)) |
| 4 | Apex rule profile with verification workflow, risk engine, account snapshots / CSV import | **Done**; rules still need verifying by you ([report](docs/phase-reports/phase-4.md)) |
| 5 | Claude copilot, economic calendar, briefings | Next |
| 6 | Backtesting, paper-trading simulator, journal, walk-forward | Planned |
| 7 | Hardening, security review, performance, deployment | Planned |

The Apex rule values in this repo are **provisional and unverified** until Phase 4 checks each one
against official Apex documentation. No price is shown unless a market-data provider is configured
(`APEX_MARKET_DATA_PROVIDER`); the built-in `simulated` provider produces synthetic prices that are
labelled SIMULATED everywhere and must not be used for trading.

## Layout

```
apps/
  api/            FastAPI service (Python 3.13, SQLAlchemy 2 async, Pydantic 2)
    app/
      api/        HTTP + WebSocket routes        core/      config, db, security, logging, guard
      models/     ORM models                     schemas/   Pydantic I/O models
      services/   health, audit                  workers/   background worker process
      adapters/ strategies/ risk/ research/ ai/   (filled in by later phases)
    tests/        pytest suite (real PostgreSQL + Redis)
  web/            Next.js 16 + React 19 + Tailwind 4 dashboard
    app/          routes: (auth)/login and 14 (dashboard) pages
    components/   shell (sidebar, top bar, palette) and UI primitives
    features/     page-level feature modules
    hooks/ lib/   session, realtime status, API client, formatting, hotkeys
    e2e/          Playwright end-to-end tests
packages/shared-types/   TypeScript contracts mirroring the API schemas
infrastructure/
  docker/         Dockerfiles             migrations/   Alembic migrations
  monitoring/     health/logging notes
scripts/          dev.sh, e2e-api.sh, backup-db.sh
docs/             architecture, compliance, integrations, deployment, phase reports
```

## Running locally

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20.9+, PostgreSQL 16, Redis 7.

```bash
cp .env.example .env                     # then set APEX_SECRET_KEY
# create the database once
createuser -P apex   # password: apex_dev_password (or change APEX_DATABASE_URL)
createdb -O apex apex

npm install                              # web workspace
scripts/dev.sh                           # migrations + worker + API :8000 + web :3000
```

Open http://localhost:3000. On first run the sign-in page offers to create the administrator
account (permanently disabled once a user exists). Alternatively:

```bash
cd apps/api && uv run python -m app.cli create-admin --email you@example.com
```

With Docker: `cp .env.example .env && docker compose up --build`.

## Tests

```bash
cd apps/api && uv run pytest              # API: needs PostgreSQL db `apex_test` + Redis
cd apps/api && uv run ruff check .
npm run lint:web && npm run typecheck -w web && npm run test:web
cd apps/web && npx playwright test        # full-stack e2e, uses db `apex_e2e`
```

## Documentation

- [Architecture](docs/architecture/overview.md)
- [Execution safety policy](docs/compliance/execution-safety.md)
- [Apex rule verification](docs/compliance/apex-rules.md)
- [Tradovate / market data integration status](docs/integrations/status.md)
- [Deployment](docs/deployment/local.md)
