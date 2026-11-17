"""Smart-money-concepts price-action detection: deterministic, causal, explainable.

Every detector is a pure function of closed bars. A pattern carries the bar at which it formed
and the bar at which its defining rule was first satisfied using only bars up to that point
(``confirmed_at``). Status (mitigated, filled, invalidated...) is evaluated as of the last bar
passed in, so running the analysis on a truncated series reproduces exactly what was knowable
then - replay and backtests cannot see the future.

These are operational price-action definitions. Names such as "order block" or "liquidity" are
conventional labels for candle geometry; nothing here observes orders, stops or institutions.
"""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.marketdata.calendar import trading_day
from app.research.bars import Bar
from app.research.indicators import atr, ema, is_rth, rsi, session_vwap

Dir = Literal["bullish", "bearish", "neutral"]

DISCLAIMER = (
    "Patterns are rule-based candle geometry. They do not observe resting orders, stops or "
    "institutional activity, and a detected pattern is not a prediction."
)


@dataclass(frozen=True, slots=True)
class SmcConfig:
    #: A swing high/low needs ``pivot_left`` bars before and ``pivot_right`` bars after it;
    #: it is CONFIRMED only once the right-hand bars have closed.
    pivot_left: int = 3
    pivot_right: int = 3
    #: Equal highs/lows: within max(eq_ticks ticks, eq_atr x ATR).
    eq_ticks: int = 2
    eq_atr: float = 0.1
    #: Bars after a sweep in which a close back through the level confirms a rejection.
    sweep_window: int = 3
    #: Displacement: candle body >= displacement_atr x ATR(14).
    displacement_atr: float = 1.2
    #: Order block search: bars before the structure break to look for the last opposing candle.
    ob_lookback: int = 10
    #: Fair value gaps smaller than max(fvg_min_ticks ticks, fvg_min_atr x ATR) are ignored.
    fvg_min_ticks: int = 2
    fvg_min_atr: float = 0.15
    #: Supply/demand base candles: range <= base_atr x ATR, 1..base_max candles.
    base_atr: float = 0.6
    base_max: int = 3
    atr_period: int = 14
    #: Limit per pattern family in the output (most recent first).
    max_zones: int = 12


@dataclass(slots=True)
class Pattern:
    id: str
    type: str
    direction: Dir
    formed_at: datetime
    confirmed_at: datetime | None
    top: float
    bottom: float
    status: str
    invalidation: str
    invalidation_price: float | None
    evidence: list[str]
    quality: int
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["formed_at"] = self.formed_at.isoformat()
        d["confirmed_at"] = self.confirmed_at.isoformat() if self.confirmed_at else None
        return d


def _pid(*parts: object) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode(), usedforsecurity=False).hexdigest()[:12]


def _atr_at(series: list[float | None], i: int, fallback: float) -> float:
    v = series[i] if 0 <= i < len(series) else None
    return v if v else fallback


def causal_atr(bars: Sequence[Bar], period: int) -> list[float | None]:
    """Wilder ATR, with the warm-up bars filled by the mean range of bars up to and including
    each bar (never later bars), so early-bar thresholds stay causal."""
    out = atr(bars, period)
    total = 0.0
    for i, b in enumerate(bars):
        total += b.high - b.low
        if out[i] is None:
            out[i] = total / (i + 1)
    return out


# --------------------------------------------------------------------------------------------- swings


@dataclass(frozen=True, slots=True)
class Swing:
    index: int
    kind: Literal["high", "low"]
    price: float
    confirmed_index: int | None  # None = provisional
    label: str = ""  # HH / LH / HL / LL relative to the previous swing of the same kind


