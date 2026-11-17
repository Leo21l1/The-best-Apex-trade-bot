from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.marketdata.feed import IngestionService
from app.models.research import BacktestRun, JournalTrade
from app.services.journal import mistake_analysis, parse_trades_csv, root_symbol, rule_violations, stats, welch
from app.services.research import claim_next, execute_run, load_validations
from tests.conftest import CSRF

TRADOVATE = (
    "symbol,_priceFormat,_priceFormatType,_tickSize,buyFillId,sellFillId,qty,buyPrice,sellPrice,pnl,"
    "boughtTimestamp,soldTimestamp,duration\n"
    "MESZ6,-2,0,0.25,111,112,2,5000.25,5004.75,$45.00,11/10/2026 10:01:05,11/10/2026 10:15:30,14min 25sec\n"
    "NQZ6,-2,0,0.25,113,114,1,18010.00,18000.00,$(200.00),11/10/2026 11:20:00,11/10/2026 11:05:00,15min\n"
    "ZZZZ6,-2,0,0.25,115,116,1,1,2,$1.00,11/10/2026 11:20:00,11/10/2026 11:25:00,5min\n"
)
GENERIC = (
    "symbol,side,qty,entry_time,exit_time,entry_price,exit_price,fees\n"
    "ES,long,1,2026-11-11T10:00:00-05:00,2026-11-11T10:30:00-05:00,5000,5002,4.50\n"
    "CLZ26,short,2,2026-11-11 11:00,2026-11-11 11:20,70.50,70.25,\n"
)


def test_root_symbol() -> None:
    assert root_symbol("MESZ6") == "MES" and root_symbol("MESZ26") == "MES" and root_symbol("ES") == "ES"
    assert root_symbol("M2KH7") == "M2K" and root_symbol("GCG27") == "GC"
    assert root_symbol("AAPL") is None and root_symbol("ESA6") is None


def test_parse_tradovate_performance_export() -> None:
    r = parse_trades_csv(TRADOVATE, fee_per_side={"standard": 2.5, "micro": 0.6})
    assert r.layout == "tradovate_performance" and len(r.trades) == 2
    assert len(r.errors) == 1 and "unsupported symbol" in r.errors[0]
    mes, nq = r.trades
    assert (mes.symbol, mes.direction, mes.quantity, mes.entry_price, mes.exit_price) == (
        "MES",
        "long",
        2,
        5000.25,
        5004.75,
    )
    assert mes.gross_pnl == 45.0 and mes.fees == 2.4  # estimated 2 x 2 x $0.60
    assert mes.entry_time == datetime(2026, 11, 10, 15, 1, 5, tzinfo=UTC)  # 10:01:05 EST
    # Sold before bought: a short, entered at the sell price.
    assert (nq.direction, nq.entry_price, nq.exit_price, nq.gross_pnl) == ("short", 18000.0, 18010.0, -200.0)
    assert mes.source_key == "tv:111|112"


def test_parse_generic_layout_computes_pnl() -> None:
    r = parse_trades_csv(GENERIC)
    assert r.layout == "generic" and r.errors == []
    es, cl = r.trades
    assert es.gross_pnl == 100.0 and es.fees == 4.5
    assert cl.symbol == "CL" and cl.gross_pnl == pytest.approx(0.25 * 1000 * 2) and cl.fees == 10.0
    bad = parse_trades_csv("when,what\n1,2\n")
    assert bad.layout is None and "Unrecognised columns" in bad.errors[0]
    rev = parse_trades_csv(
        "symbol,side,qty,entry_time,exit_time,entry_price,exit_price\n"
        "ES,long,1,2026-11-11 10:30,2026-11-11 10:00,5000,5001\n"
    )
    assert "exit is before entry" in rev.errors[0]


