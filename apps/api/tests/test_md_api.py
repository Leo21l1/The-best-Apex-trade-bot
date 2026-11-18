from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from starlette.testclient import TestClient

from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.main import create_app
from app.marketdata.calendar import floor_minute, is_market_open
from app.marketdata.feed import CANDLE_CHANNEL, STATUS_KEY, IngestionService
from app.marketdata.status import market_data_component, symbol_freshness
from app.schemas.system import ComponentState
from tests.conftest import ADMIN_EMAIL, STRONG_PASSWORD


async def _seed(symbols: tuple[str, ...] = ("ES", "NQ"), hours: int = 80) -> IngestionService:
    """Backfill simulated history up to the real current time and publish feed status.

    80 hours always reaches back past a weekend closure, so tests pass on any day.
    """
    s = get_settings()
    s.market_data_provider = "simulated"
    s.market_data_symbols = list(symbols)
    svc = IngestionService(SimulatedProvider(), symbols, get_sessionmaker(), get_redis(), backfill_hours=hours)
    await svc.backfill()
    await svc._set_state("streaming")
    return svc


async def test_market_endpoints_require_auth(client: httpx.AsyncClient) -> None:
    for path in (
        "/api/market/instruments",
        "/api/market/candles?symbol=ES",
        "/api/market/snapshot",
        "/api/market/status",
        "/api/market/gaps?symbol=ES",
    ):
        assert (await client.get(path)).status_code == 401, path


async def test_no_provider_means_no_prices(admin_client: httpx.AsyncClient) -> None:
    r = (await admin_client.get("/api/market/candles?symbol=ES&timeframe=5m")).json()
    assert r["bars"] == []
    assert r["provenance"]["quality"] == "unavailable" and r["provenance"]["provider"] is None
    snap = (await admin_client.get("/api/market/snapshot")).json()
    assert snap["provenance"]["quality"] == "unavailable"
    assert all(i["last"] is None for i in snap["items"])
    st = (await admin_client.get("/api/market/status")).json()
    assert st["state"] == "disabled" and st["reporting"] is False


async def test_instruments_list(admin_client: httpx.AsyncClient) -> None:
    r = (await admin_client.get("/api/market/instruments")).json()
    by = {i["symbol"]: i for i in r}
    assert len(r) == 12
    assert by["ES"]["tick_size"] == 0.25 and by["CL"]["tick_size"] == 0.01
    assert by["MES"]["sibling"] == "ES" and by["MES"]["is_micro"] is True


async def test_candles_carry_provenance_and_validate_input(
    admin_client: httpx.AsyncClient,
) -> None:
    await _seed()
    r = await admin_client.get("/api/market/candles?symbol=es&timeframe=5m&limit=50")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "ES" and body["timeframe"] == "5m"
    prov = body["provenance"]
    assert prov["provider"] == "simulated" and prov["quality"] == "simulated"
    assert prov["as_of"] and prov["last_bar"]
    bars = body["bars"]
    assert 0 < len(bars) <= 50
    assert [b["t"] for b in bars] == sorted(b["t"] for b in bars)
    assert all(b["l"] <= min(b["o"], b["c"]) and b["h"] >= max(b["o"], b["c"]) for b in bars)

    assert (await admin_client.get("/api/market/candles?symbol=ZZZ")).status_code == 404
    assert (await admin_client.get("/api/market/candles?symbol=ES&timeframe=7m")).status_code == 422
    assert (await admin_client.get("/api/market/candles?symbol=ES&limit=99999")).status_code == 422
    naive = await admin_client.get("/api/market/candles?symbol=ES&before=2026-01-01T00:00:00")
    assert naive.status_code == 422


async def test_snapshot_and_status_report_freshness(admin_client: httpx.AsyncClient) -> None:
    await _seed()
    snap = (await admin_client.get("/api/market/snapshot")).json()
    assert [i["symbol"] for i in snap["items"]] == ["ES", "NQ"]
    assert all(i["last"] and i["last_bar"] for i in snap["items"])
    st = (await admin_client.get("/api/market/status")).json()
    assert st["reporting"] is True and st["state"] == "streaming"
    assert st["quality"] == "simulated" and st["bars_stored"] > 0
    if is_market_open(datetime.now(UTC)):
        assert all(not s["stale"] for s in st["symbols"])


async def test_stored_bars_marked_stale_when_worker_stops_reporting(
    admin_client: httpx.AsyncClient,
) -> None:
    await _seed()
    await get_redis().delete(STATUS_KEY)  # worker died
    body = (await admin_client.get("/api/market/candles?symbol=ES")).json()
    assert body["bars"], "history is still served"
    assert body["provenance"]["stale"] is True
    assert "not reporting" in body["provenance"]["note"]
    snap = (await admin_client.get("/api/market/snapshot")).json()
    assert all(i["stale"] for i in snap["items"])
    st = (await admin_client.get("/api/market/status")).json()
    assert st["state"] == "not_reporting"
    comp = await market_data_component()
    assert comp.state == ComponentState.DOWN


