"""Stored-history audit for one instrument: coverage, sessions, gaps, integrity and provenance.

Answers "how much usable history do I really have?" from the candles table itself, before any
study is run. Read-only.
"""

from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.marketdata.calendar import trading_day
from app.marketdata.instruments import INSTRUMENTS
from app.marketdata.validation import find_gaps
from app.research.bars import Bar
from app.research.indicators import is_rth

#: A session is usable for strategy testing when it has this many regular-session (09:30-16:00 ET)
#: bars. A full regular session has 390.
MIN_RTH_BARS = 300
#: Data-sufficiency thresholds in usable sessions (the same 20-session standard as the baselines).
SUFFICIENT_SESSIONS = 20
MIN_SESSIONS_FOR_ANY_STUDY = 10


def required_rth_bars(symbol: str, d: date) -> int:
    """Regular-session bars a trade date needs to count as complete: 300 of the 390 scheduled
    09:30-16:00 ET minutes, scaled down on early-close days (CME calendar). 0 = no regular session."""
    from datetime import time

    from app.marketdata.calendar import ET
    from app.services.session_intel import session_bounds

    b = session_bounds(symbol, d)
    if not b:
        return 0
    rth_start = datetime.combine(d, time(9, 30), ET)
    rth_end = datetime.combine(d, time(16, 0), ET)
    minutes = (min(b[1], rth_end) - max(b[0], rth_start)).total_seconds() / 60
    return int(MIN_RTH_BARS * min(minutes, 390) / 390) if minutes > 0 else 0  # 300 of 390 on a full day


def roll_dates(symbol: str, start: date, end: date) -> list[str]:
    """Approximate quarterly roll dates (Thursday 8 days before the 3rd-Friday expiry) for equity-index
    futures. Continuous front-month prices are NOT back-adjusted, so the roll is a price jump."""
    root = symbol.lstrip("M") if symbol.startswith("M") and symbol not in ("MCL", "MGC") else symbol
    if root not in ("ES", "NQ", "YM", "RTY", "2K"):
        return []
    out = []
    for y in range(start.year, end.year + 1):
        for m in (3, 6, 9, 12):
            first = date(y, m, 1)
            third_friday = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
            roll = third_friday - timedelta(days=8)
            if start <= roll <= end:
                out.append(roll.isoformat())
    return out


def audit_bars(
    symbol: str,
    bars: Sequence[Bar],
    requested_start: datetime,
    requested_end: datetime,
    provenance: dict[str, int] | None = None,
) -> dict[str, Any]:
    inst = INSTRUMENTS[symbol]
    tick = float(inst.tick_size)
    out: dict[str, Any] = {
        "symbol": symbol,
        "requested": {
            "start": requested_start.isoformat(),
            "end": requested_end.isoformat(),
            "days": round((requested_end - requested_start).total_seconds() / 86400, 1),
        },
        "bars": len(bars),
        "provenance": provenance or {},
    }
    if not bars:
        out.update(verdict="NO DATA", sessions=0, usable_sessions=0)
        return out
    first, last = bars[0].ts, bars[-1].ts
    by_day: dict[date, list[Bar]] = {}
    for b in bars:
        by_day.setdefault(trading_day(b.ts), []).append(b)
    rth = {d: sum(1 for b in rows if is_rth(b.ts)) for d, rows in by_day.items()}
    need = {d: required_rth_bars(symbol, d) for d in by_day}
    usable = sorted(d for d, n in rth.items() if need[d] and n >= need[d])
    partial = sorted(d for d, n in rth.items() if 0 < n and not (need[d] and n >= need[d]))
    overnight_only = sorted(d for d, n in rth.items() if n == 0)

    ts_counts = Counter(b.ts for b in bars)
    dupes = sum(c - 1 for c in ts_counts.values() if c > 1)
    misaligned = sum(1 for b in bars if b.ts.second or b.ts.microsecond)
    unordered = sum(1 for a, b in itertools.pairwise(bars) if b.ts <= a.ts)
    bad_ohlc = sum(
        1 for b in bars if not (b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high) or b.low <= 0
    )
    off_tick = sum(
        1 for b in bars if any(abs(round(p / tick) * tick - p) > 1e-6 for p in (b.open, b.high, b.low, b.close))
    )
    zero_vol = sum(1 for b in bars if b.volume <= 0)
    gaps = find_gaps([b.ts for b in bars], first, last)
    in_session_missing = sum(g.missing_bars for g in gaps)
    largest = sorted(gaps, key=lambda g: g.missing_bars, reverse=True)[:5]
    closes = [b.close for b in bars]
    out.update(
        earliest_bar=first.astimezone(UTC).isoformat(),
        latest_bar=last.astimezone(UTC).isoformat(),
        span_days=round((last - first).total_seconds() / 86400, 2),
        truncated_by_history=first > requested_start + timedelta(days=1),
        sessions=len(by_day),
        usable_sessions=len(usable),
        usable_session_dates=[d.isoformat() for d in usable],
        partial_sessions=[d.isoformat() for d in partial],
        overnight_only_sessions=[d.isoformat() for d in overnight_only],
        integrity={
            "duplicate_timestamps": dupes,
            "timestamps_not_on_the_minute": misaligned,
            "out_of_order": unordered,
            "invalid_ohlc": bad_ohlc,
            "prices_off_tick": off_tick,
            "zero_volume_bars": zero_vol,
        },
        missing_in_session_minutes=in_session_missing,
        largest_gaps=[
            {"start": g.start.isoformat(), "end": g.end.isoformat(), "missing_bars": g.missing_bars} for g in largest
        ],
        rth_bars_by_session={d.isoformat(): n for d, n in sorted(rth.items())},
        quarterly_roll_dates_in_range=roll_dates(symbol, first.date(), last.date()),
        price_range=[min(closes), max(closes)],
        median_volume=sorted(b.volume for b in bars)[len(bars) // 2],
    )
    n = len(usable)
    out["verdict"] = (
        "SUFFICIENT" if n >= SUFFICIENT_SESSIONS else "LIMITED" if n >= MIN_SESSIONS_FOR_ANY_STUDY else "INSUFFICIENT"
    )
    out["verdict_rule"] = (
        f"usable session = >= {MIN_RTH_BARS} regular-session bars (scaled on "
        f"early-close days); SUFFICIENT >= {SUFFICIENT_SESSIONS}, "
        f"LIMITED >= {MIN_SESSIONS_FOR_ANY_STUDY}, otherwise INSUFFICIENT"
    )
    return out


def compare_instruments(a_sym: str, a: Sequence[Bar], b_sym: str, b: Sequence[Bar]) -> dict[str, Any]:
    """Checks that two instruments are independent series (not one series stored twice)."""
    bm = {x.ts: x for x in b}
    common = [(x, bm[x.ts]) for x in a if x.ts in bm]
    if not common:
        return {"pairs": f"{a_sym}/{b_sym}", "common_minutes": 0}
    identical = sum(
        1 for x, y in common if (x.open, x.high, x.low, x.close, x.volume) == (y.open, y.high, y.low, y.close, y.volume)
    )
    ratio = sorted(y.close / x.close for x, y in common if x.close)[len(common) // 2]
    return {
        "pairs": f"{a_sym}/{b_sym}",
        "common_minutes": len(common),
        "identical_bars": identical,
        "median_price_ratio": round(ratio, 3),
        "independent": identical == 0,
    }