def jt(
    pnl: float,
    mistakes: list[str] | None = None,
    qty: int = 1,
    symbol: str = "ES",
    day: int = 10,
    exit_hour: int = 15,
    **kw: object,
) -> JournalTrade:
    t = datetime(2026, 11, day, exit_hour, 0, tzinfo=UTC)
    return JournalTrade(
        id=kw.get("id", 1),
        source_key="k",
        source="manual",
        trading_date=date(2026, 11, day),
        symbol=symbol,
        direction="long",
        quantity=qty,
        entry_time=t - timedelta(minutes=10),
        exit_time=t,
        entry_price=5000,
        exit_price=5001,
        gross_pnl=pnl,
        fees=0,
        net_pnl=pnl,
        mistakes=mistakes or [],
        planned_stop=kw.get("planned_stop"),
    )


def test_stats_and_welch() -> None:
    s = stats([jt(100), jt(-50), jt(-50, day=11), jt(200, day=12)])
    assert s["net_pnl"] == 200 and s["win_rate"] == 0.5 and s["profit_factor"] == 3.0 and s["max_drawdown"] == 100
    w = welch([-100, -120, -90, -110], [50, 60, 40, 55])
    assert w["t"] < 0 and w["p"] < 0.05 and "small sample" in w["note"]
    assert welch([1], [2, 3])["t"] is None


def test_mistake_analysis_ranks_costly_tags() -> None:
    trades = [
        jt(-100, ["moved_stop"]),
        jt(-150, ["moved_stop"]),
        jt(-120, ["moved_stop"]),
        jt(80),
        jt(90),
        jt(60),
        jt(-30, ["early_exit"]),
        jt(70),
    ]
    rows = mistake_analysis(trades)
    assert rows[0]["tag"] == "moved_stop" and rows[0]["count"] == 3 and rows[0]["total_pnl"] == -370
    assert rows[0]["avg_pnl_with"] < rows[0]["avg_pnl_without"]
    early = next(r for r in rows if r["tag"] == "early_exit")
    assert early["welch"]["t"] is None  # one trade: no test, explicitly stated


def test_rule_violations_against_configured_values() -> None:
    from app.models.calendar import EconomicEvent

    trades = [jt(-600, id=1), jt(-500, id=2), jt(10, qty=7, id=3, day=11), jt(5, id=4, day=12, exit_hour=22)]
    cpi = EconomicEvent(
        uid="c",
        ts=datetime(2026, 11, 10, 14, 55, tzinfo=UTC),
        title="CPI",
        category="cpi",
        importance="high",
        affected=["ES"],
        source="manual",
    )
    v = rule_violations(trades, 6, 1000, [cpi], 15, 15)
    rules = sorted({x["rule"] for x in v})
    assert rules == ["contract limit", "daily loss limit", "held into session close", "open during news window"]
    assert sum(1 for x in v if x["rule"] == "daily loss limit") == 1  # reported once per day


async def test_journal_api_import_dedupe_edit_and_analytics(admin_client: httpx.AsyncClient) -> None:
    dry = (await admin_client.post("/api/journal/import", json={"csv": GENERIC, "dry_run": True}, headers=CSRF)).json()
    assert dry["parsed"] == 2 and dry["imported"] == 0
    first = (await admin_client.post("/api/journal/import", json={"csv": GENERIC}, headers=CSRF)).json()
    again = (await admin_client.post("/api/journal/import", json={"csv": GENERIC}, headers=CSRF)).json()
    assert first["imported"] == 2 and again["imported"] == 0 and again["duplicates"] == 2
    blocked = (await admin_client.post("/api/journal/import", json={"csv": TRADOVATE}, headers=CSRF)).json()
    assert blocked["imported"] == 0 and blocked["errors"]  # any bad row blocks the import
    data = (await admin_client.get("/api/journal/trades")).json()
    assert data["stats"]["trades"] == 2 and "moved_stop" in data["mistake_tags"]
    tid = data["trades"][0]["id"]
    r = await admin_client.patch(
        f"/api/journal/trades/{tid}",
        json={
            "strategy_id": "orb",
            "mistakes": ["moved_stop", "moved_stop"],
            "followed_plan": False,
            "notes": "late",
            "planned_stop": 4995,
        },
        headers=CSRF,
    )
    assert r.status_code == 200 and r.json()["mistakes"] == ["moved_stop"]
    assert (
        await admin_client.patch(f"/api/journal/trades/{tid}", json={"mistakes": ["bad_tag"]}, headers=CSRF)
    ).status_code == 422
    a = (await admin_client.get("/api/analytics?days=3650&period=month")).json()
    assert a["overall"]["trades"] == 2 and a["by_period"][0]["key"] == "2026-11"
    assert {g["key"] for g in a["by_strategy"]} == {"orb", "untagged"}
    assert a["mistakes"][0]["tag"] == "moved_stop" and a["planned_vs_actual"]["trades_with_plan"] == 1
    assert a["rules_verified"] is False and any("UNVERIFIED" in n for n in a["notes"])
    assert (await admin_client.delete(f"/api/journal/trades/{tid}", headers=CSRF)).status_code == 204


