"""Real-time advisory monitoring of open paper positions.

Raises alerts only. It never changes stops, targets or positions, in the simulator or anywhere else.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.marketdata.instruments import INSTRUMENTS
from app.marketdata.types import Timeframe
from app.models.research import PaperAccount, TradePlan
from app.paper.engine import PaperBook
from app.research.scanner import load_bars
from app.services.alerts import emit_alert
from app.services.calendar import events_between
from app.strategies.base import MarketContext


def assess(
    pos: dict[str, Any],
    symbol: str,
    last: float,
    plan: TradePlan | None,
    ctx_now: MarketContext | None,
    atr_at_entry: float | None,
    events: list[Any],
    now: datetime,
    open_risk_budget: float,
) -> list[dict[str, str]]:
    """Pure assessment of one open position -> list of advisory messages (code, severity, text)."""
    out: list[dict[str, str]] = []
    s = 1 if pos["qty"] > 0 else -1
    side = "bullish" if s == 1 else "bearish"
    pv = float(INSTRUMENTS[symbol].point_value)
    stop = plan.stop if plan else None
    target = plan.target if plan else None
    if ctx_now is not None:
        t5, t15 = ctx_now.trend(Timeframe.M5), ctx_now.trend(Timeframe.M15)
        if t5 == -s and t15 == -s:
            out.append(
                {
                    "code": "thesis",
                    "severity": "warning",
                    "text": f"Your original {side} thesis may be weakening: 5m and 15m trends now point the other way.",
                }
            )
        a = ctx_now.atr14[-1] if ctx_now.m5 else None
        if a and atr_at_entry and a >= 1.5 * atr_at_entry:
            out.append(
                {
                    "code": "volatility",
                    "severity": "info",
                    "text": f"Current volatility is elevated relative to your entry conditions "
                    f"(5m ATR {a:.2f} vs {atr_at_entry:.2f} at entry).",
                }
            )
    for e in events:
        if (
            symbol in (e.affected or [])
            and e.importance == "high"
            and timedelta(0) <= e.ts - now <= timedelta(minutes=15)
        ):
            out.append(
                {
                    "code": f"news:{e.id}",
                    "severity": "warning",
                    "text": f"High-impact economic news is approaching: {e.title} in "
                    f"{max(0, int((e.ts - now).total_seconds() // 60))} min.",
                }
            )
    if stop is not None:
        risk = abs(last - stop) * pv * abs(pos["qty"])
        if (last - stop) * s > 0 and risk > open_risk_budget > 0:
            out.append(
                {
                    "code": "risk",
                    "severity": "critical",
                    "text": f"This position exceeds your configured risk budget: ${risk:,.2f} to the stop vs "
                    f"${open_risk_budget:,.2f} remaining.",
                }
            )
        dist = abs(pos["avg_price"] - stop)
        if dist > 0 and (last - stop) * s <= 0.25 * dist:
            out.append(
                {
                    "code": "near_stop",
                    "severity": "warning",
                    "text": f"Price {last} is within 25% of the original invalidation level {stop}.",
                }
            )
    if target is not None:
        dist = abs(target - pos["avg_price"])
        if dist > 0 and (target - last) * s <= 0.25 * dist:
            out.append(
                {"code": "near_target", "severity": "info", "text": f"Price {last} is close to the target {target}."}
            )
    return out


async def assist(session: AsyncSession, redis: Redis, acct: PaperAccount, settings: Settings, now: datetime) -> int:
    book = PaperBook(acct.state)
    if not book.s["positions"]:
        return 0
    events = await events_between(session, now - timedelta(minutes=5), now + timedelta(minutes=20))
    summary = book.summary()
    budget = max(min(summary["drawdown_room"], summary["daily_loss_remaining"]), 0.0)
    raised = 0
    for symbol, pos in book.s["positions"].items():
        last = book.s["last_prices"].get(symbol)
        if last is None or not pos.get("opened_at"):
            continue
        plan = await session.get(TradePlan, pos["plan_id"]) if pos.get("plan_id") else None
        bars = await load_bars(session, acct.provider, symbol, now - timedelta(hours=120), now)
        ctx_now = MarketContext(symbol, now, bars) if len(bars) > 300 else None
        opened = datetime.fromisoformat(pos["opened_at"])
        before = [b for b in bars if b.ts < opened]
        ctx_entry = MarketContext(symbol, opened, before) if len(before) > 300 else None
        atr_entry = ctx_entry.atr14[-1] if ctx_entry and ctx_entry.m5 else None
        for msg in assess(pos, symbol, last, plan, ctx_now, atr_entry, events, now, budget):
            row = await emit_alert(
                session,
                redis,
                kind="trade_assistant",
                severity=msg["severity"],
                symbol=symbol,
                title=f"[Paper] {symbol} position: {msg['text'].split(':')[0]}",
                body=msg["text"] + " Advisory only: nothing was changed.",
                dedupe_key=f"assist:{acct.id}:{symbol}:{pos['opened_at']}:{msg['code']}",
                cooldown_s=1800,
            )
            raised += row is not None
    return raised
