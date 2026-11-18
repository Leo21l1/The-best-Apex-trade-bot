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
        rep = await run_preflight(s, ["ES", "NQ"], now)
    b = by(rep)
    assert rep["ok"] is False
    assert b["PostgreSQL"]["result"] == "PASS" and b["Worker"]["result"] == "FAIL"
    assert b["ES data"]["result"] == "FAIL" and "STALE" in b["ES data"]["detail"]  # an hour old while open
    assert b["NQ data"]["result"] == "FAIL" and b["News calendar"]["result"] == "FAIL"
    assert b["Account snapshot"]["result"] == "FAIL" and b["Apex rule profile"]["result"] == "WARN"
    assert b["Order execution"]["result"] == "PASS" and b["Evidence gate"]["result"] == "PASS"


async def test_preflight_account_snapshot_fresh_passes(admin_client: httpx.AsyncClient) -> None:
    get_settings().market_data_provider = "simulated"
    r = await admin_client.post("/api/account/snapshots", json={"balance": "50000.00"}, headers=CSRF)
    assert r.status_code == 201
    async with get_sessionmaker()() as s:
        rep = await run_preflight(s, ["ES"])
    assert by(rep)["Account snapshot"]["result"] == "PASS"
