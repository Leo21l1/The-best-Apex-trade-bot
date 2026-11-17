"""Historical data coverage, cost-estimated backfill and data readiness.

* Coverage is computed from stored 1-minute bars per trade date (18:00 ET roll) against the CME
  exchange calendar for the instrument (sessions, holidays, early closes).
* A session counts as stored when it has at least ``MIN_BARS_PER_SESSION`` bars (real 1-minute
  data omits minutes without trades, so a full session is not 1,380 bars).
* Backfill never starts on its own: ``estimate`` returns the missing sessions and the provider's own
  cost estimate (Databento ``metadata.get_cost``, which is not billed); a job only runs after the
  user approves that estimate. The job fetches one session per symbol per worker cycle, through the
  provider's rate limiter and historical-cutoff clamp, so the live stream keeps running.
* Existing bars are reused: only sessions below the threshold are requested; upserts are
  idempotent, so overlapping downloads cannot create duplicates.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redis import get_redis
from app.services.session_intel import session_bounds

MIN_BARS_PER_SESSION = 200
TARGET_SESSIONS = 25
MIN_SESSIONS_PRELIMINARY = 5
JOB_KEY = "apex:backfill:job"


async def stored_sessions(session: AsyncSession, provider: str, symbol: str, since: datetime) -> dict[date, int]:
    """Trade date -> number of stored 1-minute bars (trade date rolls at 18:00 New York)."""
    rows = await session.execute(
        text(
            "SELECT (date_trunc('day', (ts AT TIME ZONE 'America/New_York') + interval '6 hours'))::date AS d, "
            "count(*) FROM candles WHERE provider = :p AND symbol = :s AND ts >= :since "
            "GROUP BY d ORDER BY d"
        ),
        {"p": provider, "s": symbol, "since": since},
    )
    return {r[0]: int(r[1]) for r in rows}


def expected_sessions(symbol: str, end: date, count: int) -> list[date]:
    """The last ``count`` exchange trade dates on or before ``end`` (holidays excluded)."""
    out: list[date] = []
    d = end
    for _ in range(count * 3 + 30):
        if session_bounds(symbol, d) is not None:
            out.append(d)
            if len(out) >= count:
                break
        d -= timedelta(days=1)
    return sorted(out)


async def coverage(
    session: AsyncSession, symbol: str, now: datetime | None = None, target: int = TARGET_SESSIONS
) -> dict[str, Any]:
    from app.marketdata.calendar import trading_day

    s = get_settings()
    now = now or datetime.now(UTC)
    provider = s.market_data_provider
    if provider == "none":
        return {"symbol": symbol, "provider": None, "status": "UNAVAILABLE", "reason": "No market-data provider."}
    today = trading_day(now)
    expected = expected_sessions(symbol, today - timedelta(days=1), target)  # completed sessions only
    since = datetime.combine(expected[0] - timedelta(days=1), datetime.min.time(), UTC) if expected else now
    stored = await stored_sessions(session, provider, symbol, since)
    full = [d for d in expected if stored.get(d, 0) >= MIN_BARS_PER_SESSION]
    missing = [d for d in expected if stored.get(d, 0) < MIN_BARS_PER_SESSION]
    bounds = await session.execute(
        text("SELECT min(ts), max(ts), count(*) FROM candles WHERE provider = :p AND symbol = :s"),
        {"p": provider, "s": symbol},
    )
    first, last, total = bounds.one()
    n = len(full)
    return {
        "symbol": symbol,
        "provider": provider,
        "target_sessions": target,
        "sessions_stored": n,
        "sessions_expected": len(expected),
        "missing_sessions": [d.isoformat() for d in missing],
        "earliest_bar": first.isoformat() if first else None,
        "latest_bar": last.isoformat() if last else None,
        "bars_total": int(total or 0),
        "relative_volume_readiness": "robust"
        if n >= 20
        else "preliminary"
        if n >= MIN_SESSIONS_PRELIMINARY
        else "insufficient",
        "volatility_readiness": "robust"
        if n >= 20
        else "preliminary"
        if n >= MIN_SESSIONS_PRELIMINARY
        else "insufficient",
        "backtest_readiness": "ready"
        if (total or 0) >= 1000 and n >= 10
        else "limited"
        if (total or 0) >= 1000
        else "insufficient",
        "rule": f"A session counts when it has >= {MIN_BARS_PER_SESSION} stored 1-minute bars; sessions follow the "
        "CME calendar for this instrument (holidays excluded).",
    }


async def estimate(session: AsyncSession, symbols: list[str], target: int = TARGET_SESSIONS) -> dict[str, Any]:
    """Missing sessions per symbol and the provider's cost estimate. Nothing is downloaded."""
    from app.marketdata.factory import build_provider

    s = get_settings()
    provider = build_provider(s)
    items: list[dict[str, Any]] = []
    total_cost: float | None = 0.0
    try:
        for sym in symbols:
            cov = await coverage(session, sym, target=target)
            missing = [date.fromisoformat(x) for x in cov.get("missing_sessions", [])]
            windows = []
            for d in missing:
                b = session_bounds(sym, d)
                if b:
                    windows.append((b[0], b[1]))
            cost: float | None = 0.0
            note = None
            if windows and s.market_data_provider == "databento":
                cost, note = await _databento_cost(provider, sym, windows)
            elif s.market_data_provider == "simulated":
                note = "Simulated provider: synthetic bars, no cost, labelled SIMULATED."
            if cost is None or total_cost is None:
                total_cost = None
            else:
                total_cost += cost
            items.append(
                {
                    "symbol": sym,
                    "missing_sessions": [d.isoformat() for d in missing],
                    "sessions_stored": cov.get("sessions_stored"),
                    "estimated_cost_usd": cost,
                    "note": note,
                }
            )
    finally:
        if provider is not None:
            await provider.aclose()
    return {
        "provider": s.market_data_provider,
        "target_sessions": target,
        "items": items,
        "estimated_cost_usd": round(total_cost, 4) if total_cost is not None else None,
        "note": "Estimates come from the provider's metadata.get_cost (not billed). Backfill only starts after "
        "you approve the estimate. Your subscription may already include this data.",
    }