def swings(bars: Sequence[Bar], cfg: SmcConfig = SmcConfig()) -> list[Swing]:
    """Pivot swings. Ties on the left are not pivots; ties on the right are allowed (first wins)."""
    out: list[Swing] = []
    n, left, right = len(bars), cfg.pivot_left, cfg.pivot_right
    for i in range(left, n):
        lh = all(bars[i].high > bars[j].high for j in range(i - left, i))
        ll = all(bars[i].low < bars[j].low for j in range(i - left, i))
        avail = min(right, n - 1 - i)
        rh = all(bars[i].high >= bars[j].high for j in range(i + 1, i + 1 + avail))
        rl = all(bars[i].low <= bars[j].low for j in range(i + 1, i + 1 + avail))
        conf = i + right if avail == right else None
        if lh and rh:
            out.append(Swing(i, "high", bars[i].high, conf))
        if ll and rl:
            out.append(Swing(i, "low", bars[i].low, conf))
    # Labels compare confirmed swings only.
    labelled: list[Swing] = []
    prev: dict[str, float] = {}
    for s in out:
        label = ""
        if s.confirmed_index is not None:
            p = prev.get(s.kind)
            if p is not None:
                if s.kind == "high":
                    label = "HH" if s.price > p else "LH" if s.price < p else "EQH"
                else:
                    label = "HL" if s.price > p else "LL" if s.price < p else "EQL"
            prev[s.kind] = s.price
        labelled.append(Swing(s.index, s.kind, s.price, s.confirmed_index, label))
    return labelled


# ----------------------------------------------------------------------------------- market structure


@dataclass(frozen=True, slots=True)
class Break:
    index: int
    direction: Dir
    kind: Literal["BOS", "CHoCH"]
    level: float
    swing_index: int
    displacement: bool


def structure_breaks(
    bars: Sequence[Bar], sw: Sequence[Swing], cfg: SmcConfig, atr14: list[float | None]
) -> list[Break]:
    """A close beyond the most recent CONFIRMED swing (confirmed before that bar) breaks structure.

    The first break, or one in the same direction as the prior break, is a BOS; a break against
    the prior break's direction is a CHoCH. Each swing can be broken once.
    """
    by_conf: dict[int, list[Swing]] = {}
    for s in sw:
        if s.confirmed_index is not None:
            by_conf.setdefault(s.confirmed_index, []).append(s)
    last_high: Swing | None = None
    last_low: Swing | None = None
    trend: Dir = "neutral"
    out: list[Break] = []
    fb = 1e-9
    for i, b in enumerate(bars):
        # Breaks are judged against swings already confirmed at the previous bar's close.
        if last_high is not None and b.close > last_high.price:
            disp = abs(b.close - b.open) >= cfg.displacement_atr * _atr_at(atr14, i, fb)
            kind = "CHoCH" if trend == "bearish" else "BOS"
            out.append(Break(i, "bullish", kind, last_high.price, last_high.index, disp))
            trend, last_high = "bullish", None
        if last_low is not None and b.close < last_low.price:
            disp = abs(b.close - b.open) >= cfg.displacement_atr * _atr_at(atr14, i, fb)
            kind = "CHoCH" if trend == "bullish" else "BOS"
            out.append(Break(i, "bearish", kind, last_low.price, last_low.index, disp))
            trend, last_low = "bearish", None
        for s in by_conf.get(i, []):
            if s.kind == "high":
                last_high = s
            else:
                last_low = s
    return out


# --------------------------------------------------------------------------------------- the analysis


@dataclass
class Analysis:
    symbol: str
    timeframe: str
    as_of: datetime | None
    bars: int
    atr: float | None
    trend: Dir
    patterns: list[Pattern]
    swings: list[dict[str, Any]]
    indicators: dict[str, Any]
    premium_discount: dict[str, Any] | None
    liquidity: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "bars": self.bars,
            "atr": self.atr,
            "trend": self.trend,
            "patterns": [p.as_dict() for p in self.patterns],
            "swings": self.swings,
            "indicators": self.indicators,
            "premium_discount": self.premium_discount,
            "liquidity": self.liquidity,
            "disclaimer": DISCLAIMER,
        }


def _zone_life(
    bars: Sequence[Bar], start: int, top: float, bottom: float, bullish: bool
) -> tuple[str, int, int | None, int | None]:
    """(status, retests, first_touch_index, invalidated_index) for a zone after bar ``start``.

    Bullish zones are touched when a low trades at or below the top and invalidated by a close
    below the bottom (bearish zones mirror this). Retests count separate visits.
    """
    retests, inside, first, inval = 0, False, None, None
    for j in range(start + 1, len(bars)):
        b = bars[j]
        touch = b.low <= top if bullish else b.high >= bottom
        if touch and not inside:
            retests += 1
            first = j if first is None else first
        inside = touch
        if (bullish and b.close < bottom) or (not bullish and b.close > top):
            inval = j
            break
    status = "invalidated" if inval is not None else "mitigated" if retests else "active"
    return status, retests, first, inval


