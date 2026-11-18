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


async def stored_session_counts(
    session: AsyncSession, provider: str, symbol: str, since: datetime
) -> dict[date, tuple[int, int]]:
    """Trade date -> (all stored 1-minute bars, bars inside 09:30-16:00 ET)."""
    rows = await session.execute(
        text(
            "SELECT (date_trunc('day', (ts AT TIME ZONE 'America/New_York') + interval '6 hours'))::date AS d, "
            "count(*), count(*) FILTER (WHERE (ts AT TIME ZONE 'America/New_York')::time >= time '09:30' "
            "AND (ts AT TIME ZONE 'America/New_York')::time < time '16:00') "
            "FROM candles WHERE provider = :p AND symbol = :s AND ts >= :since GROUP BY d ORDER BY d"
        ),
        {"p": provider, "s": symbol, "since": since},
    )
    return {r[0]: (int(r[1]), int(r[2])) for r in rows}


def session_complete(symbol: str, d: date, counts: tuple[int, int] | None) -> bool:
    """Same definition as the study's 'usable session': enough bars overall AND in regular hours.

    (Counting only total bars let a session with a full overnight but a broken regular session look
    complete to the backfill - so it was never repaired - while the study rejected it.)"""
    from app.research.data_audit import required_rth_bars

    total, rth = counts or (0, 0)
    need = required_rth_bars(symbol, d)
    return total >= MIN_BARS_PER_SESSION and (need == 0 or rth >= need)


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
    stored = await stored_session_counts(session, provider, symbol, since)
    full = [d for d in expected if session_complete(symbol, d, stored.get(d))]
    missing = [d for d in expected if not session_complete(symbol, d, stored.get(d))]
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
        "rule": f"A session counts when it has >= {MIN_BARS_PER_SESSION} stored 1-minute bars and >= 300 "
        "regular-hours bars (scaled down on early-close days); sessions follow the CME "
        "calendar for this instrument (holidays excluded).",
    }


FETCHED_KEY = "apex:backfill:fetched:"  # + provider:symbol -> set of trade dates downloaded once
MAX_SESSIONS_PER_COST_REQUEST = 10


def _ranges(sym: str, missing: list[date]) -> list[tuple[list[date], datetime, datetime]]:
    """Group missing sessions into contiguous runs (by exchange calendar) of at most N sessions.

    Databento charges by the data in the requested range, so cost is additive over time ranges:
    splitting a span into smaller ranges and summing is exact. Contiguous runs avoid estimating
    sessions that are already stored (those would be counted if one range spanned them).
    """
    out: list[tuple[list[date], datetime, datetime]] = []
    run: list[date] = []
    for d in sorted(missing):
        b = session_bounds(sym, d)
        if not b:
            continue
        if run:
            prev_end = session_bounds(sym, run[-1])[1]  # type: ignore[index]
            contiguous = (b[0] - prev_end) <= timedelta(days=4)  # next exchange session (weekends, holidays)
            if not contiguous or len(run) >= MAX_SESSIONS_PER_COST_REQUEST:
                out.append((run, session_bounds(sym, run[0])[0], prev_end))  # type: ignore[index]
                run = []
        run.append(d)
    if run:
        out.append((run, session_bounds(sym, run[0])[0], session_bounds(sym, run[-1])[1]))  # type: ignore[index]
    return out


async def already_fetched(provider: str, symbol: str) -> set[str]:
    try:
        return {
            x.decode() if isinstance(x, bytes) else x
            for x in await get_redis().smembers(f"{FETCHED_KEY}{provider}:{symbol}")
        }
    except (RedisError, OSError):
        return set()


