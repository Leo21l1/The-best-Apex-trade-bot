"""Technical indicators as pure functions over float sequences.

Every function returns a list aligned with its input; positions without enough history are
``None`` so callers can never read a value computed from too little data.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.marketdata.calendar import ET, trading_day
from app.research.bars import Bar

Series = list[float | None]

RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)


def sma(values: Sequence[float], n: int) -> Series:
    out: Series = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(values: Sequence[float], n: int) -> Series:
    """Exponential moving average, alpha = 2/(n+1), seeded with the SMA of the first n values."""
    out: Series = [None] * len(values)
    if len(values) < n:
        return out
    k = 2.0 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def true_range(bars: Sequence[Bar]) -> list[float]:
    out = []
    for i, b in enumerate(bars):
        if i == 0:
            out.append(b.high - b.low)
        else:
            pc = bars[i - 1].close
            out.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
    return out


def atr(bars: Sequence[Bar], n: int = 14) -> Series:
    """Wilder's Average True Range."""
    tr = true_range(bars)
    out: Series = [None] * len(bars)
    if len(bars) < n:
        return out
    prev = sum(tr[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(bars)):
        prev = (prev * (n - 1) + tr[i]) / n
        out[i] = prev
    return out


def rsi(values: Sequence[float], n: int = 14) -> Series:
    """Wilder's Relative Strength Index (0-100)."""
    out: Series = [None] * len(values)
    if len(values) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(values)):
        d = values[i] - values[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def bollinger(values: Sequence[float], n: int = 20, k: float = 2.0) -> tuple[Series, Series, Series]:
    """(middle, upper, lower) using the population standard deviation."""
    mid = sma(values, n)
    upper: Series = [None] * len(values)
    lower: Series = [None] * len(values)
    for i in range(n - 1, len(values)):
        m = mid[i]
        assert m is not None
        window = values[i - n + 1 : i + 1]
        sd = math.sqrt(sum((x - m) ** 2 for x in window) / n)
        upper[i], lower[i] = m + k * sd, m - k * sd
    return mid, upper, lower


def local_time(ts: datetime) -> time:
    return ts.astimezone(ET).time()


def is_rth(ts: datetime) -> bool:
    """Regular trading hours for US index futures analysis: 09:30-16:00 ET, Mon-Fri."""
    local = ts.astimezone(ET)
    return local.weekday() < 5 and RTH_OPEN <= local.time() < RTH_CLOSE


def session_vwap(bars: Sequence[Bar]) -> tuple[Series, Series]:
    """Volume-weighted average price and its volume-weighted standard deviation.

    Anchored to each trading day's 09:30 ET open; ``None`` outside regular hours and while
    cumulative volume is zero. Typical price = (H + L + C) / 3.
    """
    vwap: Series = [None] * len(bars)
    sd: Series = [None] * len(bars)
    day = None
    pv = vol = pv2 = 0.0
    for i, b in enumerate(bars):
        if not is_rth(b.ts):
            continue
        d = trading_day(b.ts)
        if d != day:
            day, pv, vol, pv2 = d, 0.0, 0.0, 0.0
        tp = (b.high + b.low + b.close) / 3
        pv += tp * b.volume
        pv2 += tp * tp * b.volume
        vol += b.volume
        if vol > 0:
            mean = pv / vol
            vwap[i] = mean
            sd[i] = math.sqrt(max(pv2 / vol - mean * mean, 0.0))
    return vwap, sd


@dataclass(frozen=True, slots=True)
class Levels:
    """Reference price levels for the trading day containing ``as_of``. None = not available."""

    prior_day_high: float | None = None
    prior_day_low: float | None = None
    prior_day_close: float | None = None
    overnight_high: float | None = None
    overnight_low: float | None = None
    opening_range_high: float | None = None
    opening_range_low: float | None = None
    #: True once the full opening-range window (09:30-09:45 ET) has elapsed.
    opening_range_complete: bool = False


OPENING_RANGE_MINUTES = 15


def reference_levels(bars_1m: Sequence[Bar], as_of: datetime) -> Levels:
    """Levels from 1-minute bars that closed at or before ``as_of`` (no look-ahead).

    * prior day H/L/C: regular-hours (09:30-16:00 ET) range of the previous trading day
    * overnight H/L: current trading day's bars before 09:30 ET (from the 18:00 ET open)
    * opening range: 09:30-09:45 ET of the current trading day
    """
    today = trading_day(as_of)
    pdh = pdl = pdc = onh = onl = orh = orl = None
    prior = None
    or_end = None
    for b in bars_1m:
        if b.ts + timedelta(minutes=1) > as_of:
            break
        d = trading_day(b.ts)
        if d < today:
            if is_rth(b.ts):
                if prior != d:  # a more recent prior day replaces the older one
                    prior, pdh, pdl = d, b.high, b.low
                else:
                    pdh, pdl = max(pdh, b.high), min(pdl, b.low)  # type: ignore[type-var]
                pdc = b.close
        elif d == today:
            lt = local_time(b.ts)
            if not is_rth(b.ts) and (lt >= time(18, 0) or lt < RTH_OPEN):
                onh = b.high if onh is None else max(onh, b.high)
                onl = b.low if onl is None else min(onl, b.low)
            elif is_rth(b.ts):
                start = b.ts.astimezone(ET).replace(hour=9, minute=30, second=0, microsecond=0)
                or_end = start + timedelta(minutes=OPENING_RANGE_MINUTES)
                if b.ts < or_end:
                    orh = b.high if orh is None else max(orh, b.high)
                    orl = b.low if orl is None else min(orl, b.low)
    complete = or_end is not None and as_of >= or_end and orh is not None
    return Levels(pdh, pdl, pdc, onh, onl, orh, orl, complete)


def last(series: Sequence[float | None]) -> float | None:
    return series[-1] if series else None
