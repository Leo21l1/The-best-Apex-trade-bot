from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.marketdata.types import DataQuality


class InstrumentOut(BaseModel):
    symbol: str
    name: str
    exchange: str
    asset_class: str
    tick_size: float
    tick_value: float
    point_value: float
    is_micro: bool
    sibling: str


class BarOut(BaseModel):
    #: Bar open time, epoch seconds UTC.
    t: int
    o: float
    h: float
    l: float  # noqa: E741
    c: float
    v: int


class Provenance(BaseModel):
    """Attached to every price-bearing response."""

    provider: str | None
    quality: DataQuality
    #: When the server produced this response.
    as_of: datetime
    #: Open time of the newest stored bar for the symbol, if any.
    last_bar: datetime | None = None
    stale: bool = False
    note: str | None = None


class CandlesOut(BaseModel):
    symbol: str
    timeframe: str
    provenance: Provenance
    bars: list[BarOut]


class SnapshotItem(BaseModel):
    symbol: str
    last: float | None
    last_bar: datetime | None
    age_seconds: float | None
    stale: bool


class SnapshotOut(BaseModel):
    provenance: Provenance
    items: list[SnapshotItem]


class SymbolFreshness(BaseModel):
    symbol: str
    last_bar: datetime | None
    age_seconds: float | None
    stale: bool


class FeedStatusOut(BaseModel):
    configured_provider: str
    reporting: bool
    state: str
    quality: DataQuality
    verified: bool | None
    description: str | None
    market_open: bool
    reconnects: int
    duplicates_dropped: int
    invalid_dropped: int
    bars_stored: int
    last_error: str | None
    stale_after_seconds: int
    symbols: list[SymbolFreshness]
    as_of: datetime


class GapOut(BaseModel):
    start: datetime
    end: datetime
    missing_bars: int


class GapReport(BaseModel):
    symbol: str
    provider: str | None
    window_start: datetime
    window_end: datetime
    expected_bars: int
    present_bars: int
    missing_bars: int
    gaps: list[GapOut]
    note: str
