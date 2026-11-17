"""Trade Review & Replay HTTP 500 regression: valid, plan-linked, missing, malformed, no-data trades."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.marketdata.calendar import trading_day
from app.models.research import JournalTrade, TradePlan
from app.services.trade_review import plan_signal

T0 = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)  # 10:00 ET


async def add(source: str = "csv", plan: TradePlan | None = None, **kw: object) -> int:
    async with get_sessionmaker()() as s:
        if plan is not None:
            s.add(plan)
            await s.flush()
        t = JournalTrade(
            source_key=f"{source}:{kw.get('key', 'k')}",
            source=source,
            trading_date=trading_day(T0),
            symbol=kw.get("symbol", "MES"),
            contract=kw.get("contract", "MESZ6"),
            direction="long",
            quantity=1,
            entry_time=T0,
            exit_time=T0 + timedelta(minutes=5),
            entry_price=5000.0,
            exit_price=5004.0,
            gross_pnl=20.0,
            fees=1.2,
            net_pnl=18.8,
            mistakes=[],
            plan_id=plan.id if plan else None,
        )
        s.add(t)
        await s.commit()
        return t.id


def label_plan(thesis: str | None, signal: dict | None) -> TradePlan:
    snap = {"label": "BUY" if signal else "WAIT", "reasons": ["x"], "quality": "live", "signal": signal}
    return TradePlan(
        symbol="MES",
        direction="long",
        stop=4996,
        target=5008,
        quantity=1,
        status="executed_manual",
        execution="manual",
        thesis=thesis,
        created_at=T0 - timedelta(minutes=5),
        signal_snapshot=snap,
    )


def test_plan_signal_normalises_both_snapshot_shapes() -> None:
    assert plan_signal(None) is None
    assert plan_signal({"label": "WAIT", "reasons": [], "quality": "live", "signal": None}) is None
    s = plan_signal(
        {
            "label": "BUY",
            "quality": "live",
            "signal": {
                "strategy_id": "orb",
                "entry": 1.0,
                "stop": 0.5,
                "targets": [2.0],
                "bar_ts": "2026-11-11T14:55:00+00:00",
            },
        }
    )
    assert s == {
        "label": "BUY",
        "strategy_id": "orb",
        "entry": 1.0,
        "stop": 0.5,
        "targets": [2.0],
        "detected_at": "2026-11-11T14:55:00+00:00",
        "quality": "live",
    }
    flat = {"label": "SHORT", "strategy_id": "vwap_trend", "detected_at": "x"}
    assert plan_signal(flat) is flat


async def test_plan_without_thesis_no_longer_returns_500(admin_client: httpx.AsyncClient) -> None:
    """The original failure: a CSV trade linked to a terminal plan whose thesis was left blank."""
    get_settings().market_data_provider = "databento"
    tid = await add(plan=label_plan(None, None))
    r = await admin_client.get(f"/api/journal/trades/{tid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review_error"] is None and body["review"]["journal"]["entry_analysis"]
    assert body["source"] == "csv" and body["broker_record"] is True and body["simulated"] is False


async def test_plan_with_active_signal_records_strategy(admin_client: httpx.AsyncClient) -> None:
    get_settings().market_data_provider = "databento"
    sig = {
        "id": 1,
        "strategy_id": "orb",
        "direction": "long",
        "entry": 5000.0,
        "stop": 4996.0,
        "targets": [5008.0],
        "score": 50,
        "bar_ts": (T0 - timedelta(minutes=10)).isoformat(),
    }
    tid = await add(plan=label_plan(None, sig))
    body = (await admin_client.get(f"/api/journal/trades/{tid}")).json()
    assert "orb" in body["review"]["journal"]["entry_analysis"]
    assert any(i["kind"] == "signal" for i in body["review"]["timeline"])


async def test_each_source_is_labelled(admin_client: httpx.AsyncClient) -> None:
    get_settings().market_data_provider = "databento"
    for src, sim, broker in (("paper", True, False), ("manual", False, False), ("csv", False, True)):
        tid = await add(source=src, key=src)
        body = (await admin_client.get(f"/api/journal/trades/{tid}")).json()
        assert (body["simulated"], body["broker_record"]) == (sim, broker)
        assert {"paper": "simulated (paper)", "manual": "manual entry", "csv": "imported broker record"}[src] in body[
            "review"
        ]["journal"]["trade_summary"]


async def test_missing_and_malformed_ids(admin_client: httpx.AsyncClient) -> None:
    r = await admin_client.get("/api/journal/trades/987654")
    assert r.status_code == 404 and "not found" in r.json()["detail"]
    assert (await admin_client.get("/api/journal/trades/987654/replay")).status_code == 404
    for bad in ("abc", "0", "-3", "99999999999", "1.5"):
        assert (await admin_client.get(f"/api/journal/trades/{bad}")).status_code == 422, bad
        assert (await admin_client.get(f"/api/journal/trades/{bad}/replay")).status_code == 422, bad


async def test_review_and_replay_without_any_candles_explain_missing_data(admin_client: httpx.AsyncClient) -> None:
    get_settings().market_data_provider = "databento"  # no bars stored at all
    tid = await add(symbol="MNQ", contract="MNQZ6", key="nodata")
    body = (await admin_client.get(f"/api/journal/trades/{tid}")).json()
    assert body["review"] is not None and body["mfe"] is None
    rep = (await admin_client.get(f"/api/journal/trades/{tid}/replay")).json()
    assert rep["bars"] == [] and rep["data_available"] is False
    assert "historical replay data is missing" in rep["data_note"]


async def test_unexpected_review_failure_returns_trade_not_500(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.routes.terminal as term

    async def boom(*a: object, **k: object) -> None:
        raise RuntimeError("simulated analysis failure")

    tid = await add(key="boom")
    monkeypatch.setattr(term, "review_trade", boom)
    r = await admin_client.get(f"/api/journal/trades/{tid}")
    assert r.status_code == 200 and r.json()["review"] is None and "could not be generated" in r.json()["review_error"]
    assert (await admin_client.get(f"/api/journal/trades/{tid}/replay")).status_code == 200
    async with get_sessionmaker()() as s:  # the trade is never deleted by a failed repair
        assert await s.scalar(select(func.count()).select_from(JournalTrade).where(JournalTrade.id == tid)) == 1
