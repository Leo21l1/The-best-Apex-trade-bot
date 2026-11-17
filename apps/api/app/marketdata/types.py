"""Core market-data domain types shared by adapters, storage and the API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class DataQuality(StrEnum):
    """Provenance class of a displayed value. Every price carries one."""

    LIVE = "live"
    DELAYED = "delayed"
    HISTORICAL = "historical"
    SIMULATED = "simulated"
    UNAVAILABLE = "unavailable"


class Timeframe(StrEnum):
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"

    @property
    def minutes(self) -> int:
        return _MINUTES[self]

    @property
    def delta(self) -> timedelta:
        return timedelta(minutes=self.minutes)


_MINUTES = {
    Timeframe.M1: 1,
    Timeframe.M3: 3,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.M30: 30,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
    Timeframe.W1: 10080,
}

#: The only timeframe that is ingested and stored; all others are aggregated from it.
BASE_TIMEFRAME = Timeframe.M1


@dataclass(frozen=True, slots=True)
class Candle:
    """One OHLCV bar. ``ts`` is the bar OPEN time, timezone-aware UTC."""

    symbol: str
    timeframe: Timeframe
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    provider: str
    quality: DataQuality
    is_closed: bool = True

    def key(self) -> tuple[str, str, str, datetime]:
        return (self.provider, self.symbol, self.timeframe.value, self.ts)
