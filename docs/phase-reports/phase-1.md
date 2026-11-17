# Phase 1 report — foundations

Date: 2026-11-17

## 1. Completed features

- **Environment**: Python 3.13 + uv, Node 22, PostgreSQL 16, Redis 7 verified. Docker CLI exists but
  there is no daemon, so images were not built (Compose file syntax-validated).
- **Monorepo** with npm workspaces (`apps/web`, `packages/shared-types`) and a uv-managed API.
- **API (FastAPI)**: environment-based settings with production guards; async SQLAlchemy;
  structured JSON logging with request IDs; security headers; global and auth rate limiting;
  safe 500 and 422 handlers (no echo of submitted values).
- **Database**: `users` and `audit_logs` tables; Alembic async migrations in
  `infrastructure/migrations` (upgrade → downgrade → upgrade verified).
- **Authentication**: first-admin bootstrap (one-time, token-gated in production), login with
  Argon2id, lockout, httpOnly SameSite=Strict cookie, CSRF header, server-side token revocation on
  logout, audit trail, CLI `create-admin`.
- **Health**: `/api/health/live`, `/api/health/ready` (503 when DB/Redis down),
  authenticated `/api/system/status` with honest `not_configured` states for market data, Apex
  rule profile, Tradovate and AI.
- **Worker**: separate process with Redis heartbeat; staleness detection.
- **WebSocket** `/api/ws`: session + Origin checks, `system.status` push every 5 s, ping/pong,
  sequence numbers. The client reconnects with backoff and falls back to polling.
- **Execution-safety guard**: one-member `ExecutionMode`, `forbid_live_execution()`, and a code-scan
  test for live order endpoints and UI-automation libraries.
- **Dashboard (Next.js 16 / React 19 / Tailwind 4)**: dark charcoal workstation shell, all 14
  pages routed, sidebar and mobile drawer, top bar (ANALYSIS ONLY badge, data-quality badge,
  rules-unverified warning, realtime indicator, New York clock), command palette (Ctrl/Cmd-K, `/`),
  `g <key>` navigation chords, `?` shortcut help. Overview, System Health and Settings are working
  pages; the other 11 pages show their scope and dependencies with no sample numbers.
- **CI** (GitHub Actions): API lint, format, tests and pip-audit; web lint, typecheck, unit tests,
  build and npm audit; Playwright e2e against Postgres and Redis service containers.

## 2. Files

See `git show --stat` for the Phase 1 commit. Main areas: `apps/api/**`, `apps/web/**`,
`packages/shared-types/**`, `infrastructure/**`, `scripts/**`, `docs/**`, `.github/workflows/ci.yml`,
`docker-compose.yml`, `.env.example`.

## 3–4. Tests executed and results (actual runs in this environment)

| Suite | Command | Result |
|---|---|---|
| API lint and format | `uv run ruff check . && uv run ruff format --check .` | pass |
| API tests (real PostgreSQL + Redis) | `uv run pytest` | **38 passed** |
| Web lint | `npm run lint:web` | pass |
| Web typecheck | `npm run typecheck -w web` | pass |
| Web unit (Vitest) | `npm run test:web` | **19 passed** |
| Web production build | `npm run build -w web` | pass |
| End-to-end (Playwright, real API, worker and web) | `npx playwright test` | **5 passed** (desktop and Pixel 7) |
| Dependency audit | `pip-audit`, `npm audit` | 0 known vulnerabilities |

API coverage includes: liveness/readiness (including DB-down → 503), honest status, worker
heartbeat and staleness, bootstrap one-time and token rules, weak-password rejection without echo,
login/logout revocation, CSRF, identical unknown-user and bad-password responses, lockout, auth
rate limit, tampered and forged JWTs, audit log without secrets, production config guards, the
execution-safety code scan (14 patterns), WebSocket auth, Origin check and ping, CLI.

The CI workflow itself has not run yet. It runs on the first push.

## 5. Known limitations

- Docker images are unbuilt here (no daemon). Compose config is validated only.
- Shared TypeScript types are hand-maintained mirrors of the Pydantic schemas (no OpenAPI codegen
  yet).
- Single-user oriented: no user-management UI, no password change or reset flow yet.
- WebSocket in development connects directly to the API port (cookie is host-scoped, so it works
  on localhost). Production needs TLS (wss) behind one host or a reverse proxy.
- No market data, rule verification, risk engine or AI yet. These are explicitly shown as
  not configured.

## 6. Required credentials

None for Phase 1. Upcoming: market-data provider key (Phase 2), possibly Tradovate API access if
read-only use is verified as permitted (Phase 2/4), `APEX_ANTHROPIC_API_KEY` (Phase 5),
economic-calendar key (Phase 5).

## 7. Commands

```bash
scripts/dev.sh                               # API :8000, worker, web :3000
cd apps/api && uv run pytest                 # API tests
npm run test:web                             # web unit tests
cd apps/web && npx playwright test           # e2e
docker compose up --build                    # containerised stack
```

## 8. Next phase (2 — market data)

Provider-independent adapter interface; data-quality model (live/delayed/historical/simulated/
unavailable) on every bar; candle storage with duplicate protection and missing-bar detection;
timestamp normalisation and exchange calendars; freshness monitoring; Redis fan-out to the
WebSocket; real charts (lightweight-charts). Research Tradovate's documented market-data and
read-only options and their applicability to Apex accounts, then select a licensed provider.
