"""Deterministic data-quality checks: bar validation, duplicate filtering, gap detection."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.marketdata.calendar import expected_minutes, is_market_open, to_utc
from app.marketdata.instruments import get_instrument
from app.marketdata.types import BASE_TIMEFRAME, Candle

#: Bars stamped further than this into the future are rejected (clock skew allowance).
FUTURE_TOLERANCE = timedelta(seconds=90)


def validate_candle(c: Candle, now: datetime) -> list[str]:
    """Return a list of problems; an empty list means the bar is acceptable."""
    problems: list[str] = []
    inst = get_instrument(c.symbol)
    if inst is None:
        return [f"unknown symbol {c.symbol}"]
    if c.ts.tzinfo is None:
        return ["naive timestamp"]
    ts = to_utc(c.ts)
    if ts.second or ts.microsecond:
        problems.append("timestamp not aligned to a minute boundary")
    if ts > to_utc(now) + FUTURE_TOLERANCE:
        problems.append("timestamp in the future")
    prices = (c.open, c.high, c.low, c.close)
    if any((not p.is_finite()) or p <= 0 for p in prices):
        # Crude oil settled negative in April 2020; treated as exceptional and rejected here so
        # it is surfaced for manual review rather than silently charted.
        problems.append("non-positive or non-finite price")
        return problems
    if c.high < max(c.open, c.close) or c.low > min(c.open, c.close) or c.high < c.low:
        problems.append("OHLC inconsistent")
    if any(p % inst.tick_size != 0 for p in prices):
        problems.append(f"price not on tick grid ({inst.tick_size})")
    if c.volume < 0:
        problems.append("negative volume")
    if c.timeframe == BASE_TIMEFRAME and not is_market_open(ts):
        problems.append("bar outside exchange session")
    return problems


class DuplicateFilter:
    """Drops events identical to the last one seen for the same bar.

    A changed payload for the same bar key (a correction or an in-progress update) passes.
    """

    def __init__(self, max_keys: int = 20_000) -> None:
        self._seen: dict[tuple, tuple] = {}
        self._max = max_keys
        self.dropped = 0

    def is_duplicate(self, c: Candle) -> bool:
        key = c.key()
        payload = (c.open, c.high, c.low, c.close, c.volume, c.is_closed)
        if self._seen.get(key) == payload:
            self.dropped += 1
            return True
        if len(self._seen) >= self._max:
            for old in list(self._seen)[: self._max // 2]:
                del self._seen[old]
        self._seen[key] = payload
        return False


@dataclass(frozen=True, slots=True)
class Gap:
    start: datetime
    end: datetime
    missing_bars: int


def find_gaps(present: Iterable[datetime], start: datetime, end: datetime) -> list[Gap]:
    """Runs of in-session 1-minute bars absent from ``present`` within [start, end].

    A missing bar is a *candidate* gap: thin markets can legitimately print no trade in a
    minute, and exchange holidays are not modelled (see calendar module).
    """
    have = {to_utc(t) for t in present}
    gaps: list[Gap] = []
    run_start: datetime | None = None
    prev: datetime | None = None
    count = 0
    for minute in expected_minutes(start, end):
        if minute in have:
            if run_start is not None and prev is not None:
                gaps.append(Gap(run_start, prev, count))
            run_start, count = None, 0
        else:
            if run_start is None:
                run_start = minute
            count += 1
            prev = minute
    if run_start is not None and prev is not None:
        gaps.append(Gap(run_start, prev, count))
    return gaps


def round_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).quantize(Decimal(1)) * tick


def sort_dedupe(candles: Sequence[Candle]) -> list[Candle]:
    """Sort by time and keep the last occurrence for each bar key."""
    by_key = {c.key(): c for c in candles}
    return sorted(by_key.values(), key=lambda c: (c.symbol, c.ts))
