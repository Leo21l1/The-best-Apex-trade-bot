"""Historical market intelligence: volatility profile and explainable regime classification.

Everything is computed from completed 1-minute bars up to ``now`` and compared with the SAME New
York clock time on prior sessions. Sample sizes are reported; nothing is called significant.
A regime describes conditions; it is not an entry signal.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, time, timedelta
from statistics import median
from typing import Any

from app.marketdata.calendar import ET, trading_day
from app.marketdata.types import Timeframe
from app.research.bars import Bar, aggregate, closed_only
from app.research.indicators import atr
from app.services.session_intel import efficiency_ratio

OR_START, OR_END = time(9, 30), time(9, 45)


def session_minute(ts: datetime) -> int:
    """Minutes since the 18:00 ET start of the CME trading day the timestamp belongs to.

    Comparing raw clock times is wrong for the evening part of a session: 18:30 ET is the start of
    the trading day, but as a clock time it sorts after 16:59 (the previous close).
    """
    local = ts.astimezone(ET)
    return (local.hour * 60 + local.minute - 18 * 60) % 1440


def _pct_rank(values: Sequence[float], x: float) -> int | None:
    return round(100 * sum(1 for v in values if v <= x) / len(values)) if values else None


def volatility_profile(
    bars_1m: Sequence[Bar], now: datetime, min_sessions: int = 5, lookback: int = 20
) -> dict[str, Any]:
    bars = [b for b in bars_1m if b.ts + timedelta(minutes=1) <= now]
    if len(bars) < 30:
        return {"available": False, "reason": "Fewer than 30 completed 1-minute bars."}
    today = trading_day(now)
    t_now = now.astimezone(ET).time()
    m_now = session_minute(now)
    by_day: dict[Any, list[Bar]] = {}
    for b in bars:
        by_day.setdefault(trading_day(b.ts), []).append(b)
    prior = sorted((d for d in by_day if d < today), reverse=True)[:lookback]
    b5 = closed_only(aggregate(bars, Timeframe.M5), Timeframe.M5, now)
    a14 = atr(b5, 14)
    cur_atr = a14[-1] if a14 else None
    a50 = atr(b5, 50)[-1] if len(b5) > 50 else None
    # ATR percentile at the same clock time on prior sessions (5m ATR at the last bar before that time)
    atr_ref: list[float] = []
    idx_by_day: dict[Any, int] = {}
    for i, b in enumerate(b5):
        d = trading_day(b.ts)
        if d in prior and session_minute(b.ts) + 5 <= m_now:  # 5m bar closed by the same session time
            idx_by_day[d] = i
    atr_ref = [a14[i] for i in idx_by_day.values() if a14[i]]

    def session_range(rows: list[Bar], until: int | None) -> float | None:
        sel = [b for b in rows if until is None or session_minute(b.ts) < until]
        return (max(b.high for b in sel) - min(b.low for b in sel)) if sel else None

    rng_now = session_range(by_day.get(today, []), None)
    rng_ref = [r for r in (session_range(by_day[d], m_now) for d in prior) if r]

    def or_size(rows: list[Bar]) -> float | None:
        sel = [b for b in rows if OR_START <= b.ts.astimezone(ET).time() < OR_END]
        return (max(b.high for b in sel) - min(b.low for b in sel)) if len(sel) >= 10 else None

    or_today = or_size(by_day.get(today, [])) if t_now >= OR_END else None
    or_ref = [x for x in (or_size(by_day[d]) for d in prior) if x]
    n = len(prior)
    out: dict[str, Any] = {
        "available": True,
        "sessions": n,
        "data_quality": "robust" if n >= 20 else "preliminary" if n >= min_sessions else "insufficient",
        "atr14_5m": round(cur_atr, 4) if cur_atr else None,
        "atr_percentile_same_time": _pct_rank(atr_ref, cur_atr) if cur_atr and len(atr_ref) >= min_sessions else None,
        "atr_ratio_14_50": round(cur_atr / a50, 2) if cur_atr and a50 else None,
        "session_range_so_far": round(rng_now, 4) if rng_now else None,
        "typical_range_by_now": round(median(rng_ref), 4) if len(rng_ref) >= min_sessions else None,
        "session_range_percentile": _pct_rank(rng_ref, rng_now) if rng_now and len(rng_ref) >= min_sessions else None,
        "opening_range": round(or_today, 4) if or_today else None,
        "typical_opening_range": round(median(or_ref), 4) if len(or_ref) >= min_sessions else None,
        "opening_range_percentile": _pct_rank(or_ref, or_today) if or_today and len(or_ref) >= min_sessions else None,
    }
    ratio = out["atr_ratio_14_50"]
    out["state"] = (
        "expansion"
        if ratio and ratio > 1.4
        else "compression"
        if ratio and ratio < 0.7
        else "normal"
        if ratio
        else "unknown"
    )
    p = out["atr_percentile_same_time"]
    out["level"] = (
        "unknown"
        if p is None
        else "unusually high"
        if p >= 90
        else "high"
        if p >= 70
        else "unusually low"
        if p <= 10
        else "low"
        if p <= 30
        else "normal"
    )
    if n < min_sessions:
        out["note"] = f"Only {n} prior sessions; percentiles need at least {min_sessions}."
    return out


def regime_profile(
    bars_1m: Sequence[Bar],
    now: datetime,
    structure_trend: str | None = None,
    recent_choch: bool = False,
    vwap_crosses_today: int | None = None,
    rvol: float | None = None,
) -> dict[str, Any]:
    """Deterministic labels with the evidence used. Several labels can hold at once."""
    b5 = closed_only(aggregate([b for b in bars_1m if b.ts < now], Timeframe.M5), Timeframe.M5, now)
    if len(b5) < 30:
        return {"labels": ["unknown"], "evidence": {"reason": "Fewer than 30 completed 5-minute bars."}}
    look = b5[-24:]  # two hours
    closes = [b.close for b in look]
    er = efficiency_ratio(closes) or 0.0
    hi = max(b.high for b in b5[-27:-3])
    lo = min(b.low for b in b5[-27:-3])
    last3 = b5[-3:]
    a14 = atr(b5, 14)[-1]
    a50 = atr(b5, 50)[-1] if len(b5) > 50 else None
    labels: list[str] = []
    up = closes[-1] > closes[0]
    if er >= 0.35 and structure_trend in (None, "bullish") and up:
        labels.append("bullish trend")
    elif er >= 0.35 and structure_trend in (None, "bearish") and not up:
        labels.append("bearish trend")
    if er < 0.15 or (vwap_crosses_today is not None and vwap_crosses_today >= 4):
        labels.append("choppy")
    elif er < 0.35:
        labels.append("sideways range")
    if any(b.close > hi for b in last3) or any(b.close < lo for b in last3):
        if rvol is None or rvol >= 1.2:
            labels.append("breakout")
    if recent_choch:
        labels.append("reversal candidate")
    if a14 and a50:
        if a14 / a50 > 1.4:
            labels.append("volatility expansion")
        elif a14 / a50 < 0.7:
            labels.append("volatility compression")
    return {
        "labels": labels or ["undetermined"],
        "evidence": {
            "efficiency_ratio_2h": round(er, 3),
            "net_change_2h": round(closes[-1] - closes[0], 4),
            "prior_2h_high": hi,
            "prior_2h_low": lo,
            "last_close": closes[-1],
            "structure_trend_5m": structure_trend,
            "change_of_character_recent": recent_choch,
            "vwap_crosses_today": vwap_crosses_today,
            "relative_volume": rvol,
            "atr_ratio_14_50": round(a14 / a50, 2) if a14 and a50 else None,
        },
        "definitions": {
            "trend": "efficiency ratio of 2h of 5m closes >= 0.35 in the direction of the 5m structure trend",
            "choppy": "efficiency ratio < 0.15 or >= 4 confirmed VWAP crosses today",
            "sideways range": "efficiency ratio 0.15-0.35",
            "breakout": "a close beyond the prior 2-hour high/low in the last 3 bars with relative volume >= 1.2 "
            "(or unknown)",
            "reversal candidate": "a change of character / market structure shift in the last 6 bars",
            "volatility expansion/compression": "ATR(14)/ATR(50) on 5m above 1.4 / below 0.7",
        },
        "note": "A regime describes recent conditions; it is not an entry signal.",
    }
