"""Alert service: deduplication, cooldowns, history and realtime fan-out."""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log_event
from app.models.signal import AlertRow

log = logging.getLogger("apex.alerts")
ALERT_CHANNEL = "apex:alerts"
ALERT_KINDS = {
    "opportunity_detected",
    "trade_assistant",
    "setup_invalidated",
    "regime_changed",
    "data_feed",
    "data_stale",
    "economic_event",
    "risk_threshold",
    "account_stale",
    "strategy_degraded",
    "entry_reached",
    "news",
}


def alert_to_wire(a: AlertRow) -> dict[str, Any]:
    return {
        "id": a.id,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "kind": a.kind,
        "severity": a.severity,
        "title": a.title,
        "body": a.body,
        "symbol": a.symbol,
        "data": a.data,
        "read": a.read_at is not None,
    }


async def emit_alert(
    session: AsyncSession,
    redis: Redis,
    *,
    kind: str,
    severity: str,
    title: str,
    body: str,
    dedupe_key: str,
    cooldown_s: int,
    symbol: str | None = None,
    data: dict[str, Any] | None = None,
) -> AlertRow | None:
    """Create an alert unless one with the same ``dedupe_key`` fired within ``cooldown_s``.

    Returns the stored alert, or None when suppressed. If Redis is unavailable the alert is
    still stored (deduplication fails open so that warnings are never silently lost).
    """
    if kind not in ALERT_KINDS:
        raise ValueError(f"unknown alert kind {kind!r}")
    if cooldown_s > 0:
        try:
            fresh = await redis.set(f"apex:alert:cd:{dedupe_key}", "1", nx=True, ex=cooldown_s)
            if not fresh:
                return None
        except (RedisError, OSError):
            log_event(log, "alert dedupe unavailable", level=logging.WARNING)
    row = AlertRow(
        kind=kind,
        severity=severity,
        title=title[:200],
        body=body[:1000],
        symbol=symbol,
        dedupe_key=dedupe_key[:200],
        data=data or {},
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    try:
        await redis.publish(ALERT_CHANNEL, json.dumps(alert_to_wire(row)))
    except (RedisError, OSError):
        log_event(log, "alert publish failed", level=logging.WARNING)
    return row
