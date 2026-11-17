"""News intelligence: published announcements from official feeds, source status, manual refresh."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Query
from redis.exceptions import RedisError
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.core.config import get_settings
from app.core.redis import get_redis
from app.models.calendar import NewsItem
from app.services.news import SOURCES, STATUS_KEY, as_dict, ingest

router = APIRouter(prefix="/news", tags=["news"])
IMPACT_ORDER = ["UNKNOWN", "LOW", "MODERATE", "HIGH", "CRITICAL"]


@router.get("")
async def list_news(
    _: CurrentUser,
    session: SessionDep,
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 72,
    min_impact: str = "LOW",
    symbol: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    rows = (
        await session.scalars(
            select(NewsItem)
            .where(NewsItem.published_at >= now - timedelta(hours=hours))
            .order_by(NewsItem.published_at.desc())
            .limit(500)
        )
    ).all()
    floor = IMPACT_ORDER.index(min_impact.upper()) if min_impact.upper() in IMPACT_ORDER else 1
    items = [
        as_dict(n, now)
        for n in rows
        if IMPACT_ORDER.index(n.impact) >= floor and (symbol is None or symbol.upper() in (n.instruments or []))
    ][:limit]
    # Collapse clusters: keep the first (most recent) item of each cluster, count the rest.
    seen: dict[str, dict[str, Any]] = {}
    for it in items:
        if it["cluster"] in seen:
            seen[it["cluster"]]["duplicates"] = seen[it["cluster"]].get("duplicates", 0) + 1
        else:
            seen[it["cluster"]] = it
    return {
        "items": list(seen.values()),
        "sources": await _status(),
        "breaking_feed": "Not connected: real-time breaking-news wires need a licensed provider.",
        "note": "Impact is a rule-based relevance class, not a forecast. Directional bias is never read from "
        "headline wording.",
    }


async def _status() -> list[dict[str, Any]]:
    try:
        raw = await get_redis().get(STATUS_KEY)
        st = json.loads(raw) if raw else {}
    except (RedisError, OSError, ValueError):
        st = {}
    enabled = set(get_settings().news_sources)
    return [
        {
            "key": s.key,
            "name": s.name,
            "url": s.url,
            "credibility": s.credibility,
            "enabled": s.key in enabled,
            **st.get(s.key, {"ok": None, "error": None}),
        }
        for s in SOURCES
    ]


@router.get("/status")
async def news_status(_: CurrentUser) -> list[dict[str, Any]]:
    return await _status()


@router.post("/refresh")
async def refresh(_: AdminUser, session: SessionDep) -> dict[str, Any]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        res = await ingest(session, get_redis(), client, datetime.now(UTC), get_settings().news_sources)
    await session.commit()
    return res