async def test_gap_report_detects_deleted_bars(admin_client: httpx.AsyncClient) -> None:
    from sqlalchemy import delete, select

    from app.models.candle import CandleRow

    await _seed(("ES",), hours=80)
    clean = (await admin_client.get("/api/market/gaps?symbol=ES&hours=80")).json()
    assert clean["missing_bars"] == 0 and clean["present_bars"] == clean["expected_bars"] > 0

    async with get_sessionmaker()() as s:
        stored = set((await s.scalars(select(CandleRow.ts).where(CandleRow.symbol == "ES"))).all())
        # Anchor on a bar whose whole 40-minute look-back is stored, so no victim falls in the 17:00-18:00 ET
        # maintenance break (where no bar is expected). A plain max(ts) made this test fail just after 18:00 ET.
        last = max(t for t in stored if all(t - timedelta(minutes=m) in stored for m in range(41)))
        victims = [last - timedelta(minutes=m) for m in (10, 11, 12, 40)]
        await s.execute(delete(CandleRow).where(CandleRow.ts.in_(victims)))
        await s.commit()
    rep = (await admin_client.get("/api/market/gaps?symbol=ES&hours=80")).json()
    assert rep["missing_bars"] == 4
    assert sorted(g["missing_bars"] for g in rep["gaps"]) == [1, 3]
    assert "holidays" in rep["note"]


async def test_health_component_states() -> None:
    s = get_settings()
    assert (await market_data_component()).state == ComponentState.NOT_CONFIGURED
    s.market_data_provider = "simulated"
    assert (await market_data_component()).state == ComponentState.DOWN  # no worker
    now_iso = floor_minute(datetime.now(UTC)).isoformat()
    doc = {
        "provider": "simulated",
        "quality": "simulated",
        "verified": True,
        "state": "streaming",
        "symbols": ["ES"],
        "last_event": {"ES": now_iso},
        "updated_at": time.time(),
    }
    await get_redis().set(STATUS_KEY, json.dumps(doc))
    ok = await market_data_component()
    assert ok.state == ComponentState.OK and "not real market data" in ok.detail
    for state, expected in (
        ("reconnecting", ComponentState.DEGRADED),
        ("auth_error", ComponentState.DOWN),
    ):
        await get_redis().set(STATUS_KEY, json.dumps({**doc, "state": state}))
        assert (await market_data_component()).state == expected
    # A status document that stopped updating means the worker is gone.
    await get_redis().set(STATUS_KEY, json.dumps({**doc, "updated_at": time.time() - 600}))
    assert (await market_data_component()).state == ComponentState.DOWN


def test_symbol_freshness_staleness_rules() -> None:
    open_now = datetime(2026, 11, 11, 15, 30, tzinfo=UTC)  # Wed 10:30 ET
    fresh = symbol_freshness((open_now - timedelta(seconds=90)).isoformat(), open_now, 150)
    assert fresh["stale"] is False and fresh["age_seconds"] == 90
    old = symbol_freshness((open_now - timedelta(minutes=10)).isoformat(), open_now, 150)
    assert old["stale"] is True
    assert symbol_freshness(None, open_now, 150)["stale"] is True
    # Saturday: an old last bar is expected while the market is closed.
    sat = datetime(2026, 11, 14, 15, 0, tzinfo=UTC)
    assert symbol_freshness((sat - timedelta(hours=17)).isoformat(), sat, 150)["stale"] is False


@pytest.mark.usefixtures("release_global_clients")
def test_websocket_relays_only_subscribed_symbols() -> None:
    import redis as sync_redis

    publisher = sync_redis.Redis.from_url(get_settings().redis_url)
    with TestClient(create_app()) as tc:
        r = tc.post("/api/auth/bootstrap", json={"email": ADMIN_EMAIL, "password": STRONG_PASSWORD})
        assert r.status_code == 201
        with tc.websocket_connect("/api/ws") as ws:
            assert ws.receive_json()["topic"] == "system.status"
            ws.send_json({"type": "subscribe", "topics": ["candles:ES", "candles:BOGUS", 42]})
            ack = ws.receive_json()
            assert ack["topic"] == "subscribed" and ack["data"]["topics"] == ["candles:ES"]

            def publish(symbol: str, close: float) -> None:
                publisher.publish(
                    CANDLE_CHANNEL,
                    json.dumps(
                        {
                            "symbol": symbol,
                            "timeframe": "1m",
                            "t": 1,
                            "o": 1,
                            "h": 1,
                            "l": 1,
                            "c": close,
                            "v": 1,
                            "closed": True,
                            "provider": "simulated",
                            "quality": "simulated",
                        }
                    ),
                )

            # Wait until the relay's Redis subscription is active.
            deadline = time.time() + 5
            while publisher.pubsub_numsub(CANDLE_CHANNEL)[0][1] < 1 and time.time() < deadline:
                time.sleep(0.02)
            publish("NQ", 111.0)  # not subscribed: must not arrive
            publisher.publish(CANDLE_CHANNEL, "not json")  # malformed: ignored
            publish("ES", 222.0)
            frame = ws.receive_json()
            while frame["topic"] == "system.status":
                frame = ws.receive_json()
            assert frame["topic"] == "candles:ES" and frame["data"]["c"] == 222.0
            assert frame["data"]["quality"] == "simulated"
    publisher.close()
