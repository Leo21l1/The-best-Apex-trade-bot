# Morning runbook (about 5 minutes, before 9:00 ET)

PROJECT APEX is analysis only: no broker connection, no orders. Readiness is not proof of an edge.

1. Start / update (PowerShell, in `C:\PROJECT-APEX`):
   `docker compose up -d --build api worker web` then `docker compose ps` (all `running`).
2. `docker compose exec api python -m app.cli preflight`. Read every line; FAIL is never hidden:
   * Migrations PASS (database = code head).
   * Worker PASS; Live feed PASS (streaming, LIVE).
   * ES / NQ / MNQ data: PASS while the market is open, with a fresh last bar.
   * ES / NQ / MNQ contract: PASS with the actual contract (e.g. ESZ6) and instrument_id; tick and point
     values must match ES 0.25 = $12.50 / $50, NQ 0.25 = $5 / $20, MNQ 0.25 = $0.50 / $2.
   * News calendar: PASS with a last successful download in the last 3 hours. STALE or UNAVAILABLE
     means news risk is unknown and no setup can confirm.
   * ES / NQ / MNQ analysis: SMC active on 1m, 5m, 15m, 1h, plus the current setup state and confluence.
   * Trading journal PASS; Analysis-only mode PASS; Order execution PASS (none exists).
   * Account snapshot, copilot and notifications are INFO (optional).
3. Open http://localhost:3000 (desktop and phone):
   * The three cards show LIVE, a contract, a price with a small age, and a news status.
   * The News tab shows "Last successful download" and the next high-impact USD event.
4. Strategy triggers are evaluated 09:35-15:30 ET, and confirmations also need the New York session.
   Before then, cards show bias, structure and developing setups only.
5. Every setup is RESEARCH / UNVALIDATED unless the evidence label says VALIDATED. A plan whose
   1-contract risk exceeds $1,000 shows a warning; its structural stop is not moved.

If anything essential fails, fix it before relying on the dashboard. Do not run research jobs
(confluence-study, research-study) during market hours.
