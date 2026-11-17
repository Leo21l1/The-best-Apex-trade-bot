"""Supported futures universe and contract specifications.

Tick sizes and tick values are the CME Group contract-specification minimum price fluctuation
and its dollar value per contract. ``point_value`` (the contract multiplier) is derived as
tick_value / tick_size. Each micro contract is one tenth of its standard sibling.
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
    #: Dollar value of one tick for one contract.
    tick_value: Decimal
    is_micro: bool
    #: The related standard/micro symbol tracking the same underlying.
    sibling: str

    @property
    def point_value(self) -> Decimal:
        """Dollar value of a 1.00 price move for one contract (contract multiplier)."""
        return self.tick_value / self.tick_size

    @property
    def standard_equivalent(self) -> Decimal:
        """Fraction of a standard contract this contract represents (micros: 0.1)."""
        return Decimal("0.1") if self.is_micro else Decimal("1")

    def ticks_between(self, a: Decimal, b: Decimal) -> Decimal:
        return abs(a - b) / self.tick_size

    def dollars_between(self, a: Decimal, b: Decimal, contracts: int = 1) -> Decimal:
        """Dollar P&L magnitude of a move from ``a`` to ``b`` for ``contracts`` contracts."""
        return self.ticks_between(a, b) * self.tick_value * contracts


def _i(
    symbol: str, name: str, exch: str, cls: str, tick: str, value: str, micro: bool, sib: str
) -> Instrument:
    return Instrument(symbol, name, exch, cls, Decimal(tick), Decimal(value), micro, sib)


INSTRUMENTS: dict[str, Instrument] = {
    i.symbol: i
    for i in [
        _i("ES", "E-mini S&P 500", "CME", "equity_index", "0.25", "12.50", False, "MES"),
        _i("MES", "Micro E-mini S&P 500", "CME", "equity_index", "0.25", "1.25", True, "ES"),
        _i("NQ", "E-mini Nasdaq-100", "CME", "equity_index", "0.25", "5.00", False, "MNQ"),
        _i("MNQ", "Micro E-mini Nasdaq-100", "CME", "equity_index", "0.25", "0.50", True, "NQ"),
        _i("YM", "E-mini Dow ($5)", "CBOT", "equity_index", "1", "5.00", False, "MYM"),
        _i("MYM", "Micro E-mini Dow", "CBOT", "equity_index", "1", "0.50", True, "YM"),
        _i("RTY", "E-mini Russell 2000", "CME", "equity_index", "0.10", "5.00", False, "M2K"),
        _i("M2K", "Micro E-mini Russell 2000", "CME", "equity_index", "0.10", "0.50", True, "RTY"),
        _i("CL", "Crude Oil", "NYMEX", "energy", "0.01", "10.00", False, "MCL"),
        _i("MCL", "Micro WTI Crude Oil", "NYMEX", "energy", "0.01", "1.00", True, "CL"),
        _i("GC", "Gold", "COMEX", "metal", "0.10", "10.00", False, "MGC"),
        _i("MGC", "Micro Gold", "COMEX", "metal", "0.10", "1.00", True, "GC"),
    ]
}


def get_instrument(symbol: str) -> Instrument | None:
    return INSTRUMENTS.get(symbol.upper())
