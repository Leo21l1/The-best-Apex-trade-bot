from __future__ import annotations

import json
import time

import httpx

from app.core.redis import get_redis
from app.schemas.system import ComponentState, ComponentStatus
from app.services.health import WORKER_HEARTBEAT_KEY, check_worker, overall_state
from app.workers.main import heartbeat_once


async def test_liveness(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "x-request-id" in r.headers


async def test_readiness_reports_db_and_redis(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/health/ready")
    assert r.status_code == 200
    assert r.json()["checks"] == {"database": "ok", "redis": "ok"}


async def test_system_status_requires_auth(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/system/status")).status_code == 401


async def test_system_status_is_honest(admin_client: httpx.AsyncClient) -> None:
    r = await admin_client.get("/api/system/status")
    assert r.status_code == 200
    body = r.json()
    assert body["execution_mode"] == "ANALYSIS_ONLY"
    assert body["live_order_routing"] is False
    names = [c["name"] for c in body["components"]]
    assert len(names) == len(set(names)), "each component is reported exactly once"
    assert body["market_data_quality"] == "unavailable" and body["market_data_provider"] is None
    comps = {c["name"]: c for c in body["components"]}
    # Nothing that is not built may claim to be OK.
    assert comps["market_data"]["state"] == "not_configured"
    assert comps["apex_rule_profile"]["state"] == "degraded"
    assert "15 of 15 required rules unverified" in comps["apex_rule_profile"]["detail"]
    assert comps["tradovate_readonly"]["state"] == "not_configured"
    # No worker running in tests -> worker down -> overall degraded.
    assert comps["worker"]["state"] == "down"
    assert body["overall"] == "degraded"


async def test_worker_heartbeat_detected_and_staleness() -> None:
    await heartbeat_once()
    assert (await check_worker()).state == ComponentState.OK
    await get_redis().set(WORKER_HEARTBEAT_KEY, json.dumps({"ts": time.time() - 3600, "pid": 1}))
    stale = await check_worker()
    assert stale.state == ComponentState.DEGRADED
    assert "stale" in stale.detail
    await get_redis().set(WORKER_HEARTBEAT_KEY, "not-json")
    assert (await check_worker()).state == ComponentState.DEGRADED


def _c(name: str, state: ComponentState) -> ComponentStatus:
    from datetime import UTC, datetime

    return ComponentStatus(name=name, state=state, detail="", checked_at=datetime.now(UTC))


def test_overall_state_rules() -> None:
    ok, down, nc = ComponentState.OK, ComponentState.DOWN, ComponentState.NOT_CONFIGURED
    assert overall_state([_c("database", ok), _c("redis", ok), _c("market_data", nc)]) == ok
    assert overall_state([_c("database", down), _c("redis", ok)]) == down
    assert overall_state([_c("database", ok), _c("worker", down)]) == ComponentState.DEGRADED


async def test_readiness_fails_when_database_unreachable(client: httpx.AsyncClient) -> None:
    from app.core import db
    from app.core.config import get_settings

    await db.dispose_engine()
    get_settings().database_url = "postgresql+asyncpg://apex:wrong@localhost:1/none"
    try:
        r = await client.get("/api/health/ready")
        assert r.status_code == 503
        assert r.json()["checks"]["database"] == "down"
    finally:
        await db.dispose_engine()