async def estimate(
    session: AsyncSession,
    symbols: list[str],
    target: int = TARGET_SESSIONS,
    progress: Any = None,
    provider: Any = None,
    include_partial: bool = False,
) -> dict[str, Any]:
    """Missing sessions per symbol and the provider's cost estimate. Nothing is downloaded.

    A failed estimate is reported as failed (``estimate_failed``), never as a cost of zero, and no
    backfill can be started from it. Sessions already downloaded once are excluded.
    """
    from app.adapters.marketdata.base import ProviderError
    from app.marketdata.factory import build_provider

    say = progress or (lambda _msg: None)
    s = get_settings()
    own = provider is None
    provider = provider or build_provider(s)
    items: list[dict[str, Any]] = []
    total_cost: float | None = 0.0
    failed: list[str] = []
    try:
        for sym in symbols:
            cov = await coverage(session, sym, target=target)
            done = set() if include_partial else await already_fetched(s.market_data_provider, sym)
            missing = [date.fromisoformat(x) for x in cov.get("missing_sessions", []) if x not in done]
            cost: float | None = 0.0
            note = None
            ranges = _ranges(sym, missing)
            if missing and s.market_data_provider == "databento":
                say(f"{sym}: {len(missing)} missing sessions in {len(ranges)} cost request(s)")
                try:
                    cost = await _databento_cost(provider, sym, ranges, say)
                except ProviderError as exc:
                    cost, note = None, f"Cost estimate failed: {exc}"
                    failed.append(f"{sym}: {exc}")
                    say(f"{sym}: estimate FAILED - {exc}")
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
                    "requests": len(ranges),
                    "note": note,
                }
            )
    finally:
        if own and provider is not None:
            await provider.aclose()
    return {
        "provider": s.market_data_provider,
        "target_sessions": target,
        "items": items,
        "estimated_cost_usd": round(total_cost, 4) if total_cost is not None else None,
        "estimate_failed": bool(failed),
        "errors": failed,
        "note": "Estimates come from the provider's metadata.get_cost (not billed). Backfill only starts after "
        "you approve the estimate. Your subscription may already include this data.",
    }


async def _databento_cost(
    provider: Any, symbol: str, ranges: list[tuple[list[date], datetime, datetime]], say: Any = None
) -> float:
    """Sum of metadata.get_cost over the missing ranges (clamped to the available end).

    Raises ProviderError on any failure: a partial sum is never returned as the cost."""
    from app.adapters.marketdata.base import ProviderError
    from app.adapters.marketdata.databento import DATASET, SCHEMA, error_detail

    say = say or (lambda _m: None)
    end_cap = await provider.available_end()
    total = 0.0
    for i, (days, start, end) in enumerate(ranges, 1):
        end = min(end, end_cap)
        if start >= end:
            continue
        say(f"  {symbol} range {i}/{len(ranges)}: {days[0]} to {days[-1]} ({len(days)} sessions)...")
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
            raise ProviderError(f"cost request HTTP {r.status_code} {case}: {msg}")
        try:
            part = float(r.json())
        except (ValueError, TypeError) as exc:
            raise ProviderError("unexpected cost response from the provider") from exc
        total += part
        say(f"  {symbol} range {i}/{len(ranges)}: ${part:.4f}")
    return round(total, 4)


