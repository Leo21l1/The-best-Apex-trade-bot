# PROJECT APEX

An analysis-only futures trading intelligence workstation for an Apex Trader Funding
$50K End-of-Day (EOD) Evaluation traded manually on Tradovate.

> **Execution policy.** PROJECT APEX never places, cancels or modifies live orders and contains no
> live order-routing path. This is enforced by `apps/api/app/core/execution_guard.py` and by
> automated tests (`apps/api/tests/test_execution_safety.py`). Every order is placed manually by
> the trader. Risk figures are advisory and cannot guarantee protection against account failure.

## What it does

| Area | What you get |
|---|---|
| Market data | Provider-independent adapters; every price labelled live / delayed / historical / simulated / unavailable with provider and timestamp; validation, duplicate protection, gap detection, freshness monitoring; candles on nine timeframes |
| Market Radar | Trend, RSI, ATR and relative volume per timeframe; regime; VWAP; prior-day, overnight and opening-range levels; watchlist |
| Strategies A–K | Opening range breakout, VWAP trend, liquidity sweep reversal, breakout-retest, momentum pullback, volatility expansion, mean reversion, multi-timeframe trend, plus SMC setups (sweep + structure shift, order-block retest, FVG retest): written specifications and mathematically defined, explainable rules |
| Smart Money Analysis | Causal detectors for swings, BOS/CHoCH/MSS, equal highs/lows, liquidity sweeps (candidate / rejection / continuation / invalidated), order blocks, FVG and inverse FVG with fill tracking, supply/demand, breakout-retest, divergence; drawn on the chart with a pattern inspector, liquidity map, multi-timeframe confluence (BUY BIAS / SHORT BIAS / WAIT / NO TRADE) and an all-instrument setup scan |
| News | Official Federal Reserve and EIA RSS feeds: publication vs detection time, rule-based impact (LOW–CRITICAL), dedupe, clustering, observed reaction, alerts, chart markers and a ticker. No breaking-news wire is connected (needs a licensed provider) |
| Opportunities | Explainable candidates with entry zone, stop, targets, costs and itemised setup score; ranked only when the strategy is validated, data is real, rules are verified and the risk check passes (zero is a valid result) |
| Apex Risk Guardian | Rule profile with per-rule verification records; EOD drawdown (not intraday trailing), daily loss, risk budget, contract exposure with micros; six-output trade checks |
| Account data | Manual snapshots and CSV import of end-of-day balances (no broker connection) |
| AI copilot | Claude with read-only tools; answers split into verified data / estimates / interpretation / missing information, with every tool call shown |
| Economic calendar | Official `.ics` import, manual entry, event windows, alerts and news exposure |
| Backtesting | Event-driven replay of the live strategy code; conservative fills; IS/OOS, walk-forward, Monte Carlo, sensitivity, multiple-testing accounting; reproducible, fingerprinted experiments |
| Paper trading | Isolated simulator with market/limit/stop/bracket orders, partial fills, slippage, commissions and Apex-style limits |
| Journal and analytics | Tradovate CSV import, tagging, planned vs actual, daily/weekly/monthly reports, mistake analysis, rule checks |
| Operations | Alerts with dedupe and cooldowns, session briefing, System Health, audit log, admin/viewer roles |

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
