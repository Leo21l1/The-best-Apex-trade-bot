"""SIMULATED market-data provider for development, demos and tests.

Prices are a deterministic pseudo-random walk seeded per (symbol, minute). They are NOT real
market prices and every bar is tagged ``DataQuality.SIMULATED``, which the UI surfaces on every
value. This adapter makes no network calls.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.adapters.marketdata.base import MarketDataProvider, ProviderInfo
from app.marketdata.calendar import floor_minute, is_market_open
from app.marketdata.instruments import INSTRUMENTS
from app.marketdata.types import BASE_TIMEFRAME, Candle, DataQuality
from app.marketdata.validation import round_to_tick

# Arbitrary round anchor levels so charts look plausible. Not market prices.
_ANCHORS = {
    "ES": 5000,
    "MES": 5000,
    "NQ": 18000,
    "MNQ": 18000,
    "YM": 40000,
    "MYM": 40000,
    "RTY": 2000,
    "M2K": 2000,
    "CL": 70,
    "MCL": 70,
    "GC": 2500,
    "MGC": 2500,
}
_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


def _unit(seed: str) -> float:
    """Deterministic float in [0, 1)."""
    h = hashlib.blake2b(seed.encode(), digest_size=8).digest()
    return int.from_bytes(h, "big") / 2**64


class SimulatedProvider(MarketDataProvider):
    info = ProviderInfo(
        name="simulated",
        stream_quality=DataQuality.SIMULATED,
        description="Deterministic synthetic prices for development. Not real market data.",
        verified=True,
    )

    def __init__(
        self,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        tick_seconds: float = 2.0,
    ) -> None:
        self._clock = clock
        self._tick_seconds = tick_seconds

    # -- deterministic price model ------------------------------------------------------
    def _level(self, symbol: str, ts: datetime) -> float:
        """Smooth multi-cycle level; siblings (e.g. ES/MES) share the same path."""
        base = INSTRUMENTS[symbol].sibling if INSTRUMENTS[symbol].is_micro else symbol
        anchor = _ANCHORS[symbol]
        m = (ts - _EPOCH).total_seconds() / 60.0
        phase = _unit(base) * 1000
        wave = (
            0.010 * math.sin((m + phase) / 1440 * 2 * math.pi)
            + 0.004 * math.sin((m + phase * 3) / 240 * 2 * math.pi)
            + 0.0015 * math.sin((m + phase * 7) / 37 * 2 * math.pi)
        )
        noise = (_unit(f"{base}:{int(m)}") - 0.5) * 0.0006
        return anchor * (1 + wave + noise)

    def _bar(self, symbol: str, ts: datetime, progress: float = 1.0) -> Candle:
        tick = INSTRUMENTS[symbol].tick_size
        o = self._level(symbol, ts)
        full_close = self._level(symbol, ts + timedelta(minutes=1))
        c = o + (full_close - o) * progress
        wiggle = abs(full_close - o) * 0.6 + _ANCHORS[symbol] * 0.00015
        hi = max(o, c) + wiggle * _unit(f"h:{symbol}:{ts.isoformat()}") * progress
        lo = min(o, c) - wiggle * _unit(f"l:{symbol}:{ts.isoformat()}") * progress
        d = [round_to_tick(Decimal(str(x)), tick) for x in (o, hi, lo, c)]
        volume = int((200 + 1800 * _unit(f"v:{symbol}:{ts.isoformat()}")) * progress)
        if INSTRUMENTS[symbol].is_micro:
            volume //= 4
        return Candle(
            symbol=symbol,
            timeframe=BASE_TIMEFRAME,
            ts=ts,
            open=d[0],
            high=max(d),
            low=min(d),
            close=d[3],
            volume=volume,
            provider=self.info.name,
            quality=DataQuality.SIMULATED,
            is_closed=progress >= 1.0,
        )

    # -- adapter contract ---------------------------------------------------------------
    async def fetch_candles(self, symbol: str, start: datetime, end: datetime) -> list[Candle]:
        now = self._clock()
        cur = floor_minute(start)
        if cur < start:
            cur += timedelta(minutes=1)
        out: list[Candle] = []
        while cur < end and cur + timedelta(minutes=1) <= now:
            if is_market_open(cur):
                out.append(self._bar(symbol, cur))
            cur += timedelta(minutes=1)
        return out

    async def stream_candles(self, symbols: Sequence[str]) -> AsyncIterator[Candle]:
        last_closed: dict[str, datetime] = {}
        while True:
            now = self._clock()
            minute = floor_minute(now)
            prev = minute - timedelta(minutes=1)
            for s in symbols:
                if is_market_open(prev) and last_closed.get(s) != prev:
                    last_closed[s] = prev
                    yield self._bar(s, prev)
                if is_market_open(minute):
                    progress = max(0.02, (now - minute).total_seconds() / 60.0)
                    yield self._bar(s, minute, min(progress, 0.99))
            await asyncio.sleep(self._tick_seconds)