async def _databento_cost(
    provider: Any, symbol: str, windows: list[tuple[datetime, datetime]]
) -> tuple[float | None, str | None]:
    """Sum of metadata.get_cost over the missing session windows (clamped to the available end)."""
    from app.adapters.marketdata.databento import DATASET, SCHEMA, error_detail

    try:
        end_cap = await provider.available_end()
    except Exception as exc:
        return None, f"Could not read the historical cutoff: {type(exc).__name__}"
    total = 0.0
    for start, end in windows:
        end = min(end, end_cap)
        if start >= end:
            continue
        r = await provider._get(
            "/v0/metadata.get_cost",
            {
                "dataset": DATASET,
                "symbols": provider.continuous(symbol),
                "stype_in": "continuous",
                "schema": SCHEMA,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
        if r.status_code >= 400:
            case, msg = error_detail(r, None)
            return None, f"Cost estimate unavailable (HTTP {r.status_code} {case}): {msg}"
        try:
            total += float(r.json())
        except (ValueError, TypeError):
            return None, "Unexpected cost response from the provider."
    return round(total, 4), None


async def start_job(symbols: list[str], target: int, approved_cost: float | None, user: str | None) -> dict[str, Any]:
    job = {
        "id": uuid.uuid4().hex[:12],
        "symbols": symbols,
        "target": target,
        "approved_cost_usd": approved_cost,
        "approved_by": user,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "queued",
        "progress": {},
        "errors": [],
    }
    await get_redis().set(JOB_KEY, json.dumps(job))
    return job


async def get_job() -> dict[str, Any] | None:
    try:
        raw = await get_redis().get(JOB_KEY)
    except (RedisError, OSError):
        return None
    return json.loads(raw) if raw else None


async def run_job_step(session: AsyncSession, provider: Any, max_sessions: int = 3) -> bool:
    """Backfill up to ``max_sessions`` missing sessions of the approved job. Returns True if work was done."""
    from app.adapters.marketdata.base import ProviderError
    from app.marketdata.store import upsert_candles
    from app.marketdata.validation import sort_dedupe, validate_candle

    job = await get_job()
    if not job or job["status"] in ("done", "failed", "cancelled") or provider is None:
        return False
    job["status"] = "running"
    done = 0
    for sym in job["symbols"]:
        cov = await coverage(session, sym, target=job["target"])
        prog = job["progress"].setdefault(sym, {"fetched_sessions": 0, "bars": 0})
        prog["remaining"] = len(cov.get("missing_sessions", []))
        for ds in cov.get("missing_sessions", []):
            if done >= max_sessions:
                break
            if ds in prog.setdefault("attempted", []):
                continue
            prog["attempted"].append(ds)
            b = session_bounds(sym, date.fromisoformat(ds))
            if not b:
                continue
            try:
                candles = await provider.fetch_candles(sym, b[0], b[1])
            except ProviderError as exc:
                job["errors"].append(f"{sym} {ds}: {exc}"[:300])
                continue
            now = datetime.now(UTC)
            good = sort_dedupe([c for c in candles if not validate_candle(c, now)])
            if good:
                await upsert_candles(session, good)
                await session.commit()
            prog["fetched_sessions"] += 1
            prog["bars"] += len(good)
            done += 1
        if done >= max_sessions:
            break
    remaining = 0
    for sym in job["symbols"]:
        cov = await coverage(session, sym, target=job["target"])
        left = [
            d for d in cov.get("missing_sessions", []) if d not in job["progress"].get(sym, {}).get("attempted", [])
        ]
        job["progress"].setdefault(sym, {})["remaining"] = len(left)
        job["progress"][sym]["sessions_stored"] = cov.get("sessions_stored")
        remaining += len(left)
    if remaining == 0:
        job["status"] = "done"
        job["finished_at"] = datetime.now(UTC).isoformat()
    job["updated_at"] = datetime.now(UTC).isoformat()
    try:
        await get_redis().set(JOB_KEY, json.dumps(job))
    except (RedisError, OSError):
        pass
    return done > 0


async def strategy_statistics(
    session: AsyncSession, symbol: str | None = None, strategy_id: str | None = None, include_simulated: bool = False
) -> list[dict[str, Any]]:
    """Latest completed backtest per (strategy, symbol) with in/out-of-sample metrics and segments.

    Simulated-data runs are excluded unless asked for, and are always labelled SIMULATED.
    """
    from sqlalchemy import select

    from app.models.research import BacktestRun

    q = select(BacktestRun).where(BacktestRun.status == "done").order_by(BacktestRun.finished_at.desc()).limit(500)
    if symbol:
        q = q.where(BacktestRun.symbol == symbol)
    if strategy_id:
        q = q.where(BacktestRun.strategy_id == strategy_id)
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for r in (await session.scalars(q)).all():
        if (r.strategy_id, r.symbol) in seen or not r.result:
            continue
        simulated = r.provider == "simulated" or r.result.get("data_class") == "simulated"
        if simulated and not include_simulated:
            continue
        seen.add((r.strategy_id, r.symbol))
        res = r.result
        out.append(
            {
                "run_id": r.id,
                "strategy_id": r.strategy_id,
                "symbol": r.symbol,
                "data_class": "SIMULATED" if simulated else str(res.get("data_class") or "historical").upper(),
                "period": [r.start.isoformat(), r.end.isoformat()],
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "in_sample": res.get("in_sample"),
                "out_of_sample": res.get("out_of_sample"),
                "validation": res.get("validation"),
                "segments": res.get("segments"),
            }
        )
    return out
