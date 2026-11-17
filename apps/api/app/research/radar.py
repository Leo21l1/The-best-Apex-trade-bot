"""Market radar: per-symbol, per-timeframe state computed from closed bars."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from app.marketdata.calendar import is_market_open
from app.marketdata.types import Timeframe
from app.research.indicators import atr, ema, is_rth, rsi, sma
from app.strategies.base import MarketContext

RADAR_TIMEFRAMES = tuple(Timeframe)
_NEED = 51  # bars required for EMA50 plus one


def timeframe_row(ctx: MarketContext, tf: Timeframe) -> dict[str, Any]:
    bars = ctx.bars(tf)
    row: dict[str, Any] = {
        "timeframe": tf.value,
        "bars": len(bars),
        "trend": None,
        "rsi": None,
        "atr_pct": None,
        "rel_volume": None,
        "close": None,
        "note": None,
    }
    if not bars:
        row["note"] = "no closed bars"
        return row
    closes = [b.close for b in bars]
    row["close"] = closes[-1]
    r, a = rsi(closes, 14)[-1], atr(bars, 14)[-1]
    row["rsi"] = None if r is None else round(r, 1)
    row["atr_pct"] = None if a is None else round(a / closes[-1] * 100, 3)
    vol = sma([float(b.volume) for b in bars], 20)
    if len(vol) >= 2 and vol[-2]:
        row["rel_volume"] = round(bars[-1].volume / vol[-2], 2)
    e20, e50 = ema(closes, 20)[-1], ema(closes, 50)[-1]
    if e20 is None or e50 is None:
        row["note"] = f"insufficient history for trend ({len(bars)} of {_NEED} bars)"
    else:
        row["trend"] = "up" if e20 > e50 else "down" if e20 < e50 else "flat"
    return row


def regime(ctx: MarketContext) -> tuple[str, str]:
    """(label, definition-based explanation). Labels: trend_up, trend_down, range, mixed, unknown."""
    t5, t15, t60 = (ctx.trend(tf) for tf in (Timeframe.M5, Timeframe.M15, Timeframe.H1))
    if None in (t5, t15, t60):
        return "unknown", "Insufficient history for EMA20/EMA50 on 5m, 15m and 1h."
    if t5 == t15 == t60 == 1:
        return "trend_up", "EMA20 > EMA50 on 5m, 15m and 1h."
    if t5 == t15 == t60 == -1:
        return "trend_down", "EMA20 < EMA50 on 5m, 15m and 1h."
    a, e20, e50 = ctx.atr14[-1], ctx.ema20[-1], ctx.ema50[-1]
    if a and e20 is not None and e50 is not None and abs(e20 - e50) <= 0.5 * a:
        return "range", "5m EMA20 and EMA50 within 0.5 ATR; timeframes disagree."
    return "mixed", "Trend direction differs between 5m, 15m and 1h."


def session_label(now: datetime) -> str:
    if not is_market_open(now):
        return "closed"
    return "regular" if is_rth(now) else "overnight"


def symbol_radar(ctx: MarketContext) -> dict[str, Any]:
    label, why = regime(ctx)
    v = ctx.vwap[0][-1] if ctx.m5 else None
    last = ctx.m5[-1].close if ctx.m5 else None
    lv = ctx.levels
    return {
        "symbol": ctx.symbol,
        "last": last,
        "last_bar": ctx.bars_1m[-1].ts.isoformat() if ctx.bars_1m else None,
        "regime": label,
        "regime_definition": why,
        "session": session_label(ctx.now),
        "vwap": None if v is None else round(v, 4),
        "vs_vwap": None if v is None or last is None else ("above" if last > v else "below"),
        "levels": asdict(lv),
        "timeframes": [timeframe_row(ctx, tf) for tf in RADAR_TIMEFRAMES],
    }