def analyze(
    symbol: str,
    timeframe: str,
    bars: Sequence[Bar],
    tick: float,
    cfg: SmcConfig = SmcConfig(),
) -> Analysis:
    """Run every detector over closed ``bars`` (ascending). Uses no information after bars[-1]."""
    bars = list(bars)
    n = len(bars)
    if n < cfg.pivot_left + cfg.pivot_right + 3:
        return Analysis(symbol, timeframe, bars[-1].ts if bars else None, n, None, "neutral", [], [], {}, None, [])
    atr14 = causal_atr(bars, cfg.atr_period)
    fb = tick
    cur_atr = _atr_at(atr14, n - 1, fb)
    ts = [b.ts for b in bars]
    patterns: list[Pattern] = []

    sw = swings(bars, cfg)
    breaks = structure_breaks(bars, sw, cfg, atr14)
    trend: Dir = breaks[-1].direction if breaks else "neutral"

    # --- structure: BOS / CHoCH / MSS ---
    for br in breaks:
        bull = br.direction == "bullish"
        mss = br.kind == "CHoCH" and br.displacement
        ev = [
            f"Close {bars[br.index].close:g} {'above' if bull else 'below'} the confirmed swing "
            f"{'high' if bull else 'low'} {br.level:g} formed {ts[br.swing_index]:%Y-%m-%d %H:%M} UTC.",
            "Direction differs from the previous break (change of character)."
            if br.kind == "CHoCH"
            else "First break, or same direction as the previous break (continuation).",
        ]
        if br.displacement:
            ev.append(f"Break candle body >= {cfg.displacement_atr} x ATR (displacement).")
        patterns.append(
            Pattern(
                _pid(symbol, timeframe, br.kind, ts[br.index], br.level),
                "MSS" if mss else br.kind,
                br.direction,
                ts[br.swing_index],
                ts[br.index],
                br.level,
                br.level,
                "confirmed",
                f"A close back {'below' if bull else 'above'} the broken level weakens the break.",
                br.level,
                ev,
                min(100, 50 + (25 if br.displacement else 0) + (15 if br.kind == "CHoCH" else 0)),
                {"swing_index_ts": ts[br.swing_index].isoformat(), "displacement": br.displacement},
            )
        )

    # --- equal highs / lows ---
    tol = max(cfg.eq_ticks * tick, cfg.eq_atr * cur_atr)
    conf_sw = [s for s in sw if s.confirmed_index is not None]
    eq_levels: list[tuple[str, float, int, int, int]] = []  # kind, price, first idx, second idx, confirmed idx
    for kind in ("high", "low"):
        same = [s for s in conf_sw if s.kind == kind]
        for a, b2 in itertools.pairwise(same):
            if abs(a.price - b2.price) <= tol:
                lvl = max(a.price, b2.price) if kind == "high" else min(a.price, b2.price)
                assert b2.confirmed_index is not None
                taken = any(
                    (bars[j].high >= lvl + tick) if kind == "high" else (bars[j].low <= lvl - tick)
                    for j in range(b2.confirmed_index + 1, n)
                )
                eq_levels.append((kind, lvl, a.index, b2.index, b2.confirmed_index))
                patterns.append(
                    Pattern(
                        _pid(symbol, timeframe, "EQ", kind, ts[a.index], ts[b2.index]),
                        "equal_highs" if kind == "high" else "equal_lows",
                        "neutral",
                        ts[a.index],
                        ts[b2.confirmed_index],
                        lvl,
                        lvl,
                        "swept" if taken else "active",
                        "Not applicable: a reference level. It is consumed when price trades through it.",
                        None,
                        [
                            f"Swing {kind}s {a.price:g} and {b2.price:g} within {tol:g} "
                            f"(max({cfg.eq_ticks} ticks, {cfg.eq_atr} x ATR)).",
                            "Inferred reference level; resting orders are not observed.",
                        ],
                        55,
                    )
                )

    # --- reference levels for sweeps: swings, equal levels, prior day / overnight / session ---
    refs: list[tuple[str, str, float, int]] = []  # (name, side buy|sell, price, available-from index)
    for s in conf_sw:
        assert s.confirmed_index is not None
        refs.append((f"swing {s.kind}", "buy" if s.kind == "high" else "sell", s.price, s.confirmed_index))
    for kind, lvl, _a, _b, c in eq_levels:
        refs.append((f"equal {kind}s", "buy" if kind == "high" else "sell", lvl, c))
    refs.extend(_session_refs(bars))

    # --- liquidity sweeps ---
    sweeps: list[Pattern] = []
    for name, side, lvl, avail in refs:
        buy = side == "buy"
        for j in range(avail + 1, n):
            b = bars[j]
            if (buy and b.high >= lvl + tick) or (not buy and b.low <= lvl - tick):
                extreme = b.high if buy else b.low
                status, conf_j = "candidate", None
                last_j = min(n - 1, j + cfg.sweep_window)
                for k in range(j, last_j + 1):
                    extreme = max(extreme, bars[k].high) if buy else min(extreme, bars[k].low)
                    if (buy and bars[k].close < lvl) or (not buy and bars[k].close > lvl):
                        status, conf_j = "confirmed_rejection", k
                        break
                else:
                    if j + cfg.sweep_window <= n - 1:
                        status, conf_j = "breakout_continuation", j + cfg.sweep_window
                if status == "confirmed_rejection" and conf_j is not None:
                    for k in range(conf_j + 1, n):
                        if (buy and bars[k].close > extreme) or (not buy and bars[k].close < extreme):
                            status = "invalidated"
                            break
                sweeps.append(
                    Pattern(
                        _pid(symbol, timeframe, "sweep", name, lvl, ts[j]),
                        "buy_side_sweep" if buy else "sell_side_sweep",
                        "bearish" if buy else "bullish",
                        ts[j],
                        ts[conf_j] if conf_j is not None else None,
                        max(lvl, extreme),
                        min(lvl, extreme),
                        status,
                        f"A close {'above' if buy else 'below'} the sweep extreme {extreme:g} "
                        "invalidates the rejection.",
                        extreme,
                        [
                            f"Reference: {name} at {lvl:g} (known from {ts[min(avail, n - 1)]:%Y-%m-%d %H:%M} UTC).",
                            f"Bar {ts[j]:%H:%M} UTC traded {'above' if buy else 'below'} it "
                            f"to {b.high if buy else b.low:g}.",
                            {
                                "candidate": f"Waiting up to {cfg.sweep_window} bars for a close back "
                                f"{'below' if buy else 'above'} the level.",
                                "confirmed_rejection": f"Closed back {'below' if buy else 'above'} the level "
                                f"within {cfg.sweep_window} bars (rejection).",
                                "breakout_continuation": f"Held {'above' if buy else 'below'} the level for "
                                f"{cfg.sweep_window} bars (breakout, not a rejection).",
                                "invalidated": "Rejection later invalidated by a close beyond the sweep extreme.",
                            }[status],
                            "Stop-order concentrations are not observable from candles; the level is inferred.",
                        ],
                        min(
                            100,
                            40
                            + (30 if status == "confirmed_rejection" else 0)
                            + (15 if "equal" in name else 0)
                            + (10 if name.startswith(("prior", "overnight")) else 0),
                        ),
                        {"level": lvl, "level_name": name, "sweep_bar": ts[j].isoformat(), "extreme": extreme},
                    )
                )
                break  # a level is consumed by its first sweep
    patterns.extend(sweeps)

    # --- order blocks: last opposing candle before a displacement leg that breaks structure ---
    for br in breaks:
        bull = br.direction == "bullish"
        lo = max(0, br.index - cfg.ob_lookback)
        ob = None
        for k in range(br.index - 1, lo - 1, -1):
            c = bars[k]
            if (bull and c.close < c.open) or (not bull and c.close > c.open):
                ob = k
                break
        if ob is None:
            continue
        disp = any(
            abs(bars[k].close - bars[k].open) >= cfg.displacement_atr * _atr_at(atr14, k, fb)
            and ((bars[k].close > bars[k].open) == bull)
            for k in range(ob + 1, br.index + 1)
        )
        if not disp:
            continue
        top, bottom = bars[ob].high, bars[ob].low
        status, retests, _first, _inv = _zone_life(bars, br.index, top, bottom, bull)
        patterns.append(
            Pattern(
                _pid(symbol, timeframe, "OB", ts[ob], br.index),
                "bullish_order_block" if bull else "bearish_order_block",
                br.direction,
                ts[ob],
                ts[br.index],
                top,
                bottom,
                status,
                f"A close {'below' if bull else 'above'} {bottom if bull else top:g} invalidates the zone.",
                bottom if bull else top,
                [
                    f"Last {'down' if bull else 'up'}-close candle ({ts[ob]:%H:%M} UTC) before a displacement leg.",
                    f"Leg broke structure ({br.kind}) at {ts[br.index]:%H:%M} UTC through {br.level:g}.",
                    f"Retests since confirmation: {retests}.",
                    "Candidate zone from candle geometry, not confirmed institutional activity.",
                ],
                min(
                    100,
                    45
                    + (15 if br.kind == "CHoCH" else 0)
                    + (15 if status == "active" else 0)
                    + (10 if br.displacement else 0),
                ),
                {"retests": retests, "break": br.kind},
            )
        )

    # --- fair value gaps and inverse FVGs ---
    min_gap_abs = cfg.fvg_min_ticks * tick
    for i in range(2, n):
        a, c = bars[i - 2], bars[i]
        for bull in (True, False):
            if bull and c.low > a.high:
                top, bottom = c.low, a.high
            elif not bull and c.high < a.low:
                top, bottom = a.low, c.high
            else:
                continue
            size = top - bottom
            need = max(min_gap_abs, cfg.fvg_min_atr * _atr_at(atr14, i, fb))
            if size < need:
                continue
            deepest = None
            status, inv_j = "open", None
            for j in range(i + 1, n):
                b = bars[j]
                pen = b.low if bull else b.high
                deepest = pen if deepest is None else (min(deepest, pen) if bull else max(deepest, pen))
                if (bull and b.close < bottom) or (not bull and b.close > top):
                    status, inv_j = "invalidated", j
                    break
            fill = 0.0
            if deepest is not None:
                fill = (top - deepest) / size if bull else (deepest - bottom) / size
                fill = max(0.0, min(1.0, fill))
            if status != "invalidated":
                status = "filled" if fill >= 1.0 else "partially_filled" if fill > 0 else "open"
            d: Dir = "bullish" if bull else "bearish"
            patterns.append(
                Pattern(
                    _pid(symbol, timeframe, "FVG", ts[i], bull),
                    "bullish_fvg" if bull else "bearish_fvg",
                    d,
                    ts[i - 1],
                    ts[i],
                    top,
                    bottom,
                    status,
                    f"A close {'below' if bull else 'above'} {bottom if bull else top:g} invalidates the gap.",
                    bottom if bull else top,
                    [
                        f"Three-candle gap: {'low' if bull else 'high'} of {ts[i]:%H:%M} UTC "
                        f"{'above' if bull else 'below'} the {'high' if bull else 'low'} of {ts[i - 2]:%H:%M} UTC.",
                        f"Size {size:g} >= minimum {need:g} (max({cfg.fvg_min_ticks} ticks, {cfg.fvg_min_atr} x ATR)).",
                        f"Filled {fill:.0%} as of the last closed bar.",
                    ],
                    min(100, int(40 + 30 * min(size / max(cur_atr, 1e-9), 1.0)) + (10 if status == "open" else 0)),
                    {"fill": round(fill, 3)},
                )
            )
            if inv_j is not None:
                # Inverse FVG: an invalidated gap whose bounds are then treated with opposite polarity.
                inv_bull = not bull
                st, retests, _f, _iv = _zone_life(bars, inv_j, top, bottom, inv_bull)
                patterns.append(
                    Pattern(
                        _pid(symbol, timeframe, "IFVG", ts[i], bull),
                        "inverse_fvg",
                        "bullish" if inv_bull else "bearish",
                        ts[i - 1],
                        ts[inv_j],
                        top,
                        bottom,
                        st,
                        f"A close {'below' if inv_bull else 'above'} {bottom if inv_bull else top:g} invalidates it.",
                        bottom if inv_bull else top,
                        [
                            f"{'Bullish' if bull else 'Bearish'} gap closed through at {ts[inv_j]:%H:%M} UTC; "
                            "rule: the gap's bounds now act with opposite polarity.",
                            f"Retests since inversion: {retests}.",
                        ],
                        45,
                        {"retests": retests},
                    )
                )

    # --- supply / demand: small-range base followed by displacement away ---
    for i in range(1, n):
        c = bars[i]
        body = c.close - c.open
        if abs(body) < cfg.displacement_atr * _atr_at(atr14, i, fb):
            continue
        base = []
        for k in range(i - 1, max(-1, i - 1 - cfg.base_max), -1):
            if bars[k].high - bars[k].low <= cfg.base_atr * _atr_at(atr14, k, fb):
                base.append(k)
            else:
                break
        if not base:
            continue
        bull = body > 0
        top = max(bars[k].high for k in base)
        bottom = min(bars[k].low for k in base)
        status, retests, _f, _iv = _zone_life(bars, i, top, bottom, bull)
        context = "with" if trend == ("bullish" if bull else "bearish") else "against"
        patterns.append(
            Pattern(
                _pid(symbol, timeframe, "SD", ts[min(base)], i),
                "demand_zone" if bull else "supply_zone",
                "bullish" if bull else "bearish",
                ts[min(base)],
                ts[i],
                top,
                bottom,
                status,
                f"A close {'below' if bull else 'above'} {bottom if bull else top:g} invalidates the zone.",
                bottom if bull else top,
                [
                    f"{len(base)} base candle(s) with range <= {cfg.base_atr} x ATR.",
                    f"Displacement candle {ts[i]:%H:%M} UTC, body {abs(body):g} >= {cfg.displacement_atr} x ATR.",
                    f"Current structure trend is {trend}; zone is {context} it.",
                    f"Retests since formation: {retests}.",
                ],
                min(
                    100, 40 + (15 if context == "with" else 0) + (15 if status == "active" else 0) + 5 * (3 - len(base))
                ),
                {"retests": retests, "trend_context": context},
            )
        )

    # --- breakout and retest of a broken structure level ---
    for br in breaks:
        bull = br.direction == "bullish"
        tol_r = max(2 * tick, 0.2 * _atr_at(atr14, br.index, fb))
        for j in range(br.index + 1, min(n, br.index + 30)):
            b = bars[j]
            if (bull and b.close < br.level) or (not bull and b.close > br.level):
                break  # lost the level before a retest
            if (bull and b.low <= br.level + tol_r) or (not bull and b.high >= br.level - tol_r):
                patterns.append(
                    Pattern(
                        _pid(symbol, timeframe, "BRT", ts[br.index], ts[j]),
                        "breakout_retest",
                        br.direction,
                        ts[br.index],
                        ts[j],
                        br.level + tol_r,
                        br.level - tol_r,
                        "confirmed",
                        f"A close {'below' if bull else 'above'} {br.level:g} fails the retest.",
                        br.level,
                        [
                            f"{br.kind} through {br.level:g} at {ts[br.index]:%H:%M} UTC.",
                            f"Bar {ts[j]:%H:%M} UTC returned within {tol_r:g} of the level "
                            "and closed on the break side.",
                        ],
                        55,
                    )
                )
                break

    # --- momentum divergence at the last two confirmed swings (RSI 14) ---
    closes = [b.close for b in bars]
    r = rsi(closes, 14)
    for kind in ("high", "low"):
        same = [s for s in conf_sw if s.kind == kind]
        if len(same) >= 2:
            a, b2 = same[-2], same[-1]
            ra, rb = r[a.index], r[b2.index]
            if ra is not None and rb is not None:
                bear = kind == "high" and b2.price > a.price and rb < ra
                bull = kind == "low" and b2.price < a.price and rb > ra
                if bear or bull:
                    assert b2.confirmed_index is not None
                    patterns.append(
                        Pattern(
                            _pid(symbol, timeframe, "DIV", ts[a.index], ts[b2.index]),
                            "momentum_divergence",
                            "bearish" if bear else "bullish",
                            ts[b2.index],
                            ts[b2.confirmed_index],
                            b2.price,
                            b2.price,
                            "confirmed",
                            "Divergence is descriptive only; it has no fixed invalidation level.",
                            None,
                            [
                                f"Price {'higher high' if bear else 'lower low'} {a.price:g} -> {b2.price:g}; "
                                f"RSI(14) {ra:.1f} -> {rb:.1f}.",
                            ],
                            45,
                        )
                    )

    # Keep the output readable: most recent first, at most max_zones per type.
    patterns.sort(key=lambda p: p.confirmed_at or p.formed_at, reverse=True)
    kept: list[Pattern] = []
    per: dict[str, int] = {}
    for p in patterns:
        per[p.type] = per.get(p.type, 0) + 1
        if per[p.type] <= cfg.max_zones:
            kept.append(p)

    indicators = _indicators(bars, atr14)
    pd = _premium_discount(conf_sw, closes[-1])
    liq = liquidity_levels(bars, conf_sw, eq_levels, sweeps, closes[-1], cur_atr, tick)
    sw_out = [
        {
            "time": ts[s.index].isoformat(),
            "kind": s.kind,
            "price": s.price,
            "label": s.label,
            "confirmed": s.confirmed_index is not None,
            "confirmed_at": ts[s.confirmed_index].isoformat() if s.confirmed_index is not None else None,
        }
        for s in sw[-40:]
    ]
    return Analysis(symbol, timeframe, ts[-1], n, cur_atr, trend, kept, sw_out, indicators, pd, liq)


