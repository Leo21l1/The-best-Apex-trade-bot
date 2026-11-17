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
                "TRUNCATE backtest_runs, paper_accounts, journal_trades, trade_plans, news_items, copilot_exchanges, "
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
            if args.cmd == "pattern-stats":
                return await pattern_stats_cmd(args.symbol, args.timeframe, args.days, args.horizon)
            return await reset_db()
        finally:
            await dispose_engine()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
