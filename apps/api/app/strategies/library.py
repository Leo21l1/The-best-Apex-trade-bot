"""Strategy modules A-H.

All rules are evaluated on closed 5-minute bars (H also reads 15-minute and 1-hour trend).
Notation: s = +1 for long, -1 for short; ATR = Wilder ATR(14) on 5-minute bars; EMA20/EMA50 on
5-minute closes; VWAP anchored at 09:30 ET with volume-weighted standard deviation sigma.
"R" is the initial risk |entry - stop|. No strategy here is assumed profitable: each must pass
the research validation standards (Phase 6) before it can produce ranked opportunities.
"""

from __future__ import annotations

from datetime import time

from app.marketdata.types import Timeframe
from app.strategies.base import (
    ALL_SYMBOLS,
    EQUITY_INDEX,
    Condition,
    MarketContext,
    Strategy,
    StrategySpec,
    fmt,
)

_TESTING = (
    "Event-driven backtest on 1-minute history with next-bar fills, commission and slippage; "
    "walk-forward and out-of-sample validation; minimum trade count before eligibility."
)
_TIME_EXIT = "Flat by 15:55 ET; cancel if not entered within 3 bars (15 minutes) of the signal."
Side = tuple[list[Condition], float | None, tuple[float, ...] | None]


def _dir(s: int) -> str:
    return "above" if s == 1 else "below"


class OpeningRangeBreakout(Strategy):
    spec = StrategySpec(
        id="orb",
        code="A",
        name="Opening Range Breakout",
        timeframe="5m",
        required_data="1-minute OHLCV from 09:30 ET; 20 prior 5-minute bars for volume average.",
        eligible_instruments=EQUITY_INDEX,
        regime="Opening-range width between 0.5 and 4.0 ATR (not a dead or exhausted open).",
        entry="First 5m close beyond the 09:30-09:45 ET range (previous close inside it) with "
        "bar volume >= 1.2 x SMA20(volume), between 09:45 and 11:30 ET.",
        invalidation="A 5m close back inside the opening range before entry.",
        stop="Opening-range midpoint.",
        targets="1R and 2R from entry.",
        time_exit=_TIME_EXIT,
        testing=_TESTING,
    )
    target_r = (1.0, 2.0)
    window = (time(9, 50), time(11, 30))

    def _side(self, ctx: MarketContext, s: int) -> Side:
        lv, bar, prev = ctx.levels, ctx.m5[-1], ctx.m5[-2]
        a = ctx.atr14[-1] or 0.0
        if not lv.opening_range_complete or lv.opening_range_high is None:
            return (
                [Condition("opening range complete", False, "09:30-09:45 ET range not formed")],
                None,
                None,
            )
        hi, lo = lv.opening_range_high, lv.opening_range_low or 0.0
        level = hi if s == 1 else lo
        width = hi - lo
        vol_avg = ctx.vol_sma20[-2] or 0.0
        conds = [
            Condition("opening range complete", True, f"range {fmt(lo)}-{fmt(hi)}"),
            Condition(
                "range width 0.5-4.0 ATR",
                0.5 * a <= width <= 4.0 * a,
                f"width {fmt(width)} = {fmt(width / a if a else None)} ATR",
            ),
            Condition(
                f"close {_dir(s)} range",
                (bar.close - level) * s > 0,
                f"close {fmt(bar.close)} vs level {fmt(level)}",
            ),
            Condition(
                "fresh breakout",
                (prev.close - level) * s <= 0,
                f"previous close {fmt(prev.close)} was not beyond {fmt(level)}",
            ),
            Condition(
                "volume >= 1.2 x average",
                bar.volume >= 1.2 * vol_avg > 0,
                f"volume {bar.volume:,} vs average {fmt(vol_avg)}",
            ),
        ]
        return conds, (hi + lo) / 2, None


