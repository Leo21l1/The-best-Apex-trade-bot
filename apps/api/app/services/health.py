"""Component health checks used by /health and the System Health dashboard."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.db import ping_database
from app.core.redis import get_redis
from app.schemas.system import ComponentState, ComponentStatus, SystemStatus

WORKER_HEARTBEAT_KEY = "apex:worker:heartbeat"
CHECK_TIMEOUT_S = 3.0


def _now() -> datetime:
    return datetime.now(UTC)


async def check_database() -> ComponentStatus:
    start = time.perf_counter()
    try:
        await asyncio.wait_for(ping_database(), CHECK_TIMEOUT_S)
        return ComponentStatus(
            name="database",
            state=ComponentState.OK,
            detail="PostgreSQL reachable",
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
            checked_at=_now(),
        )
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        return ComponentStatus(
            name="database",
            state=ComponentState.DOWN,
            detail=f"PostgreSQL unreachable ({type(exc).__name__})",
            checked_at=_now(),
        )


async def check_redis() -> ComponentStatus:
    start = time.perf_counter()
    try:
        await asyncio.wait_for(get_redis().ping(), CHECK_TIMEOUT_S)
        return ComponentStatus(
            name="redis",
            state=ComponentState.OK,
            detail="Redis reachable",
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
            checked_at=_now(),
        )
    except (RedisError, OSError, TimeoutError) as exc:
        return ComponentStatus(
            name="redis",
            state=ComponentState.DOWN,
            detail=f"Redis unreachable ({type(exc).__name__})",
            checked_at=_now(),
        )


async def check_worker() -> ComponentStatus:
    stale_after = get_settings().worker_heartbeat_stale_seconds
    try:
        raw = await asyncio.wait_for(get_redis().get(WORKER_HEARTBEAT_KEY), CHECK_TIMEOUT_S)
    except (RedisError, OSError, TimeoutError):
        return ComponentStatus(
            name="worker",
            state=ComponentState.DOWN,
            detail="Cannot read heartbeat (Redis down)",
            checked_at=_now(),
        )
    if not raw:
        return ComponentStatus(
            name="worker",
            state=ComponentState.DOWN,
            detail="No worker heartbeat found",
            checked_at=_now(),
        )
    try:
        beat = json.loads(raw)
        age = time.time() - float(beat["ts"])
    except (ValueError, KeyError, TypeError):
        return ComponentStatus(
            name="worker",
            state=ComponentState.DEGRADED,
            detail="Malformed heartbeat",
            checked_at=_now(),
        )
    if age > stale_after:
        return ComponentStatus(
            name="worker",
            state=ComponentState.DEGRADED,
            detail=f"Heartbeat stale ({age:.0f}s old)",
            checked_at=_now(),
        )
    return ComponentStatus(
        name="worker",
        state=ComponentState.OK,
        detail=f"Heartbeat {age:.1f}s ago (pid {beat.get('pid')})",
        checked_at=_now(),
    )


def static_components() -> list[ComponentStatus]:
    """Integrations not yet built are reported honestly rather than hidden."""
    s = get_settings()
    now = _now()
    return [
        ComponentStatus(
            name="market_data",
            state=ComponentState.NOT_CONFIGURED,
            detail="No licensed market data provider connected. No prices shown.",
            checked_at=now,
        ),
        ComponentStatus(
            name="apex_rule_profile",
            state=ComponentState.NOT_CONFIGURED,
            detail="Rule profile not loaded. No trade can be labelled compliant.",
            checked_at=now,
        ),
        ComponentStatus(
            name="tradovate_readonly",
            state=ComponentState.NOT_CONFIGURED,
            detail="Read-only account connectivity not configured. Use manual entry / CSV import.",
            checked_at=now,
        ),
        ComponentStatus(
            name="ai_copilot",
            state=ComponentState.OK if s.anthropic_api_key else ComponentState.NOT_CONFIGURED,
            detail="Anthropic API key present (server-side only)"
            if s.anthropic_api_key
            else "APEX_ANTHROPIC_API_KEY not set",
            checked_at=now,
        ),
    ]


def overall_state(components: list[ComponentStatus]) -> ComponentState:
    core = {"database", "redis"}
    if any(c.state == ComponentState.DOWN and c.name in core for c in components):
        return ComponentState.DOWN
    if any(c.state in (ComponentState.DOWN, ComponentState.DEGRADED) for c in components):
        return ComponentState.DEGRADED
    return ComponentState.OK


async def system_status() -> SystemStatus:
    s = get_settings()
    db, redis, worker = await asyncio.gather(check_database(), check_redis(), check_worker())
    components = [db, redis, worker, *static_components()]
    return SystemStatus(
        app=s.app_name,
        version=s.version,
        environment=s.environment.value,
        execution_mode=s.execution_mode.value,
        live_order_routing=False,
        timezone=s.timezone,
        server_time=_now(),
        overall=overall_state(components),
        components=components,
    )
