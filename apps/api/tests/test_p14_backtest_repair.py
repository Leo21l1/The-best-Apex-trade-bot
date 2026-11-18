"""Backtesting repair: the worker job loop survives failures, runs never stay stuck, results store."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.marketdata.store import upsert_candles
from app.models.research import BacktestRun, JournalTrade
from app.research.backtest import BacktestParams, Trade, attach_rvol, json_safe, metrics, run_backtest, simulate
from app.research.bars import Bar
from app.research.scanner import load_bars
from tests.conftest import CSRF

END = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)
START = END - timedelta(days=9)


async def seed() -> list[Bar]:
    get_settings().market_data_provider = "simulated"
    p = SimulatedProvider(clock=lambda: END)
    async with get_sessionmaker()() as s:
        await upsert_candles(s, await p.fetch_candles("ES", START, END))
        await s.commit()
        return await load_bars(s, "simulated", "ES", START, END)


async def queue(strategy: str = "vwap_trend") -> int:
    async with get_sessionmaker()() as s:
        r = BacktestRun(
            status="queued",
            strategy_id=strategy,
            symbol="ES",
            provider="simulated",
            start=START,
            end=END,
            params={"strategy_id": strategy, "symbol": "ES"},
        )
        s.add(r)
        await s.commit()
        return r.id


async def run_row(rid: int) -> BacktestRun:
    async with get_sessionmaker()() as s:
        r = await s.get(BacktestRun, rid)
        assert r is not None
        return r


async def test_failing_trade_review_no_longer_stops_backtests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Root cause: an exception from a journal review (e.g. KeyError) used to end the job loop."""
    import app.services.trade_review as tr
    from app.workers.main import REVIEW_SKIP_KEY, lab_cycle

    await seed()
    async with get_sessionmaker()() as s:
        t = JournalTrade(
            source_key="x",
            source="csv",
            trading_date=START.date(),
            symbol="ES",
            contract="ESZ6",
            direction="long",
            quantity=1,
            entry_time=START + timedelta(days=1),
            exit_time=START + timedelta(days=1, minutes=5),
            entry_price=5000.0,
            exit_price=5001.0,
            gross_pnl=50.0,
            fees=5.0,
            net_pnl=45.0,
            mistakes=[],
        )
        s.add(t)
        await s.commit()
        tid = t.id

    async def boom(*a: object, **k: object) -> None:
        raise KeyError("strategy_id")

    monkeypatch.setattr(tr, "review_trade", boom)
    sm = get_sessionmaker()
    assert await lab_cycle(sm) == "idle"  # the failing review is logged, not raised
    assert await get_redis().get(f"{REVIEW_SKIP_KEY}{tid}")  # and skipped for a while (no retry storm)
    rid = await queue()
    assert await lab_cycle(sm) == "backtest"
    r = await run_row(rid)
    assert r.status == "done" and r.result["metrics"]["trades"] >= 0 and r.error is None
    assert "segments" in r.result and "execution_policy" in r.result


async def test_unexpected_engine_error_fails_the_run_with_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.research as research

    await seed()
    rid = await queue()

    def crash(*a: object, **k: object) -> None:
        raise ZeroDivisionError("division by zero")

    monkeypatch.setattr(research, "run_backtest", crash)
    await research.execute_run(get_sessionmaker(), rid, "simulated", use_process=False)
    r = await run_row(rid)
    assert r.status == "failed" and r.error == "Internal error: ZeroDivisionError: division by zero"
    assert r.finished_at is not None