class VwapTrendContinuation(Strategy):
    spec = StrategySpec(
        id="vwap_trend",
        code="B",
        name="VWAP Trend Continuation",
        timeframe="5m",
        required_data="5-minute OHLCV for the current regular session (VWAP) plus 50 prior bars.",
        eligible_instruments=ALL_SYMBOLS,
        regime="Trend: EMA20 beyond EMA50, close beyond VWAP, VWAP rising/falling over 6 bars.",
        entry="Bar tests VWAP or EMA20 (wick within 0.25 ATR) and closes back beyond EMA20 in "
        "the trend direction with a with-trend body, 09:45-15:30 ET.",
        invalidation="A 5m close on the wrong side of VWAP.",
        stop="Extreme of the last 3 bars minus/plus 0.25 ATR.",
        targets="1.5R and 2.5R.",
        time_exit=_TIME_EXIT,
        testing=_TESTING,
    )

    def _side(self, ctx: MarketContext, s: int) -> Side:
        bar, a = ctx.m5[-1], ctx.atr14[-1] or 0.0
        vwap = ctx.vwap[0]
        v, v6 = vwap[-1], vwap[-7] if len(vwap) >= 7 else None
        e20, e50 = ctx.ema20[-1] or 0.0, ctx.ema50[-1] or 0.0
        if v is None or v6 is None:
            return [Condition("VWAP available", False, "needs 7 regular-session bars")], None, None
        wick = bar.low if s == 1 else bar.high
        near = min(abs(wick - v), abs(wick - e20)) <= 0.25 * a or (wick - e20) * s <= 0
        recent = ctx.m5[-3:]
        extreme = min(b.low for b in recent) if s == 1 else max(b.high for b in recent)
        conds = [
            Condition("EMA20 beyond EMA50", (e20 - e50) * s > 0, f"EMA20 {fmt(e20)}, EMA50 {fmt(e50)}"),
            Condition(
                f"close {_dir(s)} VWAP",
                (bar.close - v) * s > 0,
                f"close {fmt(bar.close)}, VWAP {fmt(v)}",
            ),
            Condition(
                "VWAP sloping with trend",
                (v - v6) * s > 0,
                f"VWAP change over 6 bars {fmt(v - v6)}",
            ),
            Condition(
                "pullback tested VWAP/EMA20",
                near,
                f"wick {fmt(wick)}, EMA20 {fmt(e20)}, VWAP {fmt(v)}",
            ),
            Condition("closed back beyond EMA20", (bar.close - e20) * s > 0, f"close {fmt(bar.close)}"),
            Condition(
                "with-trend body",
                (bar.close - bar.open) * s > 0,
                f"open {fmt(bar.open)}, close {fmt(bar.close)}",
            ),
        ]
        return conds, extreme - s * 0.25 * a, None


class LiquiditySweepReversal(Strategy):
    spec = StrategySpec(
        id="sweep_reversal",
        code="C",
        name="Liquidity Sweep Reversal",
        timeframe="5m",
        required_data="1-minute OHLCV covering the prior regular session and the overnight session.",
        eligible_instruments=ALL_SYMBOLS,
        regime="Any; requires a defined reference level (prior-day high/low or overnight high/low).",
        entry="Bar trades at least 1 tick through a reference level and closes back inside it, "
        "with the rejection wick >= 50% of the bar range. Trade against the sweep.",
        invalidation="Any trade beyond the sweep extreme.",
        stop="Sweep bar extreme plus/minus 0.1 ATR.",
        targets="1R and 2R.",
        time_exit=_TIME_EXIT,
        testing=_TESTING,
    )
    target_r = (1.0, 2.0)
    window = (time(9, 35), time(15, 30))

    def _side(self, ctx: MarketContext, s: int) -> Side:
        bar, a, lv = ctx.m5[-1], ctx.atr14[-1] or 0.0, ctx.levels
        # A long fades a sweep of lows; a short fades a sweep of highs.
        names = (
            (("prior-day low", lv.prior_day_low), ("overnight low", lv.overnight_low))
            if s == 1
            else (("prior-day high", lv.prior_day_high), ("overnight high", lv.overnight_high))
        )
        levels = [(n, x) for n, x in names if x is not None]
        if not levels:
            return (
                [Condition("reference level available", False, "no prior-day/overnight level")],
                None,
                None,
            )
        extreme = bar.low if s == 1 else bar.high
        swept = [(n, x) for n, x in levels if (x - extreme) * s >= ctx.tick and (bar.close - x) * s > 0]
        rng = bar.high - bar.low
        wick = (min(bar.open, bar.close) - bar.low) if s == 1 else (bar.high - max(bar.open, bar.close))
        name, level = swept[0] if swept else levels[0]
        conds = [
            Condition("reference level available", True, ", ".join(f"{n} {fmt(x)}" for n, x in levels)),
            Condition(
                "swept and closed back inside",
                bool(swept),
                f"{name} {fmt(level)}: extreme {fmt(extreme)}, close {fmt(bar.close)}",
            ),
            Condition(
                "rejection wick >= 50% of range",
                rng > 0 and wick / rng >= 0.5,
                f"wick {fmt(wick)} of range {fmt(rng)}",
            ),
        ]
        return conds, extreme - s * 0.1 * a, None


