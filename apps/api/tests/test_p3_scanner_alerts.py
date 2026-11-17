from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from starlette.testclient import TestClient

from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.main import create_app
from app.marketdata.feed import STATUS_KEY, IngestionService
from app.models.signal import AlertRow, SignalRow
from app.research.bars import Bar
from app.research.scanner import SCAN_KEY, ScannerService, signal_outcome
from app.services.alerts import ALERT_CHANNEL, emit_alert
from tests.conftest import ADMIN_EMAIL, STRONG_PASSWORD

# On the deterministic simulated tape ES prints a multi-timeframe trend signal at this bar.
NOW = datetime(2026, 11, 11, 16, 10, 5, tzinfo=UTC)


async def seed(now: datetime = NOW, symbols: tuple[str, ...] = ("ES",)) -> ScannerService:
    s = get_settings()
    s.market_data_provider = "simulated"
    s.market_data_symbols = list(symbols)
    s.scanner_lookback_hours = 120
    svc = IngestionService(
        SimulatedProvider(clock=lambda: now),
        symbols,
        get_sessionmaker(),
        get_redis(),
        backfill_hours=120,
        clock=lambda: now,
    )
    await svc.backfill()
    await svc._set_state("streaming")
    return ScannerService(s, get_sessionmaker(), get_redis(), clock=lambda: now)


async def count(model: type) -> int:
    async with get_sessionmaker()() as s:
        return int(await s.scalar(select(func.count()).select_from(model)) or 0)


async def test_scan_builds_radar_stores_signal_once_and_alerts() -> None:
    scanner = await seed()
    doc = await scanner.scan_once()
    assert doc["provider"] == "simulated" and doc["quality"] == "simulated"
    sym = doc["symbols"][0]
    assert sym["symbol"] == "ES" and sym["regime"] in {"trend_up", "trend_down", "range", "mixed"}
    assert [r["timeframe"] for r in sym["timeframes"]] == [
        "1m",
        "3m",
        "5m",
        "15m",
        "30m",
        "1h",
        "4h",
        "1d",
        "1w",
    ]
    by_tf = {r["timeframe"]: r for r in sym["timeframes"]}
    assert by_tf["5m"]["trend"] in {"up", "down", "flat"} and by_tf["5m"]["rsi"] is not None
    # Five days of history cannot support a weekly EMA50: reported, not invented.
    assert by_tf["1w"]["trend"] is None and "insufficient history" in by_tf["1w"]["note"]
    assert len(sym["evaluations"]) == 11
    assert sym["levels"]["prior_day_high"] is not None

    opps = doc["opportunities"]
    assert opps, "expected the deterministic tape to trigger at least one strategy"
    opp = opps[0]
    assert opp["rank_eligible"] is False and len(opp["rank_blockers"]) == 3
    assert opp["validation"]["validated"] is False and opp["validation"]["sample_size"] is None
    assert any("SIMULATED" in u for u in opp["uncertainty"])
    assert opp["loss_at_stop_after_costs"] == pytest.approx(
        opp["risk_per_contract"] + opp["estimated_cost_per_contract"]
    )
    assert all(c["passed"] for c in opp["conditions"])

    stored = await count(SignalRow)
    assert stored == len(opps)
    alerts_before = await count(AlertRow)
    assert alerts_before >= 1

    again = await scanner.scan_once()  # same bar: nothing new, no duplicate rows or alerts
    assert again["opportunities"] == []
    assert await count(SignalRow) == stored and await count(AlertRow) == alerts_before
    assert json.loads(await get_redis().get(SCAN_KEY))["as_of"] == NOW.isoformat()


async def test_scan_without_provider_or_feed_makes_no_signals() -> None:
    s = get_settings()
    scanner = ScannerService(s, get_sessionmaker(), get_redis(), clock=lambda: NOW)
    doc = await scanner.scan_once()
    assert doc["symbols"] == [] and "No market data provider" in doc["note"]
    assert await count(AlertRow) == 0

    scanner = await seed()
    await get_redis().delete(STATUS_KEY)  # ingestion stopped reporting
    doc = await scanner.scan_once()
    assert doc["symbols"] == [] and "not reporting" in doc["note"]
    assert await count(SignalRow) == 0  # never signal on data of unknown freshness
    async with get_sessionmaker()() as session:
        alert = await session.scalar(select(AlertRow))
    assert alert is not None and alert.kind == "data_feed" and alert.severity == "critical"
    await scanner.scan_once()
    assert await count(AlertRow) == 1  # cooldown suppresses the repeat


def _row(direction: str = "long") -> SignalRow:
    long = direction == "long"
    return SignalRow(
        direction=direction,
        bar_ts=datetime(2026, 11, 11, 15, 0, tzinfo=UTC),
        stop=95.0 if long else 105.0,
        targets=[110.0 if long else 90.0],
        entry=100.0,
    )


def _bar(minute: int, hi: float, lo: float) -> Bar:
    return Bar(datetime(2026, 11, 11, 15, minute, tzinfo=UTC), 100, hi, lo, 100, 1)


def test_signal_outcome_rules() -> None:
    soon = datetime(2026, 11, 11, 15, 20, tzinfo=UTC)
    assert signal_outcome(_row(), [_bar(6, 101, 99)], soon) is None
    assert signal_outcome(_row(), [_bar(6, 101, 94.75)], soon) == "invalidated"
    assert signal_outcome(_row(), [_bar(6, 110, 99)], soon) == "target_reached"
    # Stop and target inside one bar: assume the stop was hit first.
    assert signal_outcome(_row(), [_bar(6, 111, 94)], soon) == "invalidated"
    # Bars at or before the trigger bar do not count.
    assert signal_outcome(_row(), [_bar(4, 200, 1)], soon) is None
    assert signal_outcome(_row("short"), [_bar(6, 105, 99)], soon) == "invalidated"
    assert signal_outcome(_row("short"), [_bar(6, 101, 90)], soon) == "target_reached"
    late = datetime(2026, 11, 11, 16, 6, tzinfo=UTC)
    assert signal_outcome(_row(), [_bar(6, 101, 99)], late) == "expired"