def _session_refs(bars: Sequence[Bar]) -> list[tuple[str, str, float, int]]:
    """Prior-day high/low (whole trading day) and overnight high/low, available from the first bar
    of the next period, computed from the bars themselves (no look-ahead)."""
    refs: list[tuple[str, str, float, int]] = []
    day_hl: dict[Any, list[float]] = {}
    order: list[Any] = []
    on: dict[Any, list[float]] = {}
    first_rth: dict[Any, int] = {}
    first_of_day: dict[Any, int] = {}
    intraday = len(bars) > 1 and (bars[1].ts - bars[0].ts).total_seconds() < 86400
    for i, b in enumerate(bars):
        d = trading_day(b.ts) if intraday else b.ts.date()
        if d not in day_hl:
            day_hl[d] = [b.high, b.low]
            order.append(d)
            first_of_day[d] = i
        else:
            day_hl[d][0] = max(day_hl[d][0], b.high)
            day_hl[d][1] = min(day_hl[d][1], b.low)
        if intraday:
            if is_rth(b.ts):
                first_rth.setdefault(d, i)
            elif d not in first_rth:
                if d not in on:
                    on[d] = [b.high, b.low]
                else:
                    on[d][0] = max(on[d][0], b.high)
                    on[d][1] = min(on[d][1], b.low)
    for prev, cur in itertools.pairwise(order):
        h, lo = day_hl[prev]
        start = first_of_day[cur] - 1  # available from the first bar of the next day
        refs.append(("prior day high", "buy", h, start))
        refs.append(("prior day low", "sell", lo, start))
    for d, (h, lo) in on.items():
        if d in first_rth:
            refs.append(("overnight high", "buy", h, first_rth[d] - 1))
            refs.append(("overnight low", "sell", lo, first_rth[d] - 1))
    return refs