class BreakoutRetest(Strategy):
    spec = StrategySpec(
        id="breakout_retest",
        code="D",
        name="Breakout and Retest",
        timeframe="5m",
        required_data="At least 46 closed 5-minute bars.",
        eligible_instruments=ALL_SYMBOLS,
        regime="A consolidation level: the highest high / lowest low of bars t-45..t-6.",
        entry="A close beyond the level occurred in bars t-5..t-1; the current bar retests the "
        "level (wick within 0.25 ATR of it) and closes beyond it with a with-trend body.",
        invalidation="A 5m close back through the level by more than 0.25 ATR.",
        stop="Level minus/plus 0.75 ATR.",
        targets="1.5R and 3R.",
        time_exit=_TIME_EXIT,
        testing=_TESTING,
    )
    target_r = (1.5, 3.0)

    def _side(self, ctx: MarketContext, s: int) -> Side:
        bars, a = ctx.m5, ctx.atr14[-1] or 0.0
        base = bars[-46:-6]
        level = max(b.high for b in base) if s == 1 else min(b.low for b in base)
        broke = any((b.close - level) * s > 0 for b in bars[-6:-1])
        bar = bars[-1]
        wick = bar.low if s == 1 else bar.high
        conds = [
            Condition("breakout in prior 5 bars", broke, f"level {fmt(level)}"),
            Condition(
                "retest within 0.25 ATR",
                (wick - level) * s <= 0.25 * a,
                f"wick {fmt(wick)}, level {fmt(level)}, ATR {fmt(a)}",
            ),
            Condition("close holds beyond level", (bar.close - level) * s > 0, f"close {fmt(bar.close)}"),
            Condition("with-trend body", (bar.close - bar.open) * s > 0, f"open {fmt(bar.open)}"),
        ]
        return conds, level - s * 0.75 * a, None


class MomentumPullback(Strategy):
    spec = StrategySpec(
        id="momentum_pullback",
        code="E",
        name="Momentum Pullback",
        timeframe="5m",
        required_data="At least 60 closed 5-minute bars.",
        eligible_instruments=ALL_SYMBOLS,
        regime="Momentum: EMA20 beyond EMA50 and RSI(14) reached >= 60 (long) / <= 40 (short) "
        "within the last 12 bars.",
        entry="At least 2 of the previous 3 bars closed against the trend, and the current bar "
        "tags EMA20 and closes beyond it with a with-trend body.",
        invalidation="A 5m close beyond EMA50 against the trend.",
        stop="Extreme of the last 5 bars minus/plus 0.1 ATR.",
        targets="1.5R and 2.5R.",
        time_exit=_TIME_EXIT,
        testing=_TESTING,
    )

    def _side(self, ctx: MarketContext, s: int) -> Side:
        bars, a = ctx.m5, ctx.atr14[-1] or 0.0
        e20, e50 = ctx.ema20[-1] or 0.0, ctx.ema50[-1] or 0.0
        recent_rsi = [x for x in ctx.rsi14[-12:] if x is not None]
        peak = (max(recent_rsi) if s == 1 else min(recent_rsi)) if recent_rsi else None
        strong = peak is not None and (peak >= 60 if s == 1 else peak <= 40)
        counter = sum(1 for b in bars[-4:-1] if (b.close - b.open) * s < 0)
        bar = bars[-1]
        wick = bar.low if s == 1 else bar.high
        five = bars[-5:]
        extreme = min(b.low for b in five) if s == 1 else max(b.high for b in five)
        conds = [
            Condition("EMA20 beyond EMA50", (e20 - e50) * s > 0, f"EMA20 {fmt(e20)}, EMA50 {fmt(e50)}"),
            Condition("recent RSI momentum", strong, f"RSI extreme over 12 bars {fmt(peak)}"),
            Condition("pullback (2 of 3 counter-trend bars)", counter >= 2, f"{counter} of 3"),
            Condition("tagged EMA20", (wick - e20) * s <= 0, f"wick {fmt(wick)}, EMA20 {fmt(e20)}"),
            Condition(
                "closed beyond EMA20 with trend",
                (bar.close - e20) * s > 0 and (bar.close - bar.open) * s > 0,
                f"open {fmt(bar.open)}, close {fmt(bar.close)}",
            ),
        ]
        return conds, extreme - s * 0.1 * a, None


