"""Operational CLI.

python -m app.cli create-admin --email you@example.com      (prompts for password)
python -m app.cli reset-db --yes                              (non-production only)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import sys
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, text

from app.core.config import Environment, get_settings
from app.core.db import dispose_engine, get_engine, get_sessionmaker
from app.core.security import hash_password
from app.models.user import Role, User
from app.schemas.auth import BootstrapRequest
from app.services.audit import record_audit


async def create_admin(email: str, password: str) -> int:
    try:
        BootstrapRequest(email=email, password=password)
    except ValidationError as exc:
        print(f"Invalid input: {exc.errors()[0]['msg']}", file=sys.stderr)
        return 2
    async with get_sessionmaker()() as s:
        if await s.scalar(select(User).where(User.email == email.lower())):
            print("User already exists", file=sys.stderr)
            return 1
        user = User(email=email.lower(), password_hash=hash_password(password), role=Role.ADMIN.value)
        s.add(user)
        await s.flush()
        await record_audit(s, action="cli.create_admin", outcome="success", actor_id=user.id)
        await s.commit()
    print(f"Created admin {email.lower()}")
    return 0


async def reset_db() -> int:
    if get_settings().environment == Environment.PRODUCTION:
        print("Refusing to reset a production database", file=sys.stderr)
        return 1
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE backtest_runs, paper_accounts, journal_trades, journal_edits, trade_plans, news_items, "
                "copilot_exchanges, "
                "economic_events, "
                "account_snapshots, rules, alerts, signals, "
                "candles, audit_logs, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    print("Database tables truncated")
    return 0


async def databento_check(live_seconds: int) -> int:
    """Free diagnostics only: dataset list/range, one out-of-range request (422, no data billed),
    a short live session for ES and NQ, and the latest stored bars. Credentials are never printed."""
    import httpx

    from app.core.config import get_settings

    s = get_settings()
    key = s.databento_api_key.get_secret_value().strip() if s.databento_api_key else ""
    if not key:
        print("APEX_DATABENTO_API_KEY is not set.")
        return 1
    try:
        return await _databento_check(key, s, live_seconds)
    except httpx.HTTPError as exc:
        print(f"Network error reaching Databento: {type(exc).__name__}. Check internet access from the container.")
        return 1


async def _databento_check(key: str, s: Any, live_seconds: int) -> int:
    from datetime import UTC, datetime, timedelta

    import httpx
    from sqlalchemy import text

    from app.adapters.marketdata.base import ProviderError
    from app.adapters.marketdata.databento import BASE_URL, DATASET, DatabentoProvider, error_detail

    ok = True
    now = datetime.now(UTC)
    print(f"Now (UTC): {now:%Y-%m-%d %H:%M:%S}  roll rule: {s.databento_roll_rule}")
    async with httpx.AsyncClient(base_url=BASE_URL, auth=(key, ""), timeout=30) as c:
        r = await c.get("/v0/metadata.list_datasets")
        print(
            f"[1] list_datasets: HTTP {r.status_code}",
            "(GLBX.MDP3 available)" if r.status_code == 200 and DATASET in r.json() else "",
        )
        if r.status_code != 200:
            print("    ", error_detail(r, key))
            return 1
        p = DatabentoProvider(key, client=c, roll_rule=s.databento_roll_rule)
        end = await p.available_end(refresh=True)
        print(
            f"[2] historical available end (ohlcv-1m): {end:%Y-%m-%d %H:%M:%S} UTC, "
            f"{(now - end).total_seconds() / 60:.1f} min behind now"
        )
        r = await c.get(
            "/v0/timeseries.get_range",
            params={
                "dataset": DATASET,
                "symbols": p.continuous("ES"),
                "stype_in": "continuous",
                "schema": "ohlcv-1m",
                "encoding": "json",
                "start": (end - timedelta(minutes=1)).isoformat(),
                "end": now.isoformat(),
            },
        )
        if r.status_code == 422:
            case, msg = error_detail(r, key)
            print(f"[3] request ending at 'now' (the old behaviour): HTTP 422 case={case!r}\n    message: {msg}")
            print("    -> confirms the end timestamp exceeded the historical cutoff; requests are now clamped.")
        else:
            print(f"[3] request ending at 'now': HTTP {r.status_code} (cutoff currently not exceeded)")
        print(
            f"[4] live session: ES/NQ {s.databento_roll_rule} front month, replaying the last 5 minutes "
            f"(up to {live_seconds}s)..."
        )
        got: dict[str, datetime] = {}
        try:

            async def run() -> None:
                # aclosing: when the time limit cancels us, the generator's finally block terminates
                # this diagnostic's own Live session (it is separate from the worker's session).
                async with contextlib.aclosing(p.stream_candles(["ES", "NQ"], since=now - timedelta(minutes=5))) as gen:
                    async for bar in gen:
                        lag = (datetime.now(UTC) - bar.ts).total_seconds() / 60
                        print(
                            f"    {bar.symbol} {bar.ts:%Y-%m-%d %H:%M} UTC close {bar.close} vol {bar.volume} "
                            f"({bar.quality.value}, bar opened {lag:.1f} min ago)"
                        )
                        got[bar.symbol] = bar.ts
                        if len(got) == 2 and all(datetime.now(UTC) - t < timedelta(minutes=3) for t in got.values()):
                            return

            await asyncio.wait_for(run(), live_seconds)
        except TimeoutError:
            pass
        except ProviderError as exc:
            ok = False
            print(f"    LIVE ERROR: {exc}")
        if not got and ok:
            ok = False
            print(
                "    No live bars received. If the market is closed (CME daily break 17:00-18:00 ET, weekends) "
                "this is expected; otherwise check the live entitlement in the Databento portal."
            )
        elif got:
            print("    Live entitlement OK: bars received for " + ", ".join(sorted(got)))
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT symbol, max(ts) AS last, count(*) FROM candles WHERE provider = 'databento' "
                    "AND symbol IN ('ES','NQ') GROUP BY symbol ORDER BY symbol"
                )
            )
        ).all()
    print(
        "[5] stored databento bars: "
        + ("; ".join(f"{r[0]} last {r[1]:%Y-%m-%d %H:%M} UTC ({r[2]} bars)" for r in rows) or "none")
    )
    return 0 if ok else 1


async def pattern_stats_cmd(symbol: str, timeframe: str, days: int, horizon: int) -> int:
    import json
    from datetime import UTC, datetime, timedelta

    from app.core.config import get_settings
    from app.marketdata.instruments import INSTRUMENTS
    from app.marketdata.types import Timeframe
    from app.research.bars import aggregate, closed_only
    from app.research.pattern_stats import pattern_stats
    from app.research.scanner import load_bars

    s = get_settings()
    now = datetime.now(UTC)
    tf = Timeframe(timeframe)
    async with get_sessionmaker()() as session:
        m1 = await load_bars(session, s.market_data_provider, symbol, now - timedelta(days=days), now)
    bars = closed_only(aggregate(m1, tf), tf, now)
    if len(bars) < 200:
        print(f"Only {len(bars)} {timeframe} bars stored for {symbol} ({s.market_data_provider}); need >= 200.")
        return 1
    out = pattern_stats(symbol, timeframe, bars, float(INSTRUMENTS[symbol].tick_size), horizon=horizon)
    out["provider"] = s.market_data_provider
    print(json.dumps(out, indent=2, default=str))
    return 0


async def _provenance(session: Any, provider: str, symbol: str, since: Any, until: Any) -> dict[str, int]:
    from sqlalchemy import text

    rows = await session.execute(
        text(
            "SELECT quality, count(*) FROM candles WHERE provider = :p AND symbol = :s AND ts >= :a AND ts < :b "
            "GROUP BY quality"
        ),
        {"p": provider, "s": symbol, "a": since, "b": until},
    )
    return {f"{provider}/{r[0]}": int(r[1]) for r in rows}


def _print_audit(d: dict[str, Any]) -> None:
    print(f"== {d['symbol']} stored history (requested {d['requested']['days']} days)")
    if not d.get("bars"):
        print("   no bars stored in the requested window")
        return
    print(f"   bars {d['bars']} | {d['earliest_bar']} -> {d['latest_bar']} ({d['span_days']} days)")
    print(f"   provenance {d['provenance']}")
    print(
        f"   sessions {d['sessions']} | usable (>= 300 regular-hours bars) {d['usable_sessions']} | partial "
        f"{len(d['partial_sessions'])} | overnight-only {len(d['overnight_only_sessions'])}"
    )
    if d.get("truncated_by_history"):
        print("   NOTE: stored history starts after the requested start; the window is limited by what is stored")
    print(f"   integrity {d['integrity']}")
    print(f"   missing in-session minutes {d['missing_in_session_minutes']} | largest gaps {d['largest_gaps'][:3]}")
    print(f"   price range {d['price_range']} | median volume {d['median_volume']}")
    print(f"   DATA VERDICT: {d['verdict']} ({d['verdict_rule']})")


async def _load(symbol: str, days: int) -> tuple[Any, list[Any], dict[str, int], Any, Any]:
    from datetime import UTC, datetime, timedelta

    from app.core.config import get_settings
    from app.research.scanner import load_bars

    s = get_settings()
    now = datetime.now(UTC)
    start = now - timedelta(days=days)
    async with get_sessionmaker()() as session:
        bars = await load_bars(session, s.market_data_provider, symbol, start, now)
        prov = await _provenance(session, s.market_data_provider, symbol, start, now)
    return s, bars, prov, start, now


async def data_audit_cmd(symbols: list[str], days: int) -> int:
    """Stored-history coverage, sessions, gaps, integrity and provenance (read-only)."""
    import json

    from app.research.data_audit import audit_bars, compare_instruments

    loaded = {}
    for sym in symbols:
        _s, bars, prov, start, now = await _load(sym, days)
        loaded[sym] = bars
        d = audit_bars(sym, bars, start, now, prov)
        _print_audit(d)
        print(f"   usable sessions: {d.get('usable_session_dates')}")
        if d.get("quarterly_roll_dates_in_range"):
            print(
                f"   quarterly roll dates in range (unadjusted continuous prices jump here): "
                f"{d['quarterly_roll_dates_in_range']}"
            )
        from app.services.history import explain_missing

        async with get_sessionmaker()() as session:
            why = await explain_missing(session, sym, target=max(25, d.get("sessions", 0)))
        if why:
            print("   incomplete expected sessions (CME calendar):")
            for w in why:
                print(
                    f"     {w['date']} {w['weekday']} {w['session_et']}: {w['rth_bars']}/{w['rth_required']} "
                    f"regular-hours bars - {w['reason']}"
                )
    if len(symbols) >= 2:
        for i in range(1, len(symbols)):
            print(
                "== independence",
                json.dumps(compare_instruments(symbols[0], loaded[symbols[0]], symbols[i], loaded[symbols[i]])),
            )
    return 0


async def rr_study_cmd(symbol: str, days: int, strategies: list[str], out: str | None) -> int:
    """Compare reward:risk configurations out of sample on stored bars (read-only)."""
    import json

    from app.marketdata.instruments import INSTRUMENTS
    from app.research.rr_study import run_study
    from app.strategies.library import STRATEGY_BY_ID

    bad = [x for x in strategies if x not in STRATEGY_BY_ID]
    if bad:
        print(f"Unknown strategies: {bad}. Known: {sorted(STRATEGY_BY_ID)}")
        return 2
    s, bars, prov, start, now = await _load(symbol, days)
    if s.market_data_provider == "none":
        print("No market-data provider configured.")
        return 1
    inst = INSTRUMENTS[symbol]
    comm = s.cost_commission_per_side_micro if inst.is_micro else s.cost_commission_per_side_standard
    quality = "simulated" if s.market_data_provider == "simulated" else "historical"
    res = run_study(
        bars, symbol, quality, strategies, comm, s.cost_slippage_ticks_per_side, requested=(start, now), provenance=prov
    )
    if out:
        from pathlib import Path

        Path(out).write_text(json.dumps(res, indent=2, default=str))  # noqa: ASYNC240
    _print_audit(res["data"])
    sp = res.get("split") or {}
    print(
        f"== split: {sp.get('in_sample_days', 0)} in-sample sessions ({sp.get('in_sample_bars', 0)} bars), "
        f"{sp.get('out_of_sample_days', 0)} out-of-sample sessions ({sp.get('out_of_sample_bars', 0)} bars); "
        f"costs {res['costs']}"
    )
    c = res["costs"]
    print(
        "\nOOS $/trade = average NET P&L per out-of-sample trade in USD, 1 contract, after commission "
        f"(${c['commission_per_side']:g}/side) and slippage ({c['slippage_ticks_per_side']:g} tick "
        "on entry, stop and time exits; none on limit targets)."
    )
    print(f"{'strategy':18} {'config':10} {'signals':>7} {'IS tr':>5} {'OOS tr':>6} {'OOS $/trade':>11}  verdict")
    for r in res["rows"]:
        if "error" in r:
            print(f"{r['strategy_id']:18} {r['config']:10} error: {r['error']}")
            continue
        o, f = r["out_of_sample"], r.get("funnel", {})
        print(
            f"{r['strategy_id']:18} {r['config']:10} {f.get('signals', 0):>7} {r['in_sample'].get('trades') or 0:>5} "
            f"{o.get('trades') or 0:>6} {o.get('expectancy') or 0:>11}  {r['verdict']}"
        )
    print("\n== signal funnel per strategy (exit at T1, every signal):")
    for r in res["rows"]:
        if r.get("config") != "t1_all" or "funnel" not in r:
            continue
        print(f"-- {r['strategy_id']}: {r['explanation']}")
        for k, v in sorted(r["funnel"].items(), key=lambda kv: -kv[1]):
            print(f"     {v:>6}  {k}")
    gates = [r for r in res["rows"] if r.get("config") not in ("t1_all", None) and r.get("funnel")]
    for r in gates:
        rej = {k: v for k, v in r["funnel"].items() if k.startswith("signal rejected: net R:R")}
        if rej:
            print(f"   {r['strategy_id']:18} {r['config']:10} {rej}")
    print("\n" + res["conclusion"])
    return 0


async def backfill_cmd(
    symbols: list[str],
    sessions: int,
    approve_cost: float | None,
    accept_unknown: bool,
    include_partial: bool = False,
) -> int:
    """Estimate (default) or start an approved historical backfill. Never downloads without approval,
    and never starts when the cost estimate failed."""
    from app.adapters.marketdata.base import ProviderAuthError, ProviderError
    from app.core.config import get_settings
    from app.services import history

    s = get_settings()
    if s.market_data_provider in ("none",):
        print("No market-data provider configured.")
        return 1
    print(
        f"Estimating historical cost for {', '.join(symbols)} (target {sessions} sessions; timeout "
        f"{s.databento_timeout_seconds:g}s per request, up to {s.databento_max_retries} retries)...",
        flush=True,
    )
    try:
        async with get_sessionmaker()() as session:
            est = await history.estimate(
                session, symbols, sessions, progress=lambda m: print(m, flush=True), include_partial=include_partial
            )
    except ProviderAuthError as exc:
        print(f"ESTIMATE FAILED: {exc}\nCheck APEX_DATABENTO_API_KEY and your Databento entitlements. Nothing started.")
        return 3
    except ProviderError as exc:
        print(f"ESTIMATE FAILED: {exc}\nNothing was started. Try again later or raise APEX_DATABENTO_TIMEOUT_SECONDS.")
        return 3
    for it in est["items"]:
        c = it["estimated_cost_usd"]
        print(
            f"{it['symbol']}: {it['sessions_stored']} sessions stored, {len(it['missing_sessions'])} to download "
            f"in {it.get('requests', 0)} range(s); estimated cost {'FAILED' if c is None else f'${c:.4f}'}"
            + (f" - {it['note']}" if it["note"] else "")
        )
    if est.get("estimate_failed"):
        print(
            "ESTIMATE FAILED for at least one symbol; nothing was started and no cost is assumed.\n"
            "Try again later, or raise APEX_DATABENTO_TIMEOUT_SECONDS in .env and restart the api container."
        )
        return 3
    cost = est["estimated_cost_usd"]
    print(f"TOTAL ESTIMATED COST: {'UNKNOWN' if cost is None else f'${cost:.4f}'} ({est['note']})")
    if approve_cost is None and not accept_unknown:
        print("Estimate only. To start, re-run with --approve-cost <amount >= estimate>.")
        return 0
    if cost is None and not accept_unknown:
        print("The provider could not estimate the cost; add --accept-unknown-cost to proceed anyway.")
        return 3
    if cost is not None and cost > 0 and (approve_cost is None or approve_cost + 1e-9 < cost):
        print(f"Approved amount is below the estimate ${cost:.4f}; nothing started.")
        return 3
    job = await history.get_job()
    if job and job["status"] in ("queued", "running"):
        print(f"A backfill is already running: {job['id']}")
        return 4
    plan = {it["symbol"]: it["missing_sessions"] for it in est["items"]}
    job = await history.start_job(symbols, sessions, cost, "cli", plan=plan)
    print(
        f"Backfill {job['id']} queued for exactly the {sum(len(v) for v in plan.values())} estimated sessions. "
        "The worker downloads a few per cycle and saves progress after each one; follow it on System Health "
        "or with `python -m app.cli data-audit`."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apex")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ca = sub.add_parser("create-admin")
    ca.add_argument("--email", required=True)
    ca.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read password from stdin instead of prompting",
    )
    rd = sub.add_parser("reset-db")
    rd.add_argument("--yes", action="store_true", required=True)
    ps = sub.add_parser("pattern-stats", help="Historical validation of SMC pattern detectors on stored bars")
    ps.add_argument("--symbol", default="ES")
    ps.add_argument("--timeframe", default="5m")
    ps.add_argument("--days", type=int, default=60)
    ps.add_argument("--horizon", type=int, default=12)
    da = sub.add_parser("data-audit", help="Stored-history coverage, sessions, gaps, integrity (read-only)")
    da.add_argument("--symbols", default="ES,NQ")
    da.add_argument("--days", type=int, default=60)
    bf = sub.add_parser("backfill", help="Estimate (default) or start an approved historical backfill")
    bf.add_argument("--symbols", default="ES,NQ")
    bf.add_argument("--sessions", type=int, default=25)
    bf.add_argument("--approve-cost", type=float, default=None)
    bf.add_argument("--accept-unknown-cost", action="store_true")
    bf.add_argument(
        "--include-partial",
        action="store_true",
        help="Also re-request sessions downloaded before that are still incomplete (shown in the estimate)",
    )
    rr = sub.add_parser("rr-study", help="Compare reward:risk configurations out of sample on stored bars")
    rr.add_argument("--symbol", default="ES")
    rr.add_argument("--days", type=int, default=60)
    rr.add_argument("--strategies", default="vwap_trend,orb,momentum_pullback,breakout_retest,mtf_trend")
    rr.add_argument("--out", default=None, help="Also write the full JSON result to this file")
    dc = sub.add_parser("databento-check", help="Diagnose Databento historical cutoff and live entitlement")
    dc.add_argument("--live-seconds", type=int, default=90)
    args = parser.parse_args(argv)

    async def run() -> int:
        try:
            if args.cmd == "create-admin":
                pw = sys.stdin.readline().rstrip("\n") if args.password_stdin else getpass.getpass()
                return await create_admin(args.email, pw)
            if args.cmd == "databento-check":
                return await databento_check(args.live_seconds)
            if args.cmd == "data-audit":
                return await data_audit_cmd(
                    [x.strip().upper() for x in args.symbols.split(",") if x.strip()], args.days
                )
            if args.cmd == "backfill":
                return await backfill_cmd(
                    [x.strip().upper() for x in args.symbols.split(",") if x.strip()],
                    args.sessions,
                    args.approve_cost,
                    args.accept_unknown_cost,
                    args.include_partial,
                )
            if args.cmd == "rr-study":
                syms = [x.strip() for x in args.strategies.split(",") if x.strip()]
                return await rr_study_cmd(args.symbol.upper(), args.days, syms, args.out)
            if args.cmd == "pattern-stats":
                return await pattern_stats_cmd(args.symbol, args.timeframe, args.days, args.horizon)
            return await reset_db()
        finally:
            await dispose_engine()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