async def _seed_sim(now: datetime, days: int = 12) -> None:
    s = get_settings()
    s.market_data_provider = "simulated"
    s.market_data_symbols = ["ES"]
    svc = IngestionService(
        SimulatedProvider(clock=lambda: now),
        ["ES"],
        get_sessionmaker(),
        get_redis(),
        backfill_hours=days * 24,
        clock=lambda: now,
    )
    await svc.backfill()


async def test_backtest_api_queue_and_worker_execution(admin_client: httpx.AsyncClient) -> None:
    now = datetime(2026, 11, 13, 21, 0, tzinfo=UTC)
    assert (
        await admin_client.post(
            "/api/backtests",
            json={"strategy_id": "vwap_trend", "symbol": "ES", "start": "2026-11-01T00:00:00Z", "end": now.isoformat()},
            headers=CSRF,
        )
    ).status_code == 409  # no provider
    await _seed_sim(now)
    body = {
        "strategy_id": "vwap_trend",
        "symbol": "ES",
        "start": (now - timedelta(days=12)).isoformat(),
        "end": now.isoformat(),
    }
    for bad in (
        {**body, "symbol": "CL", "strategy_id": "orb"},
        {**body, "end": body["start"]},
        {**body, "start": "2026-11-01T00:00:00"},
        {**body, "strategy_id": "nope"},
    ):
        assert (await admin_client.post("/api/backtests", json=bad, headers=CSRF)).status_code == 422
    r = await admin_client.post("/api/backtests", json=body, headers=CSRF)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    async with get_sessionmaker()() as s:
        run = await claim_next(s)
    assert run is not None
    await execute_run(get_sessionmaker(), run.id, "simulated")
    detail = (await admin_client.get(f"/api/backtests/{run.id}")).json()
    assert detail["status"] == "done" and detail["validated"] is False
    assert detail["result"]["dataset"]["quality"] == "simulated" and detail["summary"]["trades"] >= 1
    assert (await admin_client.get("/api/backtests")).json()[0]["id"] == run.id
    # Simulated experiments never count as validation evidence.
    async with get_sessionmaker()() as s:
        assert await load_validations(s) == {}
    strat = (await admin_client.get("/api/strategies")).json()["strategies"]
    assert all(not x["validation"]["validated"] for x in strat)


async def test_backtest_on_too_little_data_fails_cleanly(admin_client: httpx.AsyncClient) -> None:
    now = datetime(2026, 11, 13, 21, 0, tzinfo=UTC)
    await _seed_sim(now, days=1)
    r = await admin_client.post(
        "/api/backtests",
        json={
            "strategy_id": "vwap_trend",
            "symbol": "ES",
            "start": (now - timedelta(hours=10)).isoformat(),
            "end": now.isoformat(),
        },
        headers=CSRF,
    )
    async with get_sessionmaker()() as s:
        run = await claim_next(s)
        assert run is not None and run.id == r.json()["id"]
    await execute_run(get_sessionmaker(), run.id, "simulated")
    async with get_sessionmaker()() as s:
        row = await s.get(BacktestRun, run.id)
    assert row is not None and row.status == "failed" and "need at least 1000" in (row.error or "")


