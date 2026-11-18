# Morning runbook (Windows, PowerShell) - PROJECT APEX is analysis only

All commands run in `C:\PROJECT-APEX`. Nothing here deletes data. Never run `docker compose down -v`.

## Before 9:00 ET

```powershell
cd C:\PROJECT-APEX
docker compose up -d
docker compose ps
docker compose exec api python -m app.cli preflight
```

`preflight` prints PASS / WARN / FAIL for: PostgreSQL, Redis, worker, live feed, ES and NQ last price
(time, age, LIVE/HISTORICAL), news calendar, account snapshot, Apex rule profile, research studies,
evidence gate, copilot and order execution (always analysis only). Any FAIL blocks READY.

Fix FAILs in the app:

| FAIL | Fix |
|---|---|
| Account snapshot missing/stale | Apex Account Health > enter balance > Save snapshot (repeat after each trade or every 30 min) |
| News calendar | Economic Calendar > ForexFactory calendar > Sync now |
| Live feed / ES or NQ data | `docker compose logs --tail 100 worker`; then `docker compose restart worker` |
| Worker | `docker compose up -d worker` |

Then open http://localhost:3000: System Health (feed LIVE), Advanced Charts (entry confirmation),
Strategy Research (latest studies).

## Session times (America/New_York, daylight saving handled by the exchange calendar)

* CME Globex ES/NQ trade 18:00-17:00 ET Sunday-Friday with a 17:00-18:00 maintenance break.
* 9:00 ET: futures already trading (overnight session); the app shows "New York premarket".
* 9:30 ET: US cash open - the regular session starts; strategy signal windows begin between 09:35 and 10:00 ET.
  No restart is needed; the worker keeps streaming.

## What the signal states mean

* LONG/SHORT READY - every mandatory condition passes, including fresh live data, a fresh account
  snapshot, no news window, net R:R >= the minimum, and a strategy configuration classified
  "Validated for paper-trading consideration". Still a manual decision; nothing is ever sent to a broker.
* WAIT - something required is missing or not yet met (no setup, stale or missing account snapshot,
  unknown news status, strategy not validated, outside the trading window). The panel lists why.
* NO TRADE - a safety condition fails (stale market data while open, news window, risk violation or
  no allowed size, invalidated setup, invalid R:R, market closed).
* DATA UNAVAILABLE - no market data.

As of the corrected research, no strategy configuration is validated, so the app shows WAIT or
NO TRADE with the reasons. That is the intended, honest behaviour.

## If something fails

```powershell
docker compose ps
docker compose logs --tail 100 api
docker compose logs --tail 100 worker
docker compose restart worker
docker compose up -d --build api worker web
```

Containers restart automatically after a crash (`restart: unless-stopped`) and when Docker Desktop
starts. Data lives in the `pgdata` and `redisdata` volumes and survives restarts and rebuilds.