async def start_job(
    symbols: list[str],
    target: int,
    approved_cost: float | None,
    user: str | None,
    plan: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Queue a backfill limited to ``plan`` (symbol -> approved session dates), when given."""
    job = {
        "id": uuid.uuid4().hex[:12],
        "symbols": symbols,
        "target": target,
        "approved_cost_usd": approved_cost,
        "approved_by": user,
        "plan": plan,
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


async def _save_job(job: dict[str, Any]) -> None:
    job["updated_at"] = datetime.now(UTC).isoformat()
    try:
        await get_redis().set(JOB_KEY, json.dumps(job))
    except (RedisError, OSError):
        pass


async def run_job_step(session: AsyncSession, provider: Any, max_sessions: int = 3) -> bool:
    """Backfill up to ``max_sessions`` sessions of the approved job. Returns True if work was done.

    Resumable and charge-safe: the job only downloads the sessions approved with its estimate
    (``plan``); a session is marked as downloaded in Redis as soon as it is stored, and progress is
    saved after every session, so an interrupted job continues where it stopped and never requests
    a stored or already-downloaded session again. Upserts are idempotent (no duplicate bars).
    """
    from app.adapters.marketdata.base import ProviderError
    from app.marketdata.store import upsert_candles
    from app.marketdata.validation import sort_dedupe, validate_candle

    job = await get_job()
    if not job or job["status"] in ("done", "failed", "cancelled") or provider is None:
        return False
    s = get_settings()
    job["status"] = "running"
    done = 0  # requests made this cycle (successful or not): failures also use up the per-cycle budget
    plan = job.get("plan")
    for sym in job["symbols"]:
        cov = await coverage(session, sym, target=job["target"])
        fetched = await already_fetched(s.market_data_provider, sym)
        prog = job["progress"].setdefault(sym, {"fetched_sessions": 0, "bars": 0})
        if plan is not None:
            # The approved plan is the authority: only those sessions, and only while still incomplete.
            todo = [d for d in cov.get("missing_sessions", []) if d in set(plan.get(sym, []))]
        else:
            todo = [d for d in cov.get("missing_sessions", []) if d not in fetched]
        for ds in todo:
            if done >= max_sessions:
                break
            if ds in prog.setdefault("attempted", []):
                continue
            b = session_bounds(sym, date.fromisoformat(ds))
            if not b:
                continue
            prog["attempted"].append(ds)
            await _save_job(job)  # recorded before the (billed) request: a crash cannot cause an automatic repeat
            try:
                candles = await provider.fetch_candles(sym, b[0], b[1])
            except ProviderError as exc:
                job["errors"].append(f"{sym} {ds}: {exc}"[:300])
                done += 1
                await _save_job(job)
                continue
            now = datetime.now(UTC)
            good = sort_dedupe([c for c in candles if not validate_candle(c, now)])
            if good:
                await upsert_candles(session, good)
                await session.commit()
            try:
                await get_redis().sadd(f"{FETCHED_KEY}{s.market_data_provider}:{sym}", ds)
            except (RedisError, OSError):
                pass
            prog["fetched_sessions"] += 1
            prog["bars"] += len(good)
            done += 1
            await _save_job(job)
        if done >= max_sessions:
            break
    remaining = 0
    for sym in job["symbols"]:
        cov = await coverage(session, sym, target=job["target"])
        fetched = await already_fetched(s.market_data_provider, sym)
        attempted = job["progress"].get(sym, {}).get("attempted", [])
        left = [d for d in cov.get("missing_sessions", []) if d not in attempted]
        if plan is not None:
            left = [d for d in left if d in set(plan.get(sym, []))]
        else:
            left = [d for d in left if d not in fetched]
        job["progress"].setdefault(sym, {})["remaining"] = len(left)
        job["progress"][sym]["sessions_stored"] = cov.get("sessions_stored")
        remaining += len(left)
    if remaining == 0:
        job["status"] = "done"
        job["finished_at"] = datetime.now(UTC).isoformat()
    await _save_job(job)
    return done > 0


async def explain_missing(
    session: AsyncSession, symbol: str, target: int = TARGET_SESSIONS, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Why each expected (CME calendar) session in the target window is not complete.

    Uses only local facts - the calendar, stored bar counts, the download record and the last
    backfill job's errors - so it costs nothing and needs no provider request."""
    from app.marketdata.calendar import ET, trading_day
    from app.research.data_audit import required_rth_bars

    s = get_settings()
    now = now or datetime.now(UTC)
    expected = expected_sessions(symbol, trading_day(now) - timedelta(days=1), target)
    if not expected:
        return []
    since = datetime.combine(expected[0] - timedelta(days=1), datetime.min.time(), UTC)
    counts = await stored_session_counts(session, s.market_data_provider, symbol, since)
    fetched = await already_fetched(s.market_data_provider, symbol)
    job = await get_job() or {}
    attempted = set((job.get("progress", {}).get(symbol) or {}).get("attempted", []))
    errors = {e.split(":", 1)[0].split(" ", 1)[1]: e for e in job.get("errors", []) if e.startswith(f"{symbol} ")}
    out = []
    for d in expected:
        c = counts.get(d)
        if session_complete(symbol, d, c):
            continue
        total, rth = c or (0, 0)
        b = session_bounds(symbol, d)
        ds = d.isoformat()
        if ds in errors:
            reason = f"download failed: {errors[ds].split(': ', 1)[-1][:200]}"
        elif ds in fetched:
            reason = f"downloaded, but the provider returned only {rth} regular-hours bars ({total} in total)"
        elif ds in attempted:
            reason = "download started but not completed (interrupted); not retried automatically"
        elif total:
            reason = f"partially stored ({rth} regular-hours / {total} bars, e.g. from the live stream); not backfilled"
        else:
            reason = "not downloaded yet"
        out.append(
            {
                "date": ds,
                "weekday": d.strftime("%a"),
                "session_et": [b[0].astimezone(ET).strftime("%m-%d %H:%M"), b[1].astimezone(ET).strftime("%m-%d %H:%M")]
                if b
                else None,
                "bars": total,
                "rth_bars": rth,
                "rth_required": required_rth_bars(symbol, d),
                "reason": reason,
            }
        )
    return out


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
