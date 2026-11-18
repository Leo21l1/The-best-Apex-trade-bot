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
