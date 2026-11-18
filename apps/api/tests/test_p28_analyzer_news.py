"""Market analyzer, news intelligence (60 s evaluation), ForexFactory rate limiting and journal fills."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.marketdata.store import upsert_candles
from app.models.calendar import EconomicEvent, SourceSyncState
from app.research.bars import Bar
from app.services import analyzer, news_intel
from app.services.calendar import FF_LOCK, guarded_sync_forexfactory
from tests.conftest import CSRF

NOW = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)  # Wednesday 10:00 ET


async def _event(title: str, ts: datetime, importance: str = "high", uid: str | None = None) -> None:
    async with get_sessionmaker()() as session:
        session.add(
            EconomicEvent(
                uid=uid or f"t:{title}:{ts.isoformat()}",
                ts=ts,
                title=title,
                category="cpi",
                importance=importance,
                affected=["ES", "NQ", "MNQ"],
                country="USD",
                source="forexfactory",
                forecast="0.3%",
                previous="0.2%",
            )
        )
        await session.commit()


async def _fresh_sync(at: datetime) -> None:
    async with get_sessionmaker()() as session:
        session.add(SourceSyncState(source="forexfactory", last_success_at=at, failures=0, events=10))
        await session.commit()


# --------------------------------------------------------------------------------------- news


async def test_news_status_windows_in_eastern_time() -> None:
    s = get_settings()
    s.calendar_forexfactory = True
    await _fresh_sync(NOW - timedelta(minutes=30))
    cpi = NOW + timedelta(minutes=40)
    await _event("CPI m/m", cpi)
    async with get_sessionmaker()() as session:
        doc = await news_intel.evaluate(session, NOW, "ES")
        assert doc["status"] == news_intel.UPCOMING
        nxt = doc["next_high_impact"]
        assert nxt["time_et"] == "Wed Nov 18 10:40 ET" and nxt["forecast"] == "0.3%" and nxt["actual"] is None
        assert nxt["actual_note"] == "Not supplied by the source."
        assert (await news_intel.evaluate(session, cpi - timedelta(minutes=10), "ES"))["status"] == news_intel.ACTIVE
        assert (await news_intel.evaluate(session, cpi + timedelta(minutes=3), "ES"))["status"] == news_intel.ACTIVE
        assert (await news_intel.evaluate(session, cpi + timedelta(minutes=10), "ES"))["status"] == news_intel.POST
        assert (await news_intel.evaluate(session, cpi + timedelta(minutes=40), "ES"))["status"] == news_intel.CLEAR
        assert (await news_intel.evaluate(session, NOW - timedelta(hours=3), "ES"))["status"] == news_intel.CLEAR
        assert doc["today"][0]["date_et"] == "2026-11-18" and "breaking-news" in doc["freshness"]["note"]


async def test_dst_boundary_event_time_is_converted_correctly() -> None:
    await _fresh_sync(datetime(2026, 3, 9, 12, 0, tzinfo=UTC))
    ts = datetime(2026, 3, 9, 12, 30, tzinfo=UTC)  # first Monday after the DST change: 08:30 EDT
    await _event("Payrolls", ts)
    async with get_sessionmaker()() as session:
        doc = await news_intel.evaluate(session, datetime(2026, 3, 9, 12, 0, tzinfo=UTC), "ES")
    assert doc["next_high_impact"]["time_et"] == "Mon Mar 09 08:30 ET"


async def test_stale_or_missing_calendar_is_reported_never_hidden() -> None:
    get_settings().calendar_forexfactory = True
    async with get_sessionmaker()() as session:
        empty = await news_intel.evaluate(session, NOW, "ES")
    assert empty["status"] == news_intel.STALE and "No economic calendar" in empty["summary"]
    await _event("CPI", NOW + timedelta(minutes=30))
    await _fresh_sync(NOW - timedelta(hours=7))  # old download: the event is still shown, status is stale
    async with get_sessionmaker()() as session:
        doc = await news_intel.evaluate(session, NOW, "ES")
    assert doc["status"] == news_intel.STALE and doc["freshness"]["stale"] and doc["next_high_impact"]


class _Client:
    def __init__(self, response: httpx.Response) -> None:
        self.response, self.calls = response, 0

    async def get(self, url: str) -> httpx.Response:
        self.calls += 1
        self.response.request = httpx.Request("GET", url)
        return self.response


async def test_forexfactory_download_is_scheduled_locked_and_backs_off() -> None:
    s = get_settings()
    payload = [
        {
            "title": "CPI m/m",
            "country": "USD",
            "date": "2026-11-19T08:30:00-05:00",
            "impact": "High",
            "forecast": "0.3%",
            "previous": "0.2%",
        }
    ]
    ok = _Client(httpx.Response(200, json=payload))
    redis = get_redis()
    await redis.delete(FF_LOCK)
    async with get_sessionmaker()() as session:
        out = await guarded_sync_forexfactory(session, redis, ok, s, NOW)
        assert out["synced"] and ok.calls == 1
        again = await guarded_sync_forexfactory(session, redis, ok, s, NOW + timedelta(minutes=10))
        assert again["skipped"] == "not due" and ok.calls == 1  # hourly schedule survives in the database
        ev = (await session.scalars(__import__("sqlalchemy").select(EconomicEvent))).one()
        assert (ev.forecast, ev.previous, ev.actual) == ("0.3%", "0.2%", None)
    # Another worker holds the lock: no download.
    await redis.set(FF_LOCK, "1", ex=60)
    async with get_sessionmaker()() as session:
        busy = await guarded_sync_forexfactory(session, redis, ok, s, NOW + timedelta(hours=2))
    assert busy["skipped"] == "another worker is syncing" and ok.calls == 1
    await redis.delete(FF_LOCK)
    # 429: honour Retry-After, keep the stored events, report the error.
    limited = _Client(httpx.Response(429, headers={"Retry-After": "1800"}))
    async with get_sessionmaker()() as session:
        res = await guarded_sync_forexfactory(session, redis, limited, s, NOW + timedelta(hours=2))
        assert res["synced"] is False and res["retry_in_s"] >= 1800
        st = await session.get(SourceSyncState, "forexfactory")
        assert st.failures == 1 and st.last_success_at == NOW and "429" in (st.last_error or "")
        skip = await guarded_sync_forexfactory(session, redis, limited, s, NOW + timedelta(hours=2, minutes=5))
        assert skip["skipped"] == "not due" and limited.calls == 1


# ----------------------------------------------------------------------------------- analyzer


async def _load(symbols: tuple[str, ...] = ("ES", "NQ")) -> None:
    get_settings().market_data_provider = "simulated"
    async with get_sessionmaker()() as session:
        for sym in symbols:
            c = await SimulatedProvider(clock=lambda: NOW).fetch_candles(sym, NOW - timedelta(days=3), NOW)
            await upsert_candles(session, c)
        await session.commit()


async def test_analyzer_snapshot_is_complete_and_mnq_unavailable_is_not_derived_from_nq() -> None:
    await _load(("ES", "NQ"))
    async with get_sessionmaker()() as session:
        es = await analyzer.snapshot(session, "ES", NOW, use_cache=False)
        mnq = await analyzer.snapshot(session, "MNQ", NOW, use_cache=False)
    assert es["data"]["label"] == "SIMULATED" and es["data"]["price"] is not None
    assert es["bias"]["direction"] in ("BULLISH", "BEARISH", "NEUTRAL", "WAIT")
    assert set(es["timeframes"]) == {"1m", "5m", "15m", "1h"}
    assert len(es["confluence"]["factors"]) == 12 and 0 <= es["confluence"]["score"] <= 100
    assert "not a win probability" in es["confluence"]["label"]
    assert set(es["strategies"]) == set(analyzer.FAMILIES)
    assert sum(len(v) for v in es["strategies"].values()) == 11
    assert es["narrative"] and len(es["observations"]) <= 5
    assert any(lv["short"] in ("PDH", "PDL") for lv in es["levels"] if lv["type"] == "liquidity")
    assert mnq["data"]["label"] == "UNAVAILABLE" and mnq["data"]["price"] is None
    assert mnq["bias"]["label"] == "WAIT / INSUFFICIENT DATA" and "never derived" in mnq["data"]["note"]


async def test_analyzer_is_causal_future_bars_do_not_change_past_analysis() -> None:
    await _load(("ES",))
    from app.research.scanner import load_bars

    async with get_sessionmaker()() as session:
        m1 = await load_bars(session, "simulated", "ES", NOW - timedelta(days=3), NOW)
    cut = NOW - timedelta(hours=2)
    past = [b for b in m1 if b.ts + timedelta(minutes=1) <= cut]
    a = analyzer.build("ES", past, [], cut, {"quality": "simulated"}, "simulated", None)
    # Same "now", but bars after it are present in the input: they must be ignored.
    b = analyzer.build("ES", m1, [], cut, {"quality": "simulated"}, "simulated", None)
    assert a["bias"] == b["bias"] and a["timeframes"] == b["timeframes"] and a["levels"] == b["levels"]
    assert a["data"]["price"] == b["data"]["price"] == past[-1].close


def test_strategy_status_classification() -> None:
    ev = [
        {"strategy_id": "mtf_trend", "triggered": True, "side": "long", "passed": 5, "total": 5, "conditions": []},
        {"strategy_id": "orb", "triggered": True, "side": "short", "passed": 4, "total": 4, "conditions": []},
        {
            "strategy_id": "vwap_trend",
            "triggered": False,
            "side": "long",
            "passed": 3,
            "total": 4,
            "conditions": [{"name": "Pullback to VWAP", "passed": False}],
        },
        {
            "strategy_id": "mean_reversion",
            "triggered": False,
            "side": None,
            "passed": 0,
            "total": 0,
            "note": "insufficient history",
        },
        {
            "strategy_id": "smc_ob_retest",
            "triggered": False,
            "side": "long",
            "passed": 1,
            "total": 4,
            "invalidated": True,
        },
    ]
    out = {r["id"]: r for rows in analyzer.strategy_status(ev, "bullish").values() for r in rows}
    assert out["mtf_trend"]["status"] == "Confirmed"
    assert out["orb"]["status"] == "Conflicting"
    assert out["vwap_trend"]["status"] == "Developing" and out["vwap_trend"]["missing"] == ["Pullback to VWAP"]
    assert out["mean_reversion"]["status"] == "Not applicable"
    assert out["smc_ob_retest"]["status"] == "Invalidated"
    assert out["sweep_reversal"]["status"] == "Not applicable"


def test_session_names_and_period_levels_use_only_completed_periods() -> None:
    et = lambda h, m=0: datetime(2026, 11, 18, h + 5, m, tzinfo=UTC)  # noqa: E731 (EST = UTC-5)
    assert analyzer.session_name(et(10)) == "New York morning"
    assert analyzer.session_name(et(12)) == "New York lunch"
    assert analyzer.session_name(et(14)) == "New York afternoon"
    assert analyzer.session_name(et(17, 30)) == "Market closed"
    d1 = [Bar(datetime(2026, 11, d, tzinfo=UTC), 100, 100 + d, 90 - d, 95, 1) for d in range(2, 19)]
    lv = {x["short"]: x["price"] for x in analyzer.period_levels([], d1, NOW)}
    assert lv["PDH"] == 117 and lv["PDL"] == 73  # Nov 17, not today's (Nov 18) developing bar
    assert lv["PWH"] == 115 and lv["PWL"] == 75  # bars dated Nov 9-15 (the completed previous week)


async def test_api_overview_three_cards_and_symbol_whitelist(admin_client: httpx.AsyncClient) -> None:
    await _load(("ES",))
    r = await admin_client.get("/api/analyzer")
    assert r.status_code == 200
    body = r.json()
    assert [c["symbol"] for c in body["cards"]] == ["ES", "NQ", "MNQ"] and "not independent" in body["note"]
    assert (await admin_client.get("/api/analyzer/CL")).status_code == 404
    d = (await admin_client.get("/api/analyzer/es")).json()
    assert d["symbol"] == "ES" and "_stamp" not in d
    n = (await admin_client.get("/api/news/intel")).json()
    assert n["evaluated_every_seconds"] == 60 and n["status"] in (news_intel.STALE, news_intel.CLEAR)


async def test_analysis_only_no_order_routes_on_analyzer(admin_client: httpx.AsyncClient) -> None:
    for path in ("/api/analyzer", "/api/analyzer/ES", "/api/news/intel"):
        assert (await admin_client.post(path, headers=CSRF)).status_code == 405


# ------------------------------------------------------------------------------- journal fills


async def test_manual_trade_with_partial_exits_and_fees(admin_client: httpx.AsyncClient) -> None:
    t = "2026-11-17T15:0{}:00+00:00"
    body: dict[str, Any] = {
        "symbol": "MNQ",
        "direction": "long",
        "fills": [
            {"time": t.format(0), "side": "entry", "qty": 2, "price": 20000.0},
            {"time": t.format(1), "side": "entry", "qty": 1, "price": 20003.0},
            {"time": t.format(5), "side": "exit", "qty": 2, "price": 20010.0},
            {"time": t.format(9), "side": "exit", "qty": 1, "price": 19995.0},
        ],
        "fees": 3.6,
        "planned_stop": 19990.0,
        "contract": "MNQZ6",
        "mistakes": ["moved stop"],
        "tags": ["opening drive"],
    }
    r = await admin_client.post("/api/journal/trades", json=body, headers=CSRF)
    assert r.status_code == 201, r.text
    d = r.json()
    # Entries 2@20000 + 1@20003 = 60003 cost; exits 2@20010 + 1@19995 = 60015 -> +12 points x $2 = $24 gross.
    assert d["quantity"] == 3 and d["gross_pnl"] == 24.0 and d["fees"] == 3.6 and d["net_pnl"] == 20.4
    assert d["entry_price"] == pytest.approx(20001.0) and d["exit_price"] == pytest.approx(20005.0)
    assert d["source"] == "manual" and len(d["fills"]) == 4 and d["contract"] == "MNQZ6"
    unbalanced = {**body, "fills": body["fills"][:3]}
    assert (await admin_client.post("/api/journal/trades", json=unbalanced, headers=CSRF)).status_code == 422