async def test_validation_comes_from_real_data_experiments(admin_client: httpx.AsyncClient) -> None:
    passing = {
        "dataset": {"quality": "historical"},
        "metrics": {"trades": 150},
        "out_of_sample": {"expectancy_r": 0.21, "profit_factor": 1.4},
        "validation": {"passed": True, "checks": []},
        "sensitivity": [],
    }
    async with get_sessionmaker()() as s:
        s.add(
            BacktestRun(
                status="done",
                strategy_id="orb",
                symbol="ES",
                provider="databento",
                start=datetime(2026, 1, 1, tzinfo=UTC),
                end=datetime(2026, 6, 1, tzinfo=UTC),
                params={},
                result=passing,
                validated=True,
            )
        )
        await s.commit()
        v = await load_validations(s)
    assert v["orb"].validated and v["orb"].sample_size == 150 and v["orb"].expectancy_r == 0.21
    strat = {x["id"]: x for x in (await admin_client.get("/api/strategies")).json()["strategies"]}
    assert strat["orb"]["validation"]["validated"] is True and strat["vwap_trend"]["validation"]["validated"] is False


async def test_paper_api_end_to_end(admin_client: httpx.AsyncClient) -> None:
    from app.models.research import PaperAccount
    from app.services.paper import process

    assert (
        await admin_client.post(
            "/api/paper/orders", json={"symbol": "ES", "side": "buy", "type": "market", "qty": 1}, headers=CSRF
        )
    ).status_code == 409
    now = datetime.now(UTC)
    s = get_settings()
    s.market_data_provider = "simulated"
    s.market_data_symbols = ["ES"]
    st = (await admin_client.get("/api/paper")).json()
    assert st["isolated"] is True and "no connection to Apex" in st["notice"]
    assert st["summary"]["balance"] == 50000 and st["config"]["max_contracts"] == 6
    from app.marketdata.calendar import is_market_open

    r = await admin_client.post(
        "/api/paper/orders",
        json={"symbol": "ES", "side": "buy", "type": "limit", "qty": 1, "price": 5000.1},
        headers=CSRF,
    )
    if not is_market_open(now):
        assert r.json()["result"]["status"] == "rejected" and "market is closed" in r.json()["result"]["reason"]
        return
    assert r.json()["result"]["status"] == "rejected" and "tick size" in r.json()["result"]["reason"]
    # Submit a market order, then store the bar that opens after it and process.
    r = await admin_client.post(
        "/api/paper/orders", json={"symbol": "ES", "side": "buy", "type": "market", "qty": 1}, headers=CSRF
    )
    oid = r.json()["result"]["id"]
    later = now + timedelta(minutes=3)
    svc = IngestionService(
        SimulatedProvider(clock=lambda: later),
        ["ES"],
        get_sessionmaker(),
        get_redis(),
        backfill_hours=1,
        clock=lambda: later,
    )
    await svc.backfill()
    async with get_sessionmaker()() as session:
        acct = await session.scalar(select(PaperAccount))
        assert acct is not None
        await process(session, acct, later)
        await session.commit()
    st = (await admin_client.get("/api/paper")).json()
    order = next(o for o in st["orders"] if o["id"] == oid)
    assert order["status"] == "filled" and st["positions"][0]["qty"] == 1
    flat = (await admin_client.post("/api/paper/flatten", headers=CSRF)).json()
    assert flat["account"]["positions"] == [] and flat["account"]["closed"]
    trades = (await admin_client.get("/api/journal/trades?source=paper")).json()["trades"]
    assert len(trades) == 1 and trades[0]["source"] == "paper"
    reset = (await admin_client.post("/api/paper/reset", headers=CSRF)).json()
    assert reset["summary"]["balance"] == 50000 and reset["orders"] == []