async def test_unstorable_result_is_marked_failed_not_left_running(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.research as research

    await seed()
    rid = await queue()
    monkeypatch.setattr(research, "run_backtest", lambda *a, **k: {"validation": {"passed": False}, "x": math.inf})
    await research.execute_run(get_sessionmaker(), rid, "simulated", use_process=False)
    r = await run_row(rid)
    assert r.status == "failed" and "Could not store the result" in r.error


def _t(pnl: float, day: str = "2026-11-10", **kw: object) -> Trade:
    base = dict(
        direction="long",
        signal_ts=f"{day}T15:00:00+00:00",
        entry_ts=f"{day}T15:01:00+00:00",
        exit_ts=f"{day}T15:10:00+00:00",
        signal_entry=1.0,
        entry=1.0,
        stop=0.5,
        target=2.0,
        exit=1.0,
        exit_reason="target",
        quantity=1,
        pnl=pnl,
        commission=0.0,
        r=pnl / 10,
        trading_day=day,
        bars_held=10,
    )
    base.update(kw)
    return Trade(**base)  # type: ignore[arg-type]


def test_all_winners_produce_json_safe_metrics() -> None:
    m = metrics([_t(10), _t(20)], 1, 100)
    assert m["profit_factor"] is None and m["no_losing_trades"] is True
    assert json_safe({"a": [math.inf, -math.inf, math.nan, 1.5]}) == {"a": [None, None, None, 1.5]}
    m2 = metrics([_t(10), _t(-5), _t(-5), _t(8), _t(9), _t(7)], 1, 100)
    assert m2["max_consecutive_losses"] == 2 and m2["max_consecutive_wins"] == 3 and m2["avg_hold_minutes"] == 10


def test_rvol_uses_only_prior_sessions() -> None:
    def bars_for(day: int, vol: int) -> list[Bar]:
        t0 = datetime(2026, 11, day, 15, 0, tzinfo=UTC)  # 10:00 ET
        return [Bar(t0 + timedelta(minutes=i), 1, 1, 1, 1, vol) for i in range(5)]

    bars: list[Bar] = []
    for d in (2, 3, 4, 5, 6, 9):
        bars += bars_for(d, 100)
    bars += bars_for(10, 300) + bars_for(11, 99999)  # a later day must not affect the 10th
    t = _t(5, day="2026-11-10", signal_ts="2026-11-10T15:05:00+00:00")
    attach_rvol([t], sorted(bars, key=lambda b: b.ts))
    assert t.rvol == 3.0
    early = _t(5, day="2026-11-03", signal_ts="2026-11-03T15:05:00+00:00")
    attach_rvol([early], bars)
    assert early.rvol is None  # fewer than 5 prior sessions


async def test_stop_atr_override_and_segments() -> None:
    bars = await seed()
    base = simulate(BacktestParams(strategy_id="vwap_trend", symbol="ES"), bars)
    alt = simulate(BacktestParams(strategy_id="vwap_trend", symbol="ES", stop_atr=1.0, target_r=(2.0, 3.0)), bars)
    assert alt and all(round(t.stop * 4) == t.stop * 4 and round(t.target * 4) == t.target * 4 for t in alt)
    assert all(abs(abs(t.target - t.signal_entry) / abs(t.signal_entry - t.stop) - 2.0) < 0.2 for t in alt)
    assert [t.stop for t in alt] != [t.stop for t in base[: len(alt)]]
    res = run_backtest(BacktestParams(strategy_id="vwap_trend", symbol="ES"), bars, "simulated", sensitivity=False)
    seg = res["segments"]
    assert set(seg["in_sample"]) >= {"session", "weekday", "regime", "relative_volume", "stop_distance"}
    assert sum(r["trades"] for r in seg["in_sample"]["session"]) == res["in_sample"]["trades"]
    assert all(t["session"] and t["weekday"] for t in res["trades"])


async def test_api_coverage_compare_progress(admin_client: httpx.AsyncClient) -> None:
    from app.services.research import execute_run

    await seed()
    cov = (
        await admin_client.get(
            "/api/backtests/coverage", params={"symbol": "ES", "start": START.isoformat(), "end": END.isoformat()}
        )
    ).json()
    assert cov["enough"] and cov["bars"] > 1000 and cov["sessions"] >= 5
    empty = await admin_client.post(
        "/api/backtests",
        json={
            "strategy_id": "vwap_trend",
            "symbol": "ES",
            "start": "2025-01-01T00:00:00Z",
            "end": "2025-01-05T00:00:00Z",
        },
        headers=CSRF,
    )
    assert empty.status_code == 409 and "stored 1-minute" in empty.json()["detail"]
    ok = (
        await admin_client.post(
            "/api/backtests",
            json={
                "strategy_id": "vwap_trend",
                "symbol": "ES",
                "start": START.isoformat(),
                "end": END.isoformat(),
                "stop_atr": 1.5,
            },
            headers=CSRF,
        )
    ).json()
    queued = (await admin_client.get("/api/backtests")).json()[0]
    assert queued["status"] == "queued" and "worker_note" in queued  # no worker in tests
    await execute_run(get_sessionmaker(), ok["id"], "simulated", use_process=False)
    done = (await admin_client.get(f"/api/backtests/{ok['id']}")).json()
    assert done["status"] == "done" and done["progress"]["phase"] == "done" and done["params"]["stop_atr"] == 1.5
    cmp = (await admin_client.get("/api/backtests/compare", params={"ids": f"{ok['id']}"})).json()
    assert cmp[0]["metrics"] is not None and cmp[0]["out_of_sample"] is not None
    assert (await admin_client.get("/api/backtests/0")).status_code == 422
    async with get_sessionmaker()() as s:
        assert (await s.scalars(select(BacktestRun.status))).all() == ["done"]


async def test_worker_exits_nonzero_when_a_long_running_task_crashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression (launch review): a crashed task (e.g. the live feed) left the worker running with a green
    heartbeat; now the worker stops and exits non-zero so Docker's restart policy brings it back."""
    import asyncio

    import app.workers.main as wm

    async def boom(stop: asyncio.Event) -> None:
        raise RuntimeError("feed task crashed")

    async def idle(stop: asyncio.Event) -> None:
        await stop.wait()

    class Scanner:
        def __init__(self, *a: object) -> None:
            pass

        async def run(self, stop: asyncio.Event) -> None:
            await stop.wait()

    monkeypatch.setattr(wm, "start_ingestion", lambda stop: asyncio.create_task(boom(stop)))
    for name in ("calendar_sync", "forexfactory_sync", "lab_jobs", "news_poll"):
        monkeypatch.setattr(wm, name, idle)
    monkeypatch.setattr(wm, "ScannerService", Scanner)
    monkeypatch.setattr(wm, "HEARTBEAT_INTERVAL_S", 0.05)
    monkeypatch.setattr(wm, "close_redis", lambda: asyncio.sleep(0))
    monkeypatch.setattr(wm, "dispose_engine", lambda: asyncio.sleep(0))
    wm.failed_task.clear()
    stop = asyncio.Event()
    await asyncio.wait_for(wm.run(stop), timeout=10)
    assert wm.failed_task == ["ingestion"] and stop.is_set()
    wm.failed_task.clear()
