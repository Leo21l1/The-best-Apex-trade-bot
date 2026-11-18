"""Morning preflight: honest PASS / WARN / FAIL from the running stack (read-only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.marketdata.store import upsert_candles
from app.services.preflight import run_preflight
from tests.conftest import CSRF


def by(rep: dict) -> dict[str, dict]:
    return {c["check"]: c for c in rep["checks"]}


async def test_preflight_blocks_without_live_data_snapshot_or_calendar() -> None:
    get_settings().market_data_provider = "simulated"
    now = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)  # Wed 10:00 ET, market open
    c = await SimulatedProvider(clock=lambda: now - timedelta(hours=1)).fetch_candles(
        "ES", now - timedelta(hours=3), now - timedelta(hours=1)
    )
    async with get_sessionmaker()() as s:
        await upsert_candles(s, c)
        await s.commit()
        rep = await run_preflight(s, ["ES", "NQ", "MNQ"], now)
    b = by(rep)
    assert rep["ok"] is False
    assert b["PostgreSQL"]["result"] == "PASS" and b["Worker"]["result"] == "FAIL"
    assert b["ES data"]["result"] == "FAIL" and "STALE" in b["ES data"]["detail"]  # an hour old while open
    assert b["NQ data"]["result"] == "FAIL" and b["News calendar"]["result"] == "FAIL"
    assert "UNAVAILABLE" in b["News calendar"]["detail"] or "STALE" in b["News calendar"]["detail"]
    # MNQ is checked on its own and never derived from NQ.
    assert b["MNQ data"]["result"] == "FAIL" and "never derived" in b["MNQ data"]["detail"]
    assert b["ES contract"]["result"] == "PASS" and "tick 0.25 = $12.50, point $50" in b["ES contract"]["detail"]
    # A stale or missing account snapshot is informational: it never blocks market analysis.
    assert b["Account snapshot"]["result"] == "INFO" and b["Apex rule profile"]["result"] == "WARN"
    assert b["Order execution"]["result"] == "PASS" and b["Analysis-only mode"]["result"] == "PASS"


async def test_preflight_account_snapshot_fresh_passes(admin_client: httpx.AsyncClient) -> None:
    get_settings().market_data_provider = "simulated"
    r = await admin_client.post("/api/account/snapshots", json={"balance": "50000.00"}, headers=CSRF)
    assert r.status_code == 201
    async with get_sessionmaker()() as s:
        rep = await run_preflight(s, ["ES"])
    assert by(rep)["Account snapshot"]["result"] == "INFO" and "fresh" in by(rep)["Account snapshot"]["detail"]


async def test_preflight_calendar_freshness_uses_last_successful_download() -> None:
    """Regression: freshness came from event insert times, which upserts never update."""
    from app.models.calendar import EconomicEvent, SourceSyncState

    get_settings().market_data_provider = "simulated"
    get_settings().calendar_forexfactory = True
    now = datetime(2026, 11, 18, 18, 0, tzinfo=UTC)
    async with get_sessionmaker()() as s:
        s.add(
            EconomicEvent(
                uid="x",
                ts=now + timedelta(hours=20),
                title="CPI",
                category="cpi",
                importance="high",
                affected=["ES"],
                country="USD",
                source="forexfactory",
                created_at=now - timedelta(hours=19),
            )
        )  # inserted long ago...
        s.add(SourceSyncState(source="forexfactory", last_success_at=now - timedelta(minutes=20), failures=0, events=1))
        await s.commit()
        fresh = by(await run_preflight(s, ["ES"], now))["News calendar"]  # ...but downloaded 20 min ago
        assert fresh["result"] == "PASS" and "12:40 ET" in fresh["detail"]
        st = await s.get(SourceSyncState, "forexfactory")
        st.last_success_at = now - timedelta(hours=19)
        st.last_error = "HTTPStatusError: 429"
        await s.commit()
        stale = by(await run_preflight(s, ["ES"], now))["News calendar"]
    assert stale["result"] == "FAIL" and stale["detail"].startswith("STALE") and "429" in stale["detail"]


async def _bars(sym: str, prices: list[float], vols: list[int], provider: str = "databento") -> None:
    from decimal import Decimal

    from app.models.candle import CandleRow

    t0 = datetime(2026, 11, 18, 14, 30, tzinfo=UTC)
    async with get_sessionmaker()() as s:
        for i, (p, v) in enumerate(zip(prices, vols, strict=True)):
            d = Decimal(str(p))
            s.add(
                CandleRow(
                    provider=provider,
                    symbol=sym,
                    ts=t0 + timedelta(minutes=i),
                    open=d,
                    high=d + 1,
                    low=d - 1,
                    close=d,
                    volume=v,
                    quality="live",
                )
            )
        await s.commit()


FEED = {
    "symbols": ["ES", "NQ", "MNQ"],
    "contracts": {
        "NQ": {"contract": "NQZ6", "instrument_id": 42005347},
        "MNQ": {"contract": "MNQZ6", "instrument_id": 42004823},
    },
    "last_event": {"MNQ": "2026-11-18T15:19:00+00:00"},
}


async def test_mnq_identical_prices_to_nq_are_legitimate() -> None:
    from app.services.preflight import _symbol_spec

    px = [21000 + i * 0.25 for i in range(40)]
    await _bars("NQ", px, [500 + i for i in range(40)])
    await _bars("MNQ", px, [3000 + 7 * i for i in range(40)])  # same index, same prices, own volume
    async with get_sessionmaker()() as s:
        r = await _symbol_spec(s, "databento", "MNQ", "MNQZ6", FEED)
        unmapped = await _symbol_spec(s, "databento", "MNQ", None, {"symbols": ["MNQ"], "contracts": {}})
    assert r["result"] == "PASS", r
    assert unmapped["result"] == "WARN" and "has not reported" in unmapped["detail"]
    assert "tick 0.25 = $0.50, point $2" in r["detail"] and "instrument_id 42004823" in r["detail"]


async def test_mnq_copied_records_or_shared_instrument_fail() -> None:
    from app.services.preflight import _symbol_spec

    px = [21000 + i * 0.25 for i in range(40)]
    vol = [500 + i for i in range(40)]
    await _bars("NQ", px, vol)
    await _bars("MNQ", px, vol)  # prices AND volumes identical on every bar: copied NQ records
    async with get_sessionmaker()() as s:
        copied = await _symbol_spec(s, "databento", "MNQ", "MNQZ6", FEED)
        assert copied["result"] == "FAIL" and "copied" in copied["detail"]
        shared = {
            **FEED,
            "contracts": {
                "NQ": {"contract": "NQZ6", "instrument_id": 1},
                "MNQ": {"contract": "MNQZ6", "instrument_id": 1},
            },
        }
        r = await _symbol_spec(s, "databento", "MNQ", "MNQZ6", shared)
        assert r["result"] == "FAIL" and "same as NQ" in r["detail"]
        wrong = await _symbol_spec(s, "databento", "MNQ", "NQZ6", FEED)
        assert wrong["result"] == "FAIL" and "is not a MNQ contract" in wrong["detail"]


def test_mnq_unavailable_states_the_actual_cause() -> None:
    from app.services.preflight import unavailable_cause

    assert "add it to APEX_MARKET_DATA_SYMBOLS" in unavailable_cause("MNQ", "databento", {"symbols": ["ES", "NQ"]})
    c = unavailable_cause("MNQ", "databento", {"symbols": ["MNQ"], "contracts": {}, "provider_last_error": "403"})
    assert "has not mapped MNQ.v.0" in c and "403" in c
    assert "no market-data provider" in unavailable_cause("MNQ", "none", {})
