# Phase 7 report — hardening

Date: 2026-11-17

## 1. Completed work

- **Security review** ([details](../compliance/security.md)). Fixed:
  - viewers could change data; all writes are now admin-only (12 endpoints tested);
  - no password change or user management; added both, with immediate session revocation;
  - a race in first-admin bootstrap; serialised with a PostgreSQL advisory lock;
  - open WebSockets stayed authorised after logout; they now re-check the session and close;
  - no CSP on API responses and no HSTS; both added.
- **Trusted-proxy handling** accepts CIDR ranges so the real client address works inside Docker
  networks.
- **Failure testing**: Redis down (API keeps serving with in-process rate limits, readiness 503,
  honest component states, stored market data flagged stale, alerts still stored), database down
  (readiness 503), unhandled errors (generic 500 with request id, no internal detail), WebSocket
  reconnect and topic resubscription in the browser, data-feed disconnects and credential errors
  (Phase 2), worker loss (Phases 1–2).
- **Performance** (measured locally on 12 instruments with 130 hours of 1-minute bars):

  | Operation | Time |
  |---|---|
  | Full scanner cycle, 12 instruments, 9 timeframes, 8 strategies | ≈ 2.1 s (cycle runs every 20 s) |
  | Candle query, 600 bars, any timeframe | 14–17 ms |
  | Backfill of 78,480 bars | 13.8 s |
  | Backtest, one strategy, one month of 1-minute bars | ≈ 2–3 s |

- **Responsive design**: an end-to-end test loads all 15 pages at phone size and checks none
  scrolls horizontally; the navigation drawer is tested.
- **Settings**: change password; admin user management; "Read-only" marker for viewers.
- **Deployment**: production guide (secrets, HTTPS reverse proxy with an example Caddyfile, first
  admin, upgrades, backups and restore, monitoring, hardening checklist); Compose updated.
- **Documentation**: README rewritten for the finished system; architecture updated.

## 3–4. Final verification (this environment)

| Check | Result |
|---|---|
| API lint (ruff) and format | pass |
| API tests (real PostgreSQL + Redis) | **276 passed** |
| `pip-audit` | no known vulnerabilities |
| Web lint and typecheck | 0 issues |
| Web unit tests | **37 passed** |
| Web production build | pass |
| `npm audit --audit-level=high` | 0 vulnerabilities |
| End-to-end (Playwright, real API + worker + web, desktop and phone) | **22 passed** |
| `docker compose config` | valid |

## 5. Known limitations (whole system)

- **Apex rules are unverified.** The values match search snippets of the official help centre,
  but Apex's site was unreachable from the build environment. Verify each rule in the app.
- **No real market data has flowed.** Only the simulated provider has run; the Databento adapter is
  untested against the live service, and there is no real-time adapter yet.
- **No strategy is validated.** Validation needs backtests on real historical data.
- **The copilot has not called the real Anthropic API.** No key was available.
- **Docker images are unbuilt.** There is no Docker daemon here, and the GitHub Actions CI has not
  yet run on GitHub.
- **Models not built yet**: exchange holidays, contract rolls, and spread or queue modelling in
  the backtester.
- **Single-factor authentication.** Use a VPN or identity-aware proxy if the site is exposed
  publicly.

## 6. Credentials needed to finish

- A licensed market data key (e.g. `APEX_DATABENTO_API_KEY`).
- `APEX_ANTHROPIC_API_KEY` for the copilot.
- Optionally, official ICS calendar URLs in `APEX_CALENDAR_ICS_URLS`.

## 7. Commands

```bash
APEX_MARKET_DATA_PROVIDER=simulated scripts/dev.sh    # local demo
docker compose up -d --build                          # containers (see production guide)
```