def _indicators(bars: Sequence[Bar], atr14: list[float | None]) -> dict[str, Any]:
    closes = [b.close for b in bars]
    vols = [float(b.volume) for b in bars]
    out: dict[str, Any] = {}
    for n in (9, 20, 50, 200):
        v = ema(closes, n)[-1]
        out[f"ema{n}"] = round(v, 6) if v is not None else None
    out["atr14"] = atr14[-1]
    avg = sum(vols[-21:-1]) / 20 if len(vols) > 20 else None
    out["relative_volume"] = round(vols[-1] / avg, 2) if avg else None
    short = atr(bars, 5)[-1]
    long = atr(bars, 50)[-1]
    if short and long:
        ratio = short / long
        out["volatility_ratio"] = round(ratio, 2)
        out["volatility_state"] = "compression" if ratio < 0.7 else "expansion" if ratio > 1.4 else "normal"
    else:
        out["volatility_ratio"], out["volatility_state"] = None, "unknown"
    vw, sd = session_vwap(bars)
    if vw and vw[-1] is not None:
        out["vwap"] = round(vw[-1], 6)
        out["vwap_deviation_sd"] = round((closes[-1] - vw[-1]) / sd[-1], 2) if sd[-1] else None
    else:
        out["vwap"], out["vwap_deviation_sd"] = None, None
    return out