async def test_active_signal_is_invalidated_and_alerted() -> None:
    scanner = await seed()
    await scanner.scan_once()
    async with get_sessionmaker()() as session:
        row = await session.scalar(select(SignalRow))
        assert row is not None and row.status == "active"
        # Backdate the trigger and put the stop at the entry price, which later bars traded through.
        row.bar_ts -= timedelta(minutes=30)
        row.stop = row.entry
        await session.commit()
        sid = row.id
    later = NOW
    await scanner.scan_once()
    async with get_sessionmaker()() as session:
        row = await session.get(SignalRow, sid)
        assert row is not None and row.status == "invalidated" and row.status_at == later
        kinds = (await session.scalars(select(AlertRow.kind))).all()
    assert "setup_invalidated" in kinds


async def test_alert_cooldown_dedupe_and_validation() -> None:
    redis = get_redis()
    async with get_sessionmaker()() as session:
        kw = dict(kind="data_stale", severity="warning", title="t", body="b", dedupe_key="k1")
        first = await emit_alert(session, redis, cooldown_s=60, **kw)
        second = await emit_alert(session, redis, cooldown_s=60, **kw)
        other = await emit_alert(session, redis, cooldown_s=60, **{**kw, "dedupe_key": "k2"})
        no_cd = await emit_alert(session, redis, cooldown_s=0, **kw)
        await session.commit()
        assert first is not None and second is None and other is not None and no_cd is not None
        with pytest.raises(ValueError, match="unknown alert kind"):
            await emit_alert(session, redis, cooldown_s=0, **{**kw, "kind": "place_order"})
    assert await count(AlertRow) == 3


async def test_scanner_api(admin_client: httpx.AsyncClient) -> None:
    empty = (await admin_client.get("/api/scanner/radar")).json()
    assert empty["symbols"] == [] and empty["quality"] == "unavailable"

    scanner = await seed()
    await scanner.scan_once()
    radar = (await admin_client.get("/api/scanner/radar")).json()
    assert radar["symbols"][0]["symbol"] == "ES" and "opportunities" not in radar

    # Signals were created "now" in wall-clock time, so the default 24h window includes them.
    opp = (await admin_client.get("/api/opportunities")).json()
    assert opp["ranked"] == []  # nothing is validated: the leaderboard ranks zero
    assert opp["candidates"] and "Not a probability" in opp["score_note"]
    cand = opp["candidates"][0]
    assert cand["status"] == "active" and cand["rank_blockers"]
    assert (await admin_client.get("/api/opportunities?symbol=NQ")).json()["candidates"] == []
    sid = cand["strategy_id"]
    filtered = (await admin_client.get(f"/api/opportunities?strategy_id={sid}")).json()
    assert all(c["strategy_id"] == sid for c in filtered["candidates"])

    strat = (await admin_client.get("/api/strategies")).json()
    assert len(strat["strategies"]) == 11
    assert all(s["validation"]["validated"] is False for s in strat["strategies"])
    assert strat["validation_standards"]["min_trades"] == 100

    alerts = (await admin_client.get("/api/alerts")).json()
    assert alerts["unread"] >= 1 and alerts["alerts"][0]["title"].startswith("[SIMULATED DATA]")
    aid = alerts["alerts"][0]["id"]
    csrf = {"X-Requested-With": "apex"}
    assert (await admin_client.post(f"/api/alerts/{aid}/read", headers=csrf)).status_code == 204
    assert (await admin_client.post("/api/alerts/999999/read", headers=csrf)).status_code == 404
    after = (await admin_client.get("/api/alerts")).json()
    assert after["unread"] == alerts["unread"] - 1
    assert (await admin_client.post("/api/alerts/read-all", headers=csrf)).status_code == 204
    assert (await admin_client.get("/api/alerts?unread_only=true")).json()["alerts"] == []


async def test_scanner_endpoints_require_auth(client: httpx.AsyncClient) -> None:
    for path in ("/api/scanner/radar", "/api/opportunities", "/api/strategies", "/api/alerts"):
        assert (await client.get(path)).status_code == 401, path
    assert (await client.post("/api/alerts/read-all")).status_code == 401


@pytest.mark.usefixtures("release_global_clients")
def test_websocket_pushes_alerts_to_every_client() -> None:
    import redis as sync_redis

    publisher = sync_redis.Redis.from_url(get_settings().redis_url)
    with TestClient(create_app()) as tc:
        tc.post("/api/auth/bootstrap", json={"email": ADMIN_EMAIL, "password": STRONG_PASSWORD})
        with tc.websocket_connect("/api/ws") as ws:
            assert ws.receive_json()["topic"] == "system.status"
            deadline = time.time() + 5
            while publisher.pubsub_numsub(ALERT_CHANNEL)[0][1] < 1 and time.time() < deadline:
                time.sleep(0.02)
            publisher.publish(ALERT_CHANNEL, json.dumps({"id": 1, "kind": "data_feed", "title": "x"}))
            frame = ws.receive_json()
            while frame["topic"] == "system.status":
                frame = ws.receive_json()
            assert frame["topic"] == "alerts" and frame["data"]["kind"] == "data_feed"
    publisher.close()
