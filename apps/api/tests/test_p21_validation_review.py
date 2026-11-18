"""Validation review: session completeness, missing-session explanations, roll dates, no look-ahead."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.marketdata.store import upsert_candles
from app.research.backtest import BacktestParams, simulate
from app.research.bars import Bar
from app.research.data_audit import required_rth_bars, roll_dates
from app.services import history

CLOCK = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)  # Saturday


def test_required_regular_hours_follow_the_exchange_calendar() -> None:
    assert required_rth_bars("ES", date(2026, 11, 10)) == 300  # normal Tuesday
    assert required_rth_bars("ES", date(2026, 11, 27)) == int(300 * 210 / 390)  # 13:00 ET early close
    assert required_rth_bars("ES", date(2026, 11, 14)) == 0  # Saturday: no session


def test_quarterly_roll_dates() -> None:
    assert roll_dates("ES", date(2026, 8, 1), date(2026, 12, 31)) == ["2026-09-10", "2026-12-10"]
    assert roll_dates("MNQ", date(2026, 10, 1), date(2026, 11, 30)) == []  # none in Oct-Nov
    assert roll_dates("CL", date(2026, 1, 1), date(2026, 12, 31)) == []  # monthly contracts: not computed


async def _store(symbol: str, start: datetime, end: datetime) -> None:
    c = await SimulatedProvider(clock=lambda: end).fetch_candles(symbol, start, end)
    async with get_sessionmaker()() as s:
        await upsert_candles(s, c)
        await s.commit()


async def test_session_with_broken_regular_hours_is_incomplete_for_backfill_too() -> None:
    """Regression: the backfill counted >= 200 bars of any hour as complete, so a session with a full
    overnight but a broken regular session was never repaired while the study rejected it."""
    get_settings().market_data_provider = "simulated"
    # 2026-11-10: overnight 18:00-09:00 ET (900 bars) only, no regular hours.
    await _store("ES", datetime(2026, 11, 9, 23, 0, tzinfo=UTC), datetime(2026, 11, 10, 14, 0, tzinfo=UTC))
    async with get_sessionmaker()() as s:
        cov = await history.coverage(s, "ES", now=CLOCK, target=5)
        why = {w["date"]: w for w in await history.explain_missing(s, "ES", target=5, now=CLOCK)}
    assert "2026-11-10" in cov["missing_sessions"]
    w = why["2026-11-10"]
    assert w["rth_bars"] == 0 and w["bars"] >= 800 and w["rth_required"] == 300
    assert w["reason"].startswith("partially stored") and w["session_et"] == ["11-09 18:00", "11-10 17:00"]
    assert why["2026-11-12"]["reason"] == "not downloaded yet"


async def test_explanations_use_the_download_record_and_job_errors() -> None:
    get_settings().market_data_provider = "simulated"
    await history.start_job(["ES"], 5, 0.0, "t", plan={"ES": ["2026-11-10", "2026-11-11"]})
    job = await history.get_job()
    assert job is not None
    job["progress"] = {"ES": {"attempted": ["2026-11-10", "2026-11-11"]}}
    job["errors"] = ["ES 2026-11-10: Databento download failed: ReadTimeout after 60s"]
    await history._save_job(job)
    await get_redis().sadd(f"{history.FETCHED_KEY}simulated:ES", "2026-11-12")
    async with get_sessionmaker()() as s:
        why = {w["date"]: w["reason"] for w in await history.explain_missing(s, "ES", target=5, now=CLOCK)}
    assert why["2026-11-10"].startswith("download failed: Databento download failed: ReadTimeout")
    assert why["2026-11-11"].startswith("download started but not completed")
    assert why["2026-11-12"].startswith("downloaded, but the provider returned only 0")


async def test_partial_sessions_need_explicit_inclusion_to_be_re_requested() -> None:
    get_settings().market_data_provider = "simulated"
    await get_redis().sadd(f"{history.FETCHED_KEY}simulated:ES", "2026-11-12")
    async with get_sessionmaker()() as s:
        normal = await history.estimate(s, ["ES"], 5)
        incl = await history.estimate(s, ["ES"], 5, include_partial=True)
    assert "2026-11-12" not in normal["items"][0]["missing_sessions"]
    assert "2026-11-12" in incl["items"][0]["missing_sessions"]


async def test_no_look_ahead_trades_do_not_depend_on_later_bars() -> None:
    """Prefix invariance: every trade closed before T is identical whether or not bars after T exist."""
    end = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)
    c = await SimulatedProvider(clock=lambda: end).fetch_candles("ES", end - timedelta(days=9), end)
    bars = [Bar(x.ts, float(x.open), float(x.high), float(x.low), float(x.close), x.volume) for x in c]
    cut = datetime(2026, 11, 11, 18, 0, tzinfo=UTC)  # Wednesday 13:00 ET
    for sid in ("vwap_trend", "momentum_pullback", "breakout_retest", "orb"):
        full = simulate(BacktestParams(strategy_id=sid, symbol="ES"), bars)
        pre = simulate(BacktestParams(strategy_id=sid, symbol="ES"), [b for b in bars if b.ts < cut])
        closed = [t for t in full if datetime.fromisoformat(t.exit_ts) < cut - timedelta(minutes=1)]
        key = lambda t: (t.signal_ts, t.entry_ts, t.entry, t.stop, t.target, t.exit, t.exit_reason, t.pnl)  # noqa: E731
        assert [key(t) for t in closed] == [key(t) for t in pre[: len(closed)]], sid
