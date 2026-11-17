"""Plain bar series and timeframe aggregation for research code.

Research, strategies and (later) the backtester work on immutable float bars and pure functions
so the same logic runs live and in replay. Aggregation here mirrors the SQL bucketing in
``app.marketdata.store`` (buckets anchored to the 18:00 ET session open; daily/weekly bars
follow the trading day) and is tested for equality with it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.marketdata.calendar import ET
from app.marketdata.types import Timeframe


@dataclass(frozen=True, slots=True)
class Bar:
    ts: datetime  # bar open, UTC
    open: float
    high: float
    low: float
    close: float
    volume: int


_ANCHOR = datetime(2000, 1, 2, 18, 0)  # a Sunday 18:00, naive New York wall time


def bucket_start(ts: datetime, tf: Timeframe) -> datetime:
    """Open time (UTC) of the ``tf`` bar containing a 1-minute bar that opens at ``ts``."""
    if tf == Timeframe.M1:
        return ts
    local = ts.astimezone(ET).replace(tzinfo=None)
    if tf in (Timeframe.D1, Timeframe.W1):
        day = (local + timedelta(hours=6)).date()
        if tf == Timeframe.W1:
            day -= timedelta(days=day.weekday())
        return datetime(day.year, day.month, day.day, tzinfo=UTC)
    size = tf.minutes * 60
    elapsed = int((local - _ANCHOR).total_seconds())
    start_local = _ANCHOR + timedelta(seconds=elapsed - elapsed % size)
    return start_local.replace(tzinfo=ET).astimezone(UTC)


def aggregate(bars: Sequence[Bar], tf: Timeframe) -> list[Bar]:
    """Aggregate ascending 1-minute bars into ``tf`` bars. The last bar may be incomplete."""
    if tf == Timeframe.M1:
        return list(bars)
    out: list[Bar] = []
    cur_key: datetime | None = None
    o = h = low = c = 0.0
    v = 0
    for b in bars:
        key = bucket_start(b.ts, tf)
        if key != cur_key:
            if cur_key is not None:
                out.append(Bar(cur_key, o, h, low, c, v))
            cur_key, o, h, low, c, v = key, b.open, b.high, b.low, b.close, b.volume
        else:
            h = max(h, b.high)
            low = min(low, b.low)
            c = b.close
            v += b.volume
    if cur_key is not None:
        out.append(Bar(cur_key, o, h, low, c, v))
    return out


def closed_only(bars: Sequence[Bar], tf: Timeframe, now: datetime) -> list[Bar]:
    """Drop a trailing bar whose period has not finished at ``now`` (prevents look-ahead)."""
    if bars and bars[-1].ts + tf.delta > now:
        return list(bars[:-1])
    return list(bars)
