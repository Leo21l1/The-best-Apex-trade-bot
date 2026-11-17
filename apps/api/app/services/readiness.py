"""Readiness snapshot: session context + data status + checklist for one instrument, from stored data.

Used by the session API (charts, Smart Money Analysis), the AI copilot tool and trade review. All
inputs come from deterministic services; nothing here is estimated by a model.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redis import get_redis
from app.marketdata.calendar import ET
from app.marketdata.status import read_feed_status
from app.models.signal import SignalRow
from app.research.scanner import load_bars
from app.services.checklist import STYLE_WINDOWS, ChecklistConfig, evaluate
from app.services.session_intel import data_status, market_status, session_context

CACHE_KEY = "apex:readiness:"
CACHE_SECONDS = 15
HISTORY_DAYS = 16  # calendar days of 1m bars: >= 10 prior sessions for time-of-day baselines


def config(style: str = "intraday") -> ChecklistConfig:
    s = get_settings()
    return ChecklistConfig(
        style=style if style in STYLE_WINDOWS else "intraday",
        commission_per_side={
            "standard": s.cost_commission_per_side_standard,
            "micro": s.cost_commission_per_side_micro,
        },
        slippage_ticks_per_side=s.cost_slippage_ticks_per_side,
    )


def signal_setup(sig: SignalRow | None) -> dict[str, Any] | None:
    if sig is None:
        return None
    return {
        "strategy_id": sig.strategy_id,
        "direction": sig.direction,
        "entry": sig.entry,
        "entry_low": sig.entry_low,
        "entry_high": sig.entry_high,
        "stop": sig.stop,
        "targets": sig.targets,
        "detected_at": sig.bar_ts.isoformat(),
        "detected_at_et": f"{sig.bar_ts.astimezone(ET):%H:%M} ET",
        "conditions": [c.get("name") for c in (sig.detail or {}).get("conditions", [])][:8],
    }


async def active_signal(session: AsyncSession, provider: str, symbol: str, now: datetime) -> SignalRow | None:
    return await session.scalar(
        select(SignalRow)
        .where(
            SignalRow.symbol == symbol,
            SignalRow.provider == provider,
            SignalRow.status == "active",
            SignalRow.created_at <= now,
            SignalRow.created_at >= now - timedelta(minutes=30),
        )
        .order_by(SignalRow.created_at.desc())
        .limit(1)
    )


async def readiness(
    session: AsyncSession, symbol: str, style: str = "intraday", now: datetime | None = None, use_cache: bool = True
) -> dict[str, Any]:
    from app.api.routes.smc import _fresh_news
    from app.services.account import current_state
    from app.services.calendar import news_exposure

    now = now or datetime.now(UTC)
    s = get_settings()
    key = f"{CACHE_KEY}{symbol}:{style}"
    if use_cache:
        try:
            raw = await get_redis().get(key)
            if raw:
                return json.loads(raw)
        except (RedisError, OSError):
            pass
    ctx = session_context(symbol, now)
    provider = s.market_data_provider
    bars = (
        await load_bars(session, provider, symbol, now - timedelta(days=HISTORY_DAYS), now)
        if provider != "none"
        else []
    )
    feed = await read_feed_status() or {}
    # Without a worker heartbeat, classify stored bars by provider; their age then decides STALE.
    quality = (
        (feed.get("quality") or ("simulated" if provider == "simulated" else "historical"))
        if provider != "none"
        else "unavailable"
    )
    market = market_status(symbol, now)
    data = data_status(quality, bars[-1].ts if bars else None, now, market["open"])
    data.update(provider=provider, last_bar=bars[-1].ts.isoformat() if bars else None)
    news = await news_exposure(session, symbol, now, s.calendar_pre_event_minutes, s.calendar_post_event_minutes)
    fresh = await _fresh_news(session, symbol, now)
    if fresh:
        news = {
            "status": "inside_window",
            "summary": "Fresh published news: " + "; ".join(n.title[:80] for n in fresh),
            "events": news.get("events", []),
        }
    _rules, st = await current_state(session, now)
    risk = {
        "known": st.known,
        "risk_budget": float(st.risk_budget) if st.risk_budget is not None else None,
        "daily_loss_remaining": float(st.daily_loss_remaining) if st.daily_loss_remaining is not None else None,
        "drawdown_room": float(st.drawdown_room) if st.drawdown_room is not None else None,
        "rules_verified": st.rules_verified,
        "stale": st.stale,
    }
    setup = signal_setup(await active_signal(session, provider, symbol, now))
    checklist = evaluate(symbol, now, bars, config(style), setup, data, news, risk)
    out = {
        "session": ctx,
        "data": data,
        "news": news,
        "risk": risk,
        "setup": setup,
        "checklist": checklist,
        "liquidity_note": "Order-book liquidity is not known: no licensed depth-of-market feed is connected. "
        "Volume and range are proxies only.",
    }
    if use_cache:
        try:
            await get_redis().set(key, json.dumps(out, default=str), ex=CACHE_SECONDS)
        except (RedisError, OSError):
            pass
    return out
