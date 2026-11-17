"""Supported futures universe (market-data view).

Phase 2 needs only symbol identity and tick size (for price validation). Full contract
specifications (tick value, point value, margins, expirations) arrive with the risk engine.
Tick sizes are CME Group contract-specification minimum price fluctuations.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    name: str
    exchange: str
    asset_class: str
    tick_size: Decimal
    is_micro: bool
    #: The related standard/micro symbol tracking the same underlying.
    sibling: str


def _i(symbol: str, name: str, exch: str, cls: str, tick: str, micro: bool, sib: str) -> Instrument:
    return Instrument(symbol, name, exch, cls, Decimal(tick), micro, sib)


INSTRUMENTS: dict[str, Instrument] = {
    i.symbol: i
    for i in [
        _i("ES", "E-mini S&P 500", "CME", "equity_index", "0.25", False, "MES"),
        _i("MES", "Micro E-mini S&P 500", "CME", "equity_index", "0.25", True, "ES"),
        _i("NQ", "E-mini Nasdaq-100", "CME", "equity_index", "0.25", False, "MNQ"),
        _i("MNQ", "Micro E-mini Nasdaq-100", "CME", "equity_index", "0.25", True, "NQ"),
        _i("YM", "E-mini Dow ($5)", "CBOT", "equity_index", "1", False, "MYM"),
        _i("MYM", "Micro E-mini Dow", "CBOT", "equity_index", "1", True, "YM"),
        _i("RTY", "E-mini Russell 2000", "CME", "equity_index", "0.10", False, "M2K"),
        _i("M2K", "Micro E-mini Russell 2000", "CME", "equity_index", "0.10", True, "RTY"),
        _i("CL", "Crude Oil", "NYMEX", "energy", "0.01", False, "MCL"),
        _i("MCL", "Micro WTI Crude Oil", "NYMEX", "energy", "0.01", True, "CL"),
        _i("GC", "Gold", "COMEX", "metal", "0.10", False, "MGC"),
        _i("MGC", "Micro Gold", "COMEX", "metal", "0.10", True, "GC"),
    ]
}


def get_instrument(symbol: str) -> Instrument | None:
    return INSTRUMENTS.get(symbol.upper())