def anchored_vwap(bars: Sequence[Bar], anchor: int) -> list[float | None]:
    """VWAP anchored at bar ``anchor`` (typical price x volume)."""
    out: list[float | None] = [None] * len(bars)
    pv = vol = 0.0
    for i in range(anchor, len(bars)):
        b = bars[i]
        pv += (b.high + b.low + b.close) / 3 * b.volume
        vol += b.volume
        out[i] = pv / vol if vol else None
    return out


def _premium_discount(conf_sw: Sequence[Swing], price: float) -> dict[str, Any] | None:
    highs = [s for s in conf_sw if s.kind == "high"]
    lows = [s for s in conf_sw if s.kind == "low"]
    if not highs or not lows:
        return None
    hi, lo = highs[-1].price, lows[-1].price
    if hi <= lo:
        return None
    eq = (hi + lo) / 2
    pos = (price - lo) / (hi - lo)
    return {
        "range_high": hi,
        "range_low": lo,
        "equilibrium": eq,
        "position": round(pos, 3),
        "zone": "above range"
        if pos > 1
        else "below range"
        if pos < 0
        else "premium"
        if price > eq
        else "discount"
        if price < eq
        else "equilibrium",
        "basis": "Most recent confirmed swing high and swing low.",
    }


def liquidity_levels(
    bars: Sequence[Bar],
    conf_sw: Sequence[Swing],
    eq_levels: Sequence[tuple[str, float, int, int, int]],
    sweeps: Sequence[Pattern],
    price: float,
    cur_atr: float,
    tick: float,
) -> list[dict[str, Any]]:
    """Unswept inferred liquidity reference levels, ranked by a transparent heuristic.

    score = base(type) + 5 x touches + recency (0-20) - proximity penalty (distance in ATR x 5).
    """
    swept = {round(p.extra.get("level", 0), 6) for p in sweeps}
    n = len(bars)
    cands: list[tuple[str, float, int]] = []
    for s in conf_sw:
        cands.append((f"swing {s.kind}", s.price, s.index))
    for kind, lvl, _a, b2, _c in eq_levels:
        cands.append((f"equal {kind}s", lvl, b2))
    for name, _side, lvl, avail in _session_refs(bars):
        cands.append((name, lvl, max(avail, 0)))
    base = {
        "equal highs": 30,
        "equal lows": 30,
        "prior day high": 25,
        "prior day low": 25,
        "overnight high": 20,
        "overnight low": 20,
        "swing high": 10,
        "swing low": 10,
    }
    tol = max(2 * tick, 0.1 * cur_atr)
    out: dict[float, dict[str, Any]] = {}
    for name, lvl, idx in cands:
        if round(lvl, 6) in swept:
            continue
        # Ignore levels already traded through after they formed.
        after = bars[idx + 1 :] if idx + 1 < n else []
        if (
            any(b.high >= lvl + tick and b.low <= lvl - tick for b in after)
            or (name.endswith(("high", "highs")) and any(b.high > lvl for b in after))
            or (name.endswith(("low", "lows")) and any(b.low < lvl for b in after))
        ):
            continue
        touches = sum(1 for b in bars[max(0, idx - 200) :] if abs((b.high if lvl >= price else b.low) - lvl) <= tol)
        recency = 20 * (idx / max(n - 1, 1))
        dist = abs(lvl - price) / max(cur_atr, 1e-9)
        score = base.get(name, 10) + 5 * min(touches, 6) + recency - 5 * min(dist, 10)
        key = round(lvl, 6)
        prev = out.get(key)
        if prev is None or score > prev["score"]:
            out[key] = {
                "name": name,
                "price": lvl,
                "side": "above" if lvl > price else "below",
                "distance": round(lvl - price, 6),
                "distance_atr": round(dist, 2),
                "touches": touches,
                "formed_at": bars[idx].ts.isoformat(),
                "score": round(score, 1),
                "label": "Inferred price-action level, not verified resting liquidity.",
            }
    return sorted(out.values(), key=lambda d: -d["score"])[:20]
