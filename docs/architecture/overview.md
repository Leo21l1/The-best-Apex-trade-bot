# Architecture

## Components

```
Browser ──HTTPS──▶ reverse proxy ──/api/*──▶ FastAPI (api) ──▶ PostgreSQL
   │                    │                       │   ▲
   │                    └──────other──▶ Next.js │   │
   └────── WebSocket /api/ws (cookie auth) ─────┘   └── Redis ◀── worker
                                                          ▲
                                   market data provider ──┘ (read-only)
                                   Anthropic API ◀── api (copilot only, outbound)
```

- **web** – Next.js App Router dashboard. Same-origin `/api/*` calls (proxied) and one shared
  WebSocket with reconnect, backoff and topic resubscription; polling fallback for status.
- **api** – FastAPI. Deterministic analytics and risk; the copilot calls Claude with read-only tools.
- **worker** – one process with independent loops: market-data ingestion (backfill, stream,
  reconnect), scanner (every 20 s), economic-calendar sync, backtest queue, paper-account processing,
  and a heartbeat.
- **PostgreSQL** – system of record: users, audit log, candles, signals, alerts, rules, account
  snapshots, calendar events, copilot exchanges, backtest runs, paper accounts, journal trades.
  Schema managed by Alembic in `infrastructure/migrations`.
- **Redis** – feed status, scan cache, pub/sub for candles and alerts, alert cooldowns, rate limits.

## Data flow

1. The ingestion loop validates and stores closed 1-minute bars and publishes updates.
2. The scanner aggregates bars (3m–1w), runs strategies A–H on closed bars, stores new signals
   once, advances their lifecycle and raises alerts.
3. The API serves candles, radar, opportunities (ranked at request time from validation, data class,
   rule verification, risk and event windows), the risk engine, the copilot and the lab.
4. Backtests replay the same strategy code on stored bars; their results decide validation.

## Key decisions

| Decision | Rationale |
|---|---|
| Single execution mode `ANALYSIS_ONLY`, not configurable | Removes any switch that could enable live trading |
| Every price carries provider, timestamp and data class | Nothing fabricated or mislabelled reaches the screen |
| Rule profile in the database with per-rule verification | Values change over time; a human must confirm each against the official source |
| Risk numbers from a pure Decimal engine | Deterministic, testable; the AI cannot produce them |
| Backtester reuses live strategy code | Backtest and live signals follow the same rules |
| Validation requires real (non-simulated) historical data | Synthetic data can never promote a strategy |
| httpOnly SameSite=Strict cookie + CSRF header; token version | No tokens in JavaScript; server-side revocation |
| Honest component states (`not_configured`, `unavailable`) | Missing integrations are visible, never faked |
| Messages API with a self-hosted tool loop for the copilot | Tools read the local database; account data stays out of hosted session storage |

## Security

See [security review and controls](../compliance/security.md).
