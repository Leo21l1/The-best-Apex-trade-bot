"""Feed status and freshness as seen by the API (read from Redis + PostgreSQL)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.redis import get_redis
from app.marketdata.calendar import is_market_open
from app.marketdata.feed import STATUS_KEY
from app.schemas.system import ComponentState, ComponentStatus


async def read_feed_status() -> dict[str, Any] | None:
    try:
        raw = await get_redis().get(STATUS_KEY)
    except (RedisError, OSError):
        return None
    if not raw:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def symbol_freshness(
    last_event_iso: str | None, now: datetime, stale_seconds: int
) -> dict[str, Any]:
    """Age of the newest bar for a symbol. A bar opening at T covers [T, T+60s)."""
    if not last_event_iso:
        return {"last_bar": None, "age_seconds": None, "stale": True}
    last = datetime.fromisoformat(last_event_iso)
    age = max(0.0, (now - last).total_seconds())
    # While the market is closed an old last bar is expected, not stale.
    stale = is_market_open(now) and age > stale_seconds + 60
    return {"last_bar": last.isoformat(), "age_seconds": round(age, 1), "stale": stale}


async def market_data_component() -> ComponentStatus:
    s = get_settings()
    now = datetime.now(UTC)
    if s.market_data_provider == "none":
        return ComponentStatus(
            name="market_data",
            state=ComponentState.NOT_CONFIGURED,
            detail="No market data provider configured. No prices shown.",
            checked_at=now,
        )
    doc = await read_feed_status()
    if doc is None or time.time() - float(doc.get("updated_at", 0)) > 60:
        return ComponentStatus(
            name="market_data",
            state=ComponentState.DOWN,
            detail=f"Provider '{s.market_data_provider}' configured but the ingestion worker "
            "is not reporting.",
            checked_at=now,
        )
    label = f"{doc['provider']} ({str(doc['quality']).upper()})"
    state = doc.get("state")
    if state == "auth_error":
        return ComponentStatus(
            name="market_data",
            state=ComponentState.DOWN,
            detail=f"{label}: credentials rejected or missing",
            checked_at=now,
        )
    if state in ("reconnecting", "starting", "backfilling", "stopped"):
        return ComponentStatus(
            name="market_data",
            state=ComponentState.DEGRADED,
            detail=f"{label}: {state}",
            checked_at=now,
        )
    fresh = [
        symbol_freshness(doc.get("last_event", {}).get(sym), now, s.market_data_stale_seconds)
        for sym in doc.get("symbols", [])
    ]
    stale = sum(1 for f in fresh if f["stale"])
    if stale:
        return ComponentStatus(
            name="market_data",
            state=ComponentState.DEGRADED,
            detail=f"{label}: {stale}/{len(fresh)} symbols stale",
            checked_at=now,
        )
    suffix = "" if doc.get("verified") else " — adapter not verified against live service"
    if doc.get("quality") == "simulated":
        suffix = " — synthetic prices, not real market data"
    closed = "" if is_market_open(now) else " (market closed)"
    return ComponentStatus(
        name="market_data",
        state=ComponentState.OK,
        detail=f"{label}: streaming{closed}{suffix}",
        checked_at=now,
    )


async def current_quality() -> tuple[str, str | None]:
    """(quality, provider) of data being actively ingested; "unavailable" otherwise."""
    s = get_settings()
    if s.market_data_provider == "none":
        return "unavailable", None
    doc = await read_feed_status()
    if doc is None or time.time() - float(doc.get("updated_at", 0)) > 60:
        return "unavailable", s.market_data_provider
    if doc.get("state") != "streaming":
        return "unavailable", s.market_data_provider
    return str(doc["quality"]), s.market_data_provider