class VolatilityExpansion(Strategy):
    spec = StrategySpec(
        id="vol_expansion",
        code="F",
        name="Volatility Expansion",
        timeframe="5m",
        required_data="At least 120 closed 5-minute bars (Bollinger width history).",
        eligible_instruments=ALL_SYMBOLS,
        regime="Squeeze: Bollinger(20, 2) width in bars t-6..t-1 touched the lowest 20% of its "
        "last 100 values.",
        entry="Current bar range >= 1.5 ATR and close outside the Bollinger band; trade in the "
        "direction of the close.",
        invalidation="A 5m close back inside the band beyond the middle band.",
        stop="Bollinger middle band (SMA20).",
        targets="1.5R and 2.5R.",
        time_exit=_TIME_EXIT,
        testing=_TESTING,
    )

    def _side(self, ctx: MarketContext, s: int) -> Side:
        mid, up, lo = ctx.boll
        widths = [u - d for u, d in zip(up, lo, strict=True) if u is not None and d is not None]
        if len(widths) < 100 or mid[-1] is None:
            return [Condition("width history", False, f"{len(widths)} of 100 bars")], None, None
        hist = sorted(widths[-101:-1])
        threshold = hist[len(hist) // 5]
        squeeze = min(widths[-7:-1]) <= threshold
        bar, a = ctx.m5[-1], ctx.atr14[-2] or 0.0
        band = up[-1] if s == 1 else lo[-1]
        assert band is not None
        conds = [
            Condition(
                "prior squeeze (lowest 20% width)",
                squeeze,
                f"min recent width {fmt(min(widths[-7:-1]))}, threshold {fmt(threshold)}",
            ),
            Condition(
                "range >= 1.5 ATR",
                bar.high - bar.low >= 1.5 * a > 0,
                f"range {fmt(bar.high - bar.low)}, ATR {fmt(a)}",
            ),
            Condition(
                "close outside band",
                (bar.close - band) * s > 0,
                f"close {fmt(bar.close)}, band {fmt(band)}",
            ),
        ]
        return conds, mid[-1], None


class MeanReversion(Strategy):
    spec = StrategySpec(
        id="mean_reversion",
        code="G",
        name="Mean Reversion",
        timeframe="5m",
        required_data="5-minute OHLCV for the current regular session (VWAP and sigma) plus 50 bars.",
        eligible_instruments=ALL_SYMBOLS,
        regime="Range: |EMA20 - EMA50| <= 0.5 ATR (no established trend).",
        entry="Bar extreme beyond VWAP -/+ 2 sigma, RSI(14) <= 30 (long) / >= 70 (short), and the "
        "bar closes back toward VWAP (reversal body). Reward to VWAP must be >= 1R.",
        invalidation="EMA20/EMA50 separation exceeds 1 ATR (trend emerging).",
        stop="Signal bar extreme minus/plus 0.5 ATR.",
        targets="Target 1: VWAP. Target 2: VWAP +/- 1 sigma on the far side.",
        time_exit=_TIME_EXIT,
        testing=_TESTING,
    )
    window = (time(10, 0), time(15, 30))

    def _side(self, ctx: MarketContext, s: int) -> Side:
        bar, a = ctx.m5[-1], ctx.atr14[-1] or 0.0
        v, sd = ctx.vwap[0][-1], ctx.vwap[1][-1]
        r = ctx.rsi14[-1]
        e20, e50 = ctx.ema20[-1] or 0.0, ctx.ema50[-1] or 0.0
        if v is None or sd is None or r is None or sd <= 0:
            return (
                [Condition("VWAP bands available", False, "needs regular-session bars")],
                None,
                None,
            )
        band = v - s * 2 * sd
        extreme = bar.low if s == 1 else bar.high
        stop = extreme - s * 0.5 * a
        risk = (bar.close - stop) * s
        reward = (v - bar.close) * s
        conds = [
            Condition(
                "range regime",
                abs(e20 - e50) <= 0.5 * a,
                f"|EMA20-EMA50| {fmt(abs(e20 - e50))}, ATR {fmt(a)}",
            ),
            Condition(
                "stretched beyond 2 sigma",
                (band - extreme) * s >= 0,
                f"extreme {fmt(extreme)}, band {fmt(band)}, VWAP {fmt(v)}",
            ),
            Condition("RSI extreme", r <= 30 if s == 1 else r >= 70, f"RSI {fmt(r)}"),
            Condition(
                "reversal body",
                (bar.close - bar.open) * s > 0,
                f"open {fmt(bar.open)}, close {fmt(bar.close)}",
            ),
            Condition(
                "reward to VWAP >= 1R",
                risk > 0 and reward >= risk,
                f"reward {fmt(reward)}, risk {fmt(risk)}",
            ),
        ]
        return conds, stop, (v, v + s * sd)


class MultiTimeframeTrend(Strategy):
    spec = StrategySpec(
        id="mtf_trend",
        code="H",
        name="Multi-Timeframe Trend Alignment",
        timeframe="5m / 15m / 1h",
        required_data="Enough 1-minute history for EMA50 on 1-hour bars (about 3 trading days).",
        eligible_instruments=ALL_SYMBOLS,
        regime="EMA20 beyond EMA50 in the same direction on 5-minute, 15-minute and 1-hour bars.",
        entry="5m close beyond EMA20 and beyond the highest high / lowest low of the previous 3 bars.",
        invalidation="Loss of alignment on the 15-minute timeframe.",
        stop="Extreme of the last 5 bars minus/plus 0.25 ATR.",
        targets="1.5R and 3R.",
        time_exit=_TIME_EXIT,
        testing=_TESTING,
    )
    target_r = (1.5, 3.0)

    def _side(self, ctx: MarketContext, s: int) -> Side:
        bars, a = ctx.m5, ctx.atr14[-1] or 0.0
        t5, t15, t60 = ctx.trend(Timeframe.M5), ctx.trend(Timeframe.M15), ctx.trend(Timeframe.H1)
        bar, prev3, five = bars[-1], bars[-4:-1], bars[-5:]
        trigger = max(b.high for b in prev3) if s == 1 else min(b.low for b in prev3)
        extreme = min(b.low for b in five) if s == 1 else max(b.high for b in five)
        e20 = ctx.ema20[-1] or 0.0

        def lab(t: int | None) -> str:
            return "insufficient history" if t is None else {1: "up", -1: "down", 0: "flat"}[t]

        conds = [
            Condition("5m trend aligned", t5 == s, lab(t5)),
            Condition("15m trend aligned", t15 == s, lab(t15)),
            Condition("1h trend aligned", t60 == s, lab(t60)),
            Condition(
                "close beyond EMA20",
                (bar.close - e20) * s > 0,
                f"close {fmt(bar.close)}, EMA20 {fmt(e20)}",
            ),
            Condition(
                "close beyond prior 3-bar extreme",
                (bar.close - trigger) * s > 0,
                f"trigger {fmt(trigger)}",
            ),
        ]
        return conds, extreme - s * 0.25 * a, None


STRATEGIES: tuple[Strategy, ...] = (
    OpeningRangeBreakout(),
    VwapTrendContinuation(),
    LiquiditySweepReversal(),
    BreakoutRetest(),
    MomentumPullback(),
    VolatilityExpansion(),
    MeanReversion(),
    MultiTimeframeTrend(),
)
STRATEGY_BY_ID = {s.spec.id: s for s in STRATEGIES}
