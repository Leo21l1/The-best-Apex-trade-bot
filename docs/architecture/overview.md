# Architecture

## Components

```
Browser ──HTTPS──▶ Next.js (web, :3000) ──/api/* rewrite──▶ FastAPI (api, :8000) ──▶ PostgreSQL
   │                                                         │   ▲
   └──────────── WebSocket /api/ws (cookie auth) ────────────┘   └── Redis ◀── worker
```

- **web** — Next.js App Router. Pages are client components fed by same-origin `/api/*` calls
  (proxied by Next rewrites, so the browser never learns internal hostnames) and a WebSocket for
  push updates. When the socket drops, the client falls back to 10-second polling and reconnects
  with capped exponential backoff.
- **api** — FastAPI. Deterministic logic only: numerical risk decisions will never be produced by an
  LLM. The AI copilot (Phase 5) will call controlled, read-only tools.
- **worker** — separate process for scheduled/background jobs (market-data ingestion, scanning,
  alerts in later phases). Publishes a Redis heartbeat; System Health marks it degraded when stale.
- **PostgreSQL** — system of record (users, audit log; later candles, snapshots, experiments).
  Schema managed by Alembic in `infrastructure/migrations`.
- **Redis** — heartbeats, rate-limit counters, and (Phase 2+) pub/sub fan-out of market events.

## Key decisions

| Decision | Rationale |
|---|---|
| Single execution mode `ANALYSIS_ONLY`, not configurable | Removes any switch that could enable live trading. |
| httpOnly + SameSite=Strict session cookie, CSRF header on unsafe methods | Tokens never reachable from JS; no credentials in frontend code. |
| Token version on the user row | Logout/password change revokes every outstanding token server-side. |
| Honest component states (`not_configured`) | Integrations that are not built are shown as such; nothing is faked. |
| `DataQuality` type (`live/delayed/historical/simulated/unavailable`) shared with the UI | Every displayed price must carry provenance. |
| Alembic outside the app package | Keeps migrations infrastructure-owned, matching the requested layout. |
| npm workspaces (`apps/web`, `packages/shared-types`) | Shared TypeScript contracts without a publish step. |

## Security controls (Phase 1)

- Argon2id password hashing; generic login errors; timing-equalised unknown-user path.
- Account lockout after 5 failures (15 minutes); per-IP rate limits on auth and on the whole API
  (Redis-backed, in-process fallback if Redis is down).
- Bootstrap endpoint usable only while no users exist; requires `APEX_BOOTSTRAP_TOKEN` when set and
  always in production.
- Production settings refuse to start with the development secret, without a bootstrap token, or
  without secure cookies.
- Security headers on API and web (CSP, `X-Frame-Options: DENY`, `nosniff`, no referrer).
- WebSocket requires a valid session and an allowed `Origin`.
- Validation errors never echo submitted values; audit log scrubs sensitive keys.
- Structured JSON logs with request IDs; secrets redacted.
