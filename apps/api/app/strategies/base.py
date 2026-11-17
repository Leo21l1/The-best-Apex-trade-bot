"""Strategy framework: specifications, market context and explainable evaluations.

A strategy is a deterministic function of closed bars. It returns every condition it checked
(with the measured values) so a signal - or the absence of one - can always be explained.
Strategies never see the forming bar, account data or anything outside the context.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, time
from functools import cached_property
from typing import Literal

from app.marketdata.calendar import ET
from app.marketdata.instruments import INSTRUMENTS, Instrument
from app.marketdata.types import Timeframe
from app.research.bars import Bar, aggregate, closed_only
from app.research.indicators import (
    Levels,
    Series,
    atr,
    bollinger,
    ema,
    reference_levels,
    rsi,
    session_vwap,
    sma,
)

Direction = Literal["long", "short"]
ALL_SYMBOLS = tuple(INSTRUMENTS)
EQUITY_INDEX = tuple(s for s, i in INSTRUMENTS.items() if i.asset_class == "equity_index")
MIN_BARS_5M = 60


@dataclass(frozen=True, slots=True)
class StrategySpec:
    id: str
    code: str
    name: str
    timeframe: str
    required_data: str
    eligible_instruments: tuple[str, ...]
    regime: str
    entry: str
    invalidation: str
    stop: str
    targets: str
    time_exit: str
    testing: str
    costs: str = (
        "Assumed round-trip cost per contract = 2 x commission + 2 x 1 tick slippage "
        "(configurable assumptions, not broker quotes)."
    )


@dataclass(frozen=True, slots=True)
class Condition:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Signal:
    strategy_id: str
    symbol: str
    direction: Direction
    #: Open time of the 5-minute bar whose close triggered the signal.
    bar_ts: datetime
    entry: float
    entry_low: float
    entry_high: float
    stop: float
    targets: tuple[float, ...]

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_risk(self) -> float:
        return abs(self.targets[0] - self.entry) / self.risk if self.risk > 0 else 0.0


@dataclass(frozen=True, slots=True)
class Evaluation:
    strategy_id: str
    symbol: str
    as_of: datetime
    conditions: tuple[Condition, ...]
    signal: Signal | None = None
    #: Side whose conditions are listed (the side closest to triggering).
    side: Direction | None = None
    note: str | None = None


@dataclass
class MarketContext:
    """Closed bars and derived indicators for one symbol at one instant."""

    symbol: str
    now: datetime
    bars_1m: Sequence[Bar]
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def instrument(self) -> Instrument:
        return INSTRUMENTS[self.symbol]

    @property
    def tick(self) -> float:
        return float(self.instrument.tick_size)

    def round(self, price: float) -> float:
        t = self.tick
        return round(round(price / t) * t, 6)

    def bars(self, tf: Timeframe) -> list[Bar]:
        if tf not in self._cache:
            self._cache[tf] = closed_only(aggregate(self.bars_1m, tf), tf, self.now)
        return self._cache[tf]

    @cached_property
    def m5(self) -> list[Bar]:
        return self.bars(Timeframe.M5)

    @cached_property
    def closes(self) -> list[float]:
        return [b.close for b in self.m5]

    @cached_property
    def ema20(self) -> Series:
        return ema(self.closes, 20)

    @cached_property
    def ema50(self) -> Series:
        return ema(self.closes, 50)

    @cached_property
    def atr14(self) -> Series:
        return atr(self.m5, 14)

    @cached_property
    def rsi14(self) -> Series:
        return rsi(self.closes, 14)

    @cached_property
    def vwap(self) -> tuple[Series, Series]:
        return session_vwap(self.m5)

    @cached_property
    def boll(self) -> tuple[Series, Series, Series]:
        return bollinger(self.closes, 20, 2.0)

    @cached_property
    def vol_sma20(self) -> Series:
        return sma([float(b.volume) for b in self.m5], 20)

    @cached_property
    def levels(self) -> Levels:
        return reference_levels(self.bars_1m, self.now)

    def trend(self, tf: Timeframe) -> int | None:
        """+1 if EMA20 > EMA50 on ``tf``, -1 if below, None with insufficient history."""
        closes = [b.close for b in self.bars(tf)]
        e20, e50 = ema(closes, 20), ema(closes, 50)
        if not closes or e20[-1] is None or e50[-1] is None:
            return None
        return 1 if e20[-1] > e50[-1] else -1 if e20[-1] < e50[-1] else 0

    def close_time_et(self) -> time:
        """New York wall time at which the latest closed 5-minute bar ended."""
        return (self.m5[-1].ts + Timeframe.M5.delta).astimezone(ET).time()


def fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:,.4f}".rstrip("0").rstrip(".")


class Strategy:
    """Base class. Subclasses implement ``_side`` for one direction (sign +1 long, -1 short)."""

    spec: StrategySpec
    #: (target-1, target-2) as multiples of initial risk R.
    target_r: tuple[float, float] = (1.5, 2.5)
    window: tuple[time, time] = (time(9, 45), time(15, 30))

    def _side(
        self, ctx: MarketContext, s: int
    ) -> tuple[list[Condition], float | None, tuple[float, ...] | None]:
        """Return (conditions, stop price or None, explicit targets or None)."""
        raise NotImplementedError

    def evaluate(self, ctx: MarketContext) -> Evaluation:
        sid = self.spec.id
        if ctx.symbol not in self.spec.eligible_instruments:
            return Evaluation(sid, ctx.symbol, ctx.now, (), note="instrument not eligible")
        if len(ctx.m5) < MIN_BARS_5M or ctx.atr14[-1] is None or ctx.ema50[-1] is None:
            return Evaluation(
                sid,
                ctx.symbol,
                ctx.now,
                (),
                note=f"insufficient history ({len(ctx.m5)} of {MIN_BARS_5M} 5-minute bars)",
            )
        t = ctx.close_time_et()
        in_window = self.window[0] <= t <= self.window[1]
        window_cond = Condition(
            "session window",
            in_window,
            f"bar closed {t:%H:%M} ET; window {self.window[0]:%H:%M}-{self.window[1]:%H:%M} ET",
        )
        best: tuple[int, list[Condition], Direction] | None = None
        for s, name in ((1, "long"), (-1, "short")):
            conds, stop, targets = self._side(ctx, s)
            conds = [window_cond, *conds]
            passed = sum(c.passed for c in conds)
            if all(c.passed for c in conds) and stop is not None:
                signal = self._signal(ctx, s, stop, targets)
                if signal is not None:
                    return Evaluation(sid, ctx.symbol, ctx.now, tuple(conds), signal, name)
                conds.append(Condition("valid stop distance", False, "stop on wrong side of entry"))
            if best is None or passed > best[0]:
                best = (passed, conds, name)  # type: ignore[assignment]
        assert best is not None
        return Evaluation(sid, ctx.symbol, ctx.now, tuple(best[1]), None, best[2])

    def _signal(
        self, ctx: MarketContext, s: int, stop: float, targets: tuple[float, ...] | None
    ) -> Signal | None:
        entry = ctx.round(ctx.m5[-1].close)
        stop = ctx.round(stop)
        risk = (entry - stop) * s
        if risk < ctx.tick:  # stop must be at least one tick on the losing side
            return None
        if targets is None:
            targets = tuple(entry + s * risk * m for m in self.target_r)
        targets = tuple(ctx.round(x) for x in targets)
        if any((x - entry) * s <= 0 for x in targets):
            return None
        chase = 0.1 * (ctx.atr14[-1] or 0.0)
        zone = sorted((entry, ctx.round(entry + s * chase)))
        return Signal(
            self.spec.id,
            ctx.symbol,
            "long" if s == 1 else "short",
            ctx.m5[-1].ts,
            entry,
            zone[0],
            zone[1],
            stop,
            targets,
        )
