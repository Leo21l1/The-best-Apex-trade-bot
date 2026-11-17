"""Behaviour when dependencies fail: the API must degrade honestly, never crash or invent data."""

from __future__ import annotations

import httpx

from app.core import redis as redis_module
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.marketdata.status import market_data_component, read_feed_status
from app.models.signal import AlertRow
from app.schemas.system import ComponentState
from app.services.alerts import emit_alert


async def break_redis() -> None:
    await redis_module.close_redis()
    get_settings().redis_url = "redis://127.0.0.1:1/0"  # nothing listens here


async def test_api_keeps_serving_with_redis_down(admin_client: httpx.AsyncClient) -> None:
    await break_redis()
    try:
        # Rate limiting falls back to in-process counters; authenticated reads still work.
        assert (await admin_client.get("/api/auth/me")).status_code == 200
        ready = await admin_client.get("/api/health/ready")
        assert ready.status_code == 503 and ready.json()["checks"]["redis"] == "down"
        st = (await admin_client.get("/api/system/status")).json()
        comps = {c["name"]: c["state"] for c in st["components"]}
        assert comps["redis"] == "down" and comps["worker"] == "down" and st["overall"] == "down"
        radar = (await admin_client.get("/api/scanner/radar")).json()
        assert radar["symbols"] == [] and radar["quality"] == "unavailable"
        assert await read_feed_status() is None
        get_settings().market_data_provider = "simulated"
        assert (await market_data_component()).state == ComponentState.DOWN
        snap = (await admin_client.get("/api/market/snapshot")).json()
        assert snap["provenance"]["stale"] is True  # never presented as fresh
    finally:
        await redis_module.close_redis()


async def test_alerts_are_stored_even_when_redis_is_down(admin_client: httpx.AsyncClient) -> None:
    await break_redis()
    try:
        async with get_sessionmaker()() as s:
            row = await emit_alert(
                s,
                redis_module.get_redis(),
                kind="data_feed",
                severity="critical",
                title="t",
                body="b",
                dedupe_key="x",
                cooldown_s=60,
            )
            await s.commit()
        assert row is not None  # dedupe fails open so warnings are never lost
        alerts = (await admin_client.get("/api/alerts")).json()
        assert alerts["alerts"][0]["kind"] == "data_feed"
    finally:
        await redis_module.close_redis()
    async with get_sessionmaker()() as s:
        assert await s.get(AlertRow, row.id) is not None


async def test_unhandled_errors_return_generic_500_with_request_id(client: httpx.AsyncClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.api.routes import health

    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(health, "check_database", boom)
    r = await client.get("/api/health/ready", headers={"x-request-id": "trace-123"})
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error", "request_id": "trace-123"}
    assert "secret" not in r.text and r.headers["x-request-id"] == "trace-123"
