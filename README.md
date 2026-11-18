# PROJECT APEX

An analysis-only futures trading intelligence workstation for an Apex Trader Funding
$50K End-of-Day (EOD) Evaluation traded manually on Tradovate.

> **Execution policy.** PROJECT APEX is analysis only: no broker connection, and it never places,
> cancels or modifies orders. It contains no live order-routing path. This is enforced by `apps/api/app/core/execution_guard.py` and by
> automated tests (`apps/api/tests/test_execution_safety.py`). Every order is placed manually by
> the trader. Risk figures are advisory and cannot guarantee protection against account failure.

## What it does

Three functions, three tabs at http://localhost:3000:

| Tab | What you get |
|---|---|
| **Market Analyzer** | ES, NQ and MNQ cards: price with age and feed status, BULLISH / BEARISH / NEUTRAL / WAIT bias, session, key structure, nearest liquidity, active setup, setup quality (a checklist score, not a probability) and news status. Click a card for: one-sentence bias, what's happening now, important levels (FVGs, order blocks, PDH/PDL, weekly/monthly, overnight, equal highs/lows with approaching / breached / swept-and-reclaimed / accepted states), potential setup with gross and net R:R, or the concrete reasons to wait; a clean chart with optional overlays; advanced 12-factor confluence and the 11 strategies grouped into 4 families. |
| **News** | ForexFactory USD calendar in Eastern time, re-evaluated every 60 s: HIGH IMPACT UPCOMING / NEWS WINDOW ACTIVE / POST-NEWS VOLATILITY / NO MAJOR SCHEDULED EVENT NEARBY / NEWS DATA STALE, countdown to the next high-impact event, forecast and previous as published, and the time of the last successful download. |
| **Trading Journal** | Record the trades you place yourself (scaled entries and partial exits supported), fees kept separate, net P&L, R, win rate, profit factor, expectancy, drawdown, streaks, calendar and by-strategy / instrument / hour reports, mistakes and discipline notes, screenshots, CSV import. |

Settings, health and the advanced research tools are behind the gear icon. Everything is computed
deterministically from stored closed candles; the optional AI copilot only explains those results.
Definitions, the LuxAlgo comparison and the look-ahead safeguards: [docs/smc-engine.md](docs/smc-engine.md).

## Status

Phases 1–9 are built and tested on simulated data. See the reports in `docs/phase-reports/`.

What still depends on you:

1. **Verify the Apex rules.** Every rule is seeded UNVERIFIED. Check each against the official Apex
   help centre on **Apex Account Health → Rule profile** ([register](docs/compliance/apex-rules.md)).
2. **Connect real market data.** Only the `simulated` provider has run. The Databento adapter needs
   an API key and a first live run ([status](docs/integrations/status.md)).
3. **Add an Anthropic API key** for the copilot. It has only been tested against mocks. Use
   **Settings → Connection tests** (free: lists models, uses no tokens) to check it.
4. **Allow network access** to `www.federalreserve.gov` and `www.eia.gov` for the news feeds
   (`APEX_NEWS_SOURCES='[]'` disables polling).

Pattern detectors can be checked against stored history with
`cd apps/api && uv run python -m app.cli pattern-stats --symbol ES --timeframe 5m --days 60`;
anything that has not shown out-of-sample value after costs is labelled RESEARCH ONLY.

Until real data is connected no strategy can be validated, so the ranked opportunity list stays empty.

## Run it on Windows

Install Docker Desktop, get the code, then double-click `windows\Install desktop shortcut.bat`.
From then on the **PROJECT APEX** desktop icon starts everything and opens http://localhost:3000.
See [windows/README.md](windows/README.md).

## Quick start (development)

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20.9+, PostgreSQL 16, Redis 7.

```bash
cp .env.example .env                         # set APEX_SECRET_KEY
createuser -P apex && createdb -O apex apex   # password: apex_dev_password (or edit APEX_DATABASE_URL)
npm install

# Demo with clearly labelled synthetic prices:
APEX_MARKET_DATA_PROVIDER=simulated scripts/dev.sh
```

Open http://localhost:3000 and create the administrator on first run (or
`cd apps/api && uv run python -m app.cli create-admin --email you@example.com`).

For a real deployment (HTTPS, backups, upgrades) see [docs/deployment/production.md](docs/deployment/production.md).

## Tests

```bash
cd apps/api && uv run ruff check . && uv run pytest     # needs PostgreSQL db `apex_test` + Redis
npm run lint:web && npm run typecheck -w web && npm run test:web
cd apps/web && npx playwright test                      # full stack, db `apex_e2e`
```

## Layout

```
apps/api/app/
  api/routes/     HTTP + WebSocket endpoints        core/       config, db, security, rate limit, guard
  marketdata/     calendar, validation, store, feed  adapters/   market data providers
  strategies/     strategy framework and A–H         research/   indicators, scanner, scoring, backtest
  risk/           Risk Guardian engine, rule profile paper/      isolated simulator
  ai/             copilot loop and read-only tools   services/   account, alerts, calendar, journal, ...
  workers/        ingestion, scanner, calendar sync, backtest queue, paper processing
apps/web/         Next.js 16 dashboard (15 pages), Playwright e2e in e2e/
packages/shared-types/   TypeScript contracts mirroring the API
infrastructure/   Dockerfiles, Caddyfile, Alembic migrations, monitoring notes
scripts/          dev.sh, backup-db.sh, e2e helpers
docs/             architecture, compliance, integrations, deployment, phase reports
```

## Documentation

- [Architecture](docs/architecture/overview.md)
- [Execution safety policy](docs/compliance/execution-safety.md)
- [Security review and controls](docs/compliance/security.md)
- [Apex rule verification register](docs/compliance/apex-rules.md)
- [Integration status and Tradovate research](docs/integrations/status.md)
- [Local deployment](docs/deployment/local.md) · [Production deployment](docs/deployment/production.md)
- Phase reports: [1](docs/phase-reports/phase-1.md) · [2](docs/phase-reports/phase-2.md) ·
  [3](docs/phase-reports/phase-3.md) · [4](docs/phase-reports/phase-4.md) · [5](docs/phase-reports/phase-5.md) ·
  [6](docs/phase-reports/phase-6.md) · [7](docs/phase-reports/phase-7.md)
