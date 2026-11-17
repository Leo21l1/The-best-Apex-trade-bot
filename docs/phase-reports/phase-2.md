# Phase 2 report — market data

Date: 2026-11-17

## 1. Completed features

- **Provider-independent adapter interface** (`app/adapters/marketdata/base.py`): fetch closed
  1-minute bars, stream bar updates. Adapters are read-only by contract.
- **Providers**
  - `simulated`: deterministic synthetic prices, generated only during exchange sessions, on the
    tick grid, tagged SIMULATED. No network calls.
  - `databento`: historical 1-minute OHLCV from Databento's HTTP API, with client-side throttling
    and 429 retry. **Unverified against the live service.** Bars are labelled HISTORICAL.
  - `none` (default): no prices anywhere.
- **Data-quality classes** on every price-bearing response and WebSocket frame: live, delayed,
  historical, simulated, unavailable, plus provider, server timestamp, last-bar time and a stale
  flag.
- **Session calendar**: CME Globex weekly schedule (Sunday 18:00 ET to Friday 17:00 ET, daily
  17:00–18:00 break), DST-aware, trading-day roll at 18:00 ET.
- **Validation**: OHLC consistency, positive finite prices, tick-grid alignment per instrument,
  minute alignment, future timestamps, bars outside sessions, unknown symbols.
- **Duplicate protection**: in-stream filter for identical events, and an idempotent database
  upsert keyed on provider, symbol and bar time (corrections overwrite, never duplicate).
- **Missing-bar detection**: gap report per symbol against the session calendar.
- **Historical storage**: `candles` table (migration 0002). Only 1-minute bars are stored; 3m,
  5m, 15m, 30m, 1h, 4h, daily and weekly bars are aggregated in SQL, anchored to the 18:00 ET
  session open and the trading day.
- **Ingestion service** (in the worker): backfill that resumes from the last stored bar,
  streaming, reconnection with capped exponential backoff, a slow-retry state for rejected
  credentials, and a feed-status document for monitoring.
- **Freshness monitoring**: a symbol is stale when the market is open and no bar has arrived
  within the configured window; stored bars are flagged stale if the worker stops reporting.
- **API**: `/api/market/instruments`, `/candles`, `/snapshot`, `/status`, `/gaps`.
- **WebSocket**: clients subscribe to `candles:<SYMBOL>`; the API relays bar updates from Redis.
- **Dashboard**
  - Advanced Charts: candlesticks and volume, nine timeframes, instrument selector, OHLC readout
    on hover, live updates, "load older bars", New York time axis, data-quality panel.
  - Overview: latest stored close per instrument with provider, bar time and stale marker.
  - System Health: feed state, reconnects, dropped duplicates and invalid bars, per-symbol
    freshness.
  - Top bar data badge reflects the real data class.
- **Research** on Tradovate data access: see `docs/integrations/status.md`.

## 2. Files

Backend: `app/marketdata/*`, `app/adapters/marketdata/*`, `app/api/routes/market.py`,
`app/api/routes/ws.py`, `app/models/candle.py`, `app/schemas/market.py`, `app/workers/main.py`,
`app/services/health.py`, `app/core/config.py`, migration `0002_candles`.
Frontend: `features/charts/*`, `hooks/useRealtime.tsx`, `hooks/usePolling.ts`, `lib/candles.ts`,
updates to Overview, System Health, top bar, shared types.
Tests: `tests/test_md_*.py`, `lib/__tests__/candles.test.ts`, `e2e/02-charts.spec.ts`.

## 3–4. Tests executed and results (actual runs in this environment)

| Suite | Result |
|---|---|
| API lint and format (ruff) | pass |
| API tests (real PostgreSQL + Redis) | **100 passed** |
| Web lint, typecheck, production build | pass |
| Web unit tests | **27 passed** |
| End-to-end (real API, worker, web; simulated feed) | **7 passed**, including live bar updates over the WebSocket (run while the futures market was open) |
| Dependency audits | 0 known vulnerabilities |

New API coverage: session boundaries in EST and EDT, trading-day roll, 12 invalid-bar cases, tick
sizes, duplicate filter, gap detection, simulated provider determinism and validity for all 12
symbols, Databento parsing / auth / HTTP errors / rate-limit retry (mocked transport), idempotent
upsert and corrections, hand-computed 3m/5m/1h/4h/daily/weekly aggregation, backfill resume,
reconnect after disconnects, auth-error state, API provenance and input validation, stale marking
when the worker stops, gap report, health component states, WebSocket subscription filtering.

## 5. Known limitations

- **No real market data has been received.** The Databento adapter was written from public
  documentation and tested only against mocked responses. It needs an API key and a first live
  run. The build environment also blocks that host.
- **No real-time adapter yet.** Databento data is from the historical API and labelled
  HISTORICAL. A licensed streaming adapter is still to be written.
- **Exchange holidays and early closes are not modelled.** Holiday closures appear as gap
  candidates, and the simulated provider generates bars on holidays.
- **Contract expiration and rollover are not handled explicitly.** The Databento adapter requests
  continuous front-month symbols; roll rules and back-adjustment are deferred.
- **No tick data or bid/ask quotes.** The interface has capability flags for them, nothing more.
- **VWAP, opening range and price levels are not drawn yet.** They come with the scanner in Phase 3.
- **Crude oil negative prices** (as in April 2020) are rejected by validation and logged.
- Tradovate findings come from forums and vendor blogs, not official policy pages.
- One live-update end-to-end test skips itself when the futures market is closed.

## 6. Required credentials

`APEX_DATABENTO_API_KEY` (or another licensed vendor's key plus an adapter) to see real prices.

## 7. Commands

```bash
# development with synthetic, clearly labelled data
APEX_MARKET_DATA_PROVIDER=simulated scripts/dev.sh

cd apps/api && uv run pytest
npm run test:web
cd apps/web && npx playwright test
```

## 8. Next phase (3 — strategies and scanner)

Full instrument specifications (tick value, point value), mathematically defined indicators
(VWAP, opening range, prior-day and overnight levels, structure), strategy modules A–H, the
multi-timeframe scanner, explainable opportunity scoring (allowed to return zero), and alerts with
deduplication and cooldowns.
