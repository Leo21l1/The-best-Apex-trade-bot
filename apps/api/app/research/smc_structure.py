"""Two-layer market structure, order blocks, fair value gaps and liquidity states.

Independent implementation of widely used Smart Money Concepts definitions. The public LuxAlgo
"Smart Money Concepts" indicator (CC BY-NC-SA 4.0, (c) LuxAlgo) was studied as a *comparison
reference* for behaviour (leg-based pivots, internal vs swing layers, BOS/CHoCH tagging,
strong/weak highs, ATR / cumulative-range order-block filter, close vs high/low mitigation).
No Pine Script code is copied; documented differences are in docs/smc-engine.md.

Causality rules (tested in tests/test_p27_smc_structure.py):
* A pivot of size ``k`` at bar ``i`` is only CONFIRMED at bar ``i + k`` (``confirmed_index``) and is
  never used before that bar closes.
* Structure breaks are judged on bar CLOSES against pivots confirmed at or before that bar.
* Zone statuses are evaluated as of the last bar passed in; truncating the input reproduces exactly
  what was knowable at that time (no repainting of past events).

Everything is candle geometry. Nothing here observes resting orders, stops or institutions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.research.bars import Bar
from app.research.indicators import true_range

Side = Literal["bullish", "bearish"]
Trend = Literal["bullish", "bearish", "neutral"]


@dataclass(frozen=True, slots=True)
class StructureConfig:
    internal_size: int = 5
    swing_size: int = 50
    equal_size: int = 3
    #: Equal highs/lows tolerance as a fraction of the volatility measure (0..0.5).
    equal_threshold: float = 0.1
    #: Order-block volatility filter: "atr" (ATR(200), causal warm-up) or "range" (cumulative mean range).
    ob_filter: Literal["atr", "range"] = "atr"
    #: Order-block invalidation source: "highlow" (wicks) or "close".
    ob_mitigation: Literal["highlow", "close"] = "highlow"
    vol_period: int = 200
    #: Minimum FVG size: max(fvg_min_ticks ticks, fvg_min_atr x ATR(14)).
    fvg_min_ticks: int = 2
    fvg_min_atr: float = 0.15
    #: Also require the middle candle's body % to exceed 2x its causal running mean (LuxAlgo-style).
    fvg_auto_threshold: bool = True
    displacement_atr: float = 1.2
    #: Liquidity states.
    approach_atr: float = 0.5
    reclaim_bars: int = 3
    accept_closes: int = 2
    max_items: int = 8


@dataclass(frozen=True, slots=True)
class Pivot:
    index: int
    kind: Literal["high", "low"]
    price: float
    confirmed_index: int
    label: str  # HH / LH / HL / LL / EQH / EQL vs the previous pivot of the same kind and layer ("" first)


@dataclass(frozen=True, slots=True)
class StructureEvent:
    index: int  # bar whose CLOSE broke the level
    direction: Side
    kind: Literal["BOS", "CHoCH"]
    level: float
    pivot_index: int
    layer: Literal["internal", "swing"]


@dataclass(slots=True)
class Zone:
    id: str
    type: str
    direction: Side
    layer: str
    formed_at: datetime
    confirmed_at: datetime
    top: float
    bottom: float
    status: str
    retests: int
    invalidation_price: float
    evidence: list[str]
    flags: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["formed_at"] = self.formed_at.isoformat()
        d["confirmed_at"] = self.confirmed_at.isoformat()
        return d


# ----------------------------------------------------------------------------------- volatility


def causal_atr(bars: Sequence[Bar], period: int) -> list[float]:
    """Wilder ATR; during warm-up the mean true range of bars up to each bar (never later bars)."""
    tr = true_range(bars)
    out: list[float] = []
    total, prev = 0.0, None
    for i, t in enumerate(tr):
        total += t
        if i < period:
            out.append(total / (i + 1))
            prev = out[-1]
        else:
            assert prev is not None
            prev = (prev * (period - 1) + t) / period
            out.append(prev)
    return out


def cumulative_range(bars: Sequence[Bar]) -> list[float]:
    tr = true_range(bars)
    out, total = [], 0.0
    for i, t in enumerate(tr):
        total += t
        out.append(total / (i + 1))
    return out


# ----------------------------------------------------------------------------------------- pivots


def leg_pivots(bars: Sequence[Bar], size: int) -> list[Pivot]:
    """Alternating leg pivots.

    At bar ``i`` the candidate is bar ``i - size``. If its high exceeds every high of the ``size``
    bars after it, the leg turns bearish; if its low is below every low after it, the leg turns
    bullish. A change of leg confirms a pivot at the candidate bar (high on a turn to bearish,
    low on a turn to bullish), known at the close of bar ``i``. The initial leg is bearish, so the
    first possible pivot is a low (as in the reference indicator).
    """
    out: list[Pivot] = []
    leg = 0  # 0 bearish, 1 bullish
    last: dict[str, float] = {}
    for i in range(size, len(bars)):
        cand = bars[i - size]
        window = bars[i - size + 1 : i + 1]
        new_high = cand.high > max(b.high for b in window)
        new_low = cand.low < min(b.low for b in window)
        prev = leg
        if new_high:
            leg = 0
        elif new_low:
            leg = 1
        if leg == prev:
            continue
        kind: Literal["high", "low"] = "high" if leg == 0 else "low"
        price = cand.high if kind == "high" else cand.low
        p = last.get(kind)
        if p is None:
            label = ""
        elif kind == "high":
            label = "HH" if price > p else "LH" if price < p else "EQH"
        else:
            label = "HL" if price > p else "LL" if price < p else "EQL"
        last[kind] = price
        out.append(Pivot(i - size, kind, price, i, label))
    return out


# -------------------------------------------------------------------------------------- structure


@dataclass
class LayerState:
    trend: Trend = "neutral"
    events: list[StructureEvent] = field(default_factory=list)
    high: Pivot | None = None
    low: Pivot | None = None
    high_crossed: bool = False
    low_crossed: bool = False


def structure(
    bars: Sequence[Bar],
    pivots: Sequence[Pivot],
    layer: Literal["internal", "swing"],
    exclude: Sequence[Pivot] = (),
) -> LayerState:
    """BOS / CHoCH from closes through the current (most recently confirmed) pivot of each kind.

    A break in the direction of the layer's trend (or the first break) is a BOS; a break against it
    is a CHoCH and flips the trend. Each pivot can be broken once. ``exclude`` (the swing pivots,
    for the internal layer) suppresses internal breaks of a level that is also the current swing
    level, so one move is not reported twice.
    """
    by_conf: dict[int, list[Pivot]] = {}
    for p in pivots:
        by_conf.setdefault(p.confirmed_index, []).append(p)
    ex_by_conf: dict[int, list[Pivot]] = {}
    for p in exclude:
        ex_by_conf.setdefault(p.confirmed_index, []).append(p)
    st = LayerState()
    ex_high: float | None = None
    ex_low: float | None = None
    for i, b in enumerate(bars):
        for p in by_conf.get(i, []):
            if p.kind == "high":
                st.high, st.high_crossed = p, False
            else:
                st.low, st.low_crossed = p, False
        for p in ex_by_conf.get(i, []):
            if p.kind == "high":
                ex_high = p.price
            else:
                ex_low = p.price
        if st.high is not None and not st.high_crossed and b.close > st.high.price and st.high.price != ex_high:
            kind: Literal["BOS", "CHoCH"] = "CHoCH" if st.trend == "bearish" else "BOS"
            st.events.append(StructureEvent(i, "bullish", kind, st.high.price, st.high.index, layer))
            st.high_crossed, st.trend = True, "bullish"
        if st.low is not None and not st.low_crossed and b.close < st.low.price and st.low.price != ex_low:
            kind = "CHoCH" if st.trend == "bullish" else "BOS"
            st.events.append(StructureEvent(i, "bearish", kind, st.low.price, st.low.index, layer))
            st.low_crossed, st.trend = True, "bearish"
    return st


def trailing_extremes(bars: Sequence[Bar], swing_pivots: Sequence[Pivot], swing_trend: Trend) -> dict[str, Any] | None:
    """Highest high / lowest low since the latest swing pivot of each kind (the dealing range).

    The high is "strong" when the swing trend is bearish (it protects the downtrend), otherwise
    "weak" (a target in an uptrend); the low mirrors this.
    """
    highs = [p for p in swing_pivots if p.kind == "high"]
    lows = [p for p in swing_pivots if p.kind == "low"]
    if not highs or not lows:
        return None
    hp, lp = highs[-1], lows[-1]
    ti = max(range(hp.index, len(bars)), key=lambda j: (bars[j].high, -j))
    bi = min(range(lp.index, len(bars)), key=lambda j: (bars[j].low, j))
    top, bottom = bars[ti].high, bars[bi].low
    return {
        "top": top,
        "top_time": bars[ti].ts.isoformat(),
        "top_label": "Strong high" if swing_trend == "bearish" else "Weak high",
        "bottom": bottom,
        "bottom_time": bars[bi].ts.isoformat(),
        "bottom_label": "Strong low" if swing_trend == "bullish" else "Weak low",
        "basis": "Highest high since the last confirmed swing high and lowest low since the last confirmed swing low.",
    }


def premium_discount(extremes: dict[str, Any] | None, price: float) -> dict[str, Any] | None:
    """Location of price in the swing dealing range. Equilibrium band 47.5-52.5% of the range."""
    if not extremes or extremes["top"] <= extremes["bottom"]:
        return None
    top, bottom = extremes["top"], extremes["bottom"]
    pos = (price - bottom) / (top - bottom)
    zone = (
        "above range"
        if pos > 1
        else "below range"
        if pos < 0
        else "premium"
        if pos > 0.525
        else "discount"
        if pos < 0.475
        else "equilibrium"
    )
    return {
        "range_high": top,
        "range_low": bottom,
        "equilibrium": (top + bottom) / 2,
        "position": round(pos, 3),
        "zone": zone,
        "basis": "Swing dealing range: " + extremes["basis"],
    }


# ---------------------------------------------------------------------------------- order blocks


def _zone_id(*parts: object) -> str:
    import hashlib

    return hashlib.sha1("|".join(map(str, parts)).encode(), usedforsecurity=False).hexdigest()[:12]


def order_blocks(bars: Sequence[Bar], events: Sequence[StructureEvent], cfg: StructureConfig, layer: str) -> list[Zone]:
    """One candidate block per structure break.

    For a bullish break of pivot high P (bar ``p``) at bar ``i``, the block is the candle with the
    lowest low in ``[p, i)``; bearish breaks mirror this with the highest high. High-volatility
    candles (range >= 2x the volatility measure) are filtered by swapping their high/low, so a
    single news spike cannot define a block. A block is then: fresh (not revisited), mitigated
    (price traded back into it, still valid) or invalidated (the mitigation source - wick or close -
    went through its far side).
    """
    vol = causal_atr(bars, cfg.vol_period) if cfg.ob_filter == "atr" else cumulative_range(bars)
    ph, pl = [], []
    for i, b in enumerate(bars):
        hv = (b.high - b.low) >= 2 * vol[i]
        ph.append(b.low if hv else b.high)
        pl.append(b.high if hv else b.low)
    out: list[Zone] = []
    for ev in events:
        lo, hi = ev.pivot_index, ev.index
        if hi <= lo:
            continue
        bull = ev.direction == "bullish"
        rng = range(lo, hi)
        k = min(rng, key=lambda j: (pl[j], j)) if bull else max(rng, key=lambda j: (ph[j], -j))
        top, bottom = max(ph[k], pl[k]), min(ph[k], pl[k])
        status, retests, touched = "fresh", 0, False
        for j in range(ev.index + 1, len(bars)):
            b = bars[j]
            src_bad = (
                (b.close if cfg.ob_mitigation == "close" else b.low) < bottom
                if bull
                else (b.close if cfg.ob_mitigation == "close" else b.high) > top
            )
            if src_bad:
                status = "invalidated"
                break
            inside = b.low <= top if bull else b.high >= bottom
            if inside and not touched:
                retests += 1
            touched = inside
        if status != "invalidated" and retests:
            status = "mitigated"
        out.append(
            Zone(
                _zone_id(layer, "OB", bars[k].ts, ev.index),
                f"{'bullish' if bull else 'bearish'}_order_block",
                ev.direction,
                layer,
                bars[k].ts,
                bars[ev.index].ts,
                top,
                bottom,
                status,
                retests,
                bottom if bull else top,
                [
                    f"{layer.capitalize()} {ev.kind} at {bars[ev.index].ts:%m-%d %H:%M} UTC through {ev.level:g}.",
                    f"Block = candle with the {'lowest low' if bull else 'highest high'} between the broken pivot "
                    "and the break (high-volatility candles filtered).",
                    f"Invalidated by a {'wick' if cfg.ob_mitigation == 'highlow' else 'close'} "
                    f"{'below' if bull else 'above'} {bottom if bull else top:g}. Retests: {retests}.",
                ],
                {"structure_confirmed": True, "break": ev.kind},
            )
        )
    return out


# ------------------------------------------------------------------------------- fair value gaps


def fair_value_gaps(bars: Sequence[Bar], tick: float, cfg: StructureConfig, layer: str = "") -> list[Zone]:
    """Three-candle gaps confirmed at the close of the third candle.

    Bullish: low[i] > high[i-2] and the middle candle closed above high[i-2] (mirror for bearish).
    Filters: size >= max(min ticks, min ATR fraction) and, optionally, the middle candle's body % is
    more than twice its running mean (computed only from bars up to the gap).
    Status: unmitigated, partially mitigated (price entered the gap), fully mitigated (price traded
    to the far side), invalidated (a close beyond the far side).
    """
    atr14 = causal_atr(bars, 14)
    out: list[Zone] = []
    body_sum = 0.0
    for i in range(len(bars)):
        if i >= 1:
            m = bars[i - 1]
            body_sum += abs((m.close - m.open) / m.open) if m.open else 0.0
        if i < 2:
            continue
        a, m, c = bars[i - 2], bars[i - 1], bars[i]
        pct = (m.close - m.open) / m.open if m.open else 0.0
        thr = 2 * body_sum / i if cfg.fvg_auto_threshold else 0.0
        for bull in (True, False):
            if bull and c.low > a.high and m.close > a.high and pct > thr:
                top, bottom = c.low, a.high
            elif not bull and c.high < a.low and m.close < a.low and -pct > thr:
                top, bottom = a.low, c.high
            else:
                continue
            size = top - bottom
            if size < max(cfg.fvg_min_ticks * tick, cfg.fvg_min_atr * atr14[i]):
                continue
            status, retests, inside, deepest = "unmitigated", 0, False, None
            for j in range(i + 1, len(bars)):
                b = bars[j]
                if (bull and b.close < bottom) or (not bull and b.close > top):
                    status = "invalidated"
                    break
                touch = b.low <= top if bull else b.high >= bottom
                if touch and not inside:
                    retests += 1
                inside = touch
                pen = b.low if bull else b.high
                deepest = pen if deepest is None else (min(deepest, pen) if bull else max(deepest, pen))
            fill = 0.0
            if deepest is not None:
                fill = (top - deepest) / size if bull else (deepest - bottom) / size
                fill = max(0.0, min(1.0, fill))
            if status != "invalidated":
                status = "fully mitigated" if fill >= 1.0 else "partially mitigated" if fill > 0 else "unmitigated"
            disp = abs(m.close - m.open) >= cfg.displacement_atr * atr14[i - 1]
            out.append(
                Zone(
                    _zone_id(layer, "FVG", c.ts, bull),
                    f"{'bullish' if bull else 'bearish'}_fvg",
                    "bullish" if bull else "bearish",
                    layer,
                    m.ts,
                    c.ts,
                    top,
                    bottom,
                    status,
                    retests,
                    bottom if bull else top,
                    [
                        f"Gap {bottom:g}-{top:g} ({size:g} pts), candles {a.ts:%m-%d %H:%M}-{c.ts:%H:%M} UTC.",
                        f"Filled {fill:.0%}; retests {retests}.",
                        f"Middle candle displacement (body >= {cfg.displacement_atr} x ATR)."
                        if disp
                        else "No displacement on the middle candle.",
                    ],
                    {"fill": round(fill, 3), "displacement": disp, "size": round(size, 6), "index": i},
                )
            )
    return out


# -------------------------------------------------------------------------------- equal highs/lows


def equal_levels(bars: Sequence[Bar], tick: float, cfg: StructureConfig) -> list[dict[str, Any]]:
    """Consecutive short-leg pivots of the same kind within ``equal_threshold`` x ATR(200)."""
    vol = causal_atr(bars, cfg.vol_period)
    piv = leg_pivots(bars, cfg.equal_size)
    out: list[dict[str, Any]] = []
    prev: dict[str, Pivot] = {}
    for p in piv:
        q = prev.get(p.kind)
        if q is not None and abs(p.price - q.price) < max(cfg.equal_threshold * vol[p.confirmed_index], tick / 2):
            lvl = max(p.price, q.price) if p.kind == "high" else min(p.price, q.price)
            out.append(
                {
                    "type": "EQH" if p.kind == "high" else "EQL",
                    "side": "buy" if p.kind == "high" else "sell",
                    "price": lvl,
                    "first_at": bars[q.index].ts.isoformat(),
                    "second_at": bars[p.index].ts.isoformat(),
                    "confirmed_index": p.confirmed_index,
                    "confirmed_at": bars[p.confirmed_index].ts.isoformat(),
                }
            )
        prev[p.kind] = p
    return out


# ---------------------------------------------------------------------------------- liquidity state


def _extreme(bars: Sequence[Bar], j: int, k: int, buy: bool) -> float:
    """Furthest price traded beyond the level between bars j..k (the sweep extreme)."""
    seg = bars[j : k + 1]
    return max(b.high for b in seg) if buy else min(b.low for b in seg)


def level_state(
    bars: Sequence[Bar],
    level: float,
    side: Literal["buy", "sell"],
    since: int,
    atr_now: float,
    tick: float,
    cfg: StructureConfig = StructureConfig(),
) -> dict[str, Any]:
    """What price has done with a reference level after bar ``since`` (bars after it only).

    * untouched / approaching (within ``approach_atr`` x ATR, not traded through),
    * breached (traded beyond by >= 1 tick, no verdict yet),
    * swept and reclaimed (a close back on the original side within ``reclaim_bars`` bars),
    * accepted beyond (``accept_closes`` consecutive closes beyond the level).
    "Buy-side" levels sit above price (highs); "sell-side" below (lows). Resting orders are inferred,
    never observed.
    """
    buy = side == "buy"
    n = len(bars)
    for j in range(since + 1, n):
        b = bars[j]
        if (buy and b.high >= level + tick) or (not buy and b.low <= level - tick):
            beyond = 0
            for k in range(j, min(n, j + cfg.reclaim_bars)):
                c = bars[k].close
                if (buy and c > level) or (not buy and c < level):
                    beyond += 1
                    if beyond >= cfg.accept_closes:
                        return {
                            "state": "accepted beyond",
                            "at": b.ts.isoformat(),
                            "verdict_at": bars[k].ts.isoformat(),
                            "extreme": _extreme(bars, j, k, buy),
                        }
                else:
                    return {
                        "state": "swept and reclaimed",
                        "at": b.ts.isoformat(),
                        "verdict_at": bars[k].ts.isoformat(),
                        "extreme": _extreme(bars, j, k, buy),
                    }
            return {
                "state": "breached",
                "at": b.ts.isoformat(),
                "verdict_at": None,
                "extreme": _extreme(bars, j, n - 1, buy),
            }
    dist = abs(bars[-1].close - level) if bars else 0.0
    return {
        "state": "approaching" if dist <= cfg.approach_atr * atr_now else "untouched",
        "at": None,
        "verdict_at": None,
    }


# ---------------------------------------------------------------------------------------- report


def analyze_structure(bars: Sequence[Bar], tick: float, cfg: StructureConfig = StructureConfig()) -> dict[str, Any]:
    """Internal + swing structure, strong/weak extremes, premium/discount, order blocks, FVGs, EQH/EQL."""
    bars = list(bars)
    n = len(bars)
    if n < cfg.internal_size * 3:
        return {"available": False, "bars": n, "reason": f"Only {n} closed bars (need {cfg.internal_size * 3})."}
    swing_piv = leg_pivots(bars, cfg.swing_size) if n > cfg.swing_size else []
    int_piv = leg_pivots(bars, cfg.internal_size)
    swing = structure(bars, swing_piv, "swing")
    internal = structure(bars, int_piv, "internal", exclude=swing_piv)
    ext = trailing_extremes(bars, swing_piv, swing.trend)
    price = bars[-1].close
    obs = order_blocks(bars, internal.events, cfg, "internal") + order_blocks(bars, swing.events, cfg, "swing")
    fvgs = fair_value_gaps(bars, tick, cfg)
    eq = equal_levels(bars, tick, cfg)
    atr_now = causal_atr(bars, 14)[-1]

    def ev_out(e: StructureEvent) -> dict[str, Any]:
        return {
            "time": bars[e.index].ts.isoformat(),
            "direction": e.direction,
            "kind": e.kind,
            "level": e.level,
            "layer": e.layer,
            "pivot_time": bars[e.pivot_index].ts.isoformat(),
            "bars_ago": n - 1 - e.index,
        }

    def piv_out(p: Pivot) -> dict[str, Any]:
        return {
            "time": bars[p.index].ts.isoformat(),
            "confirmed_at": bars[p.confirmed_index].ts.isoformat(),
            "kind": p.kind,
            "price": p.price,
            "label": p.label,
        }

    for e in eq:
        e["state"] = level_state(bars, e["price"], e["side"], e["confirmed_index"], atr_now, tick, cfg)

    def keep(zs: list[Zone]) -> list[dict[str, Any]]:
        zs = sorted(zs, key=lambda z: z.confirmed_at, reverse=True)
        return [z.as_dict() for z in zs[: cfg.max_items * 2]]

    return {
        "available": True,
        "bars": n,
        "as_of": bars[-1].ts.isoformat(),
        "price": price,
        "atr14": atr_now,
        "internal": {
            "trend": internal.trend,
            "events": [ev_out(e) for e in internal.events[-cfg.max_items :]],
            "pivots": [piv_out(p) for p in int_piv[-cfg.max_items :]],
        },
        "swing": {
            "trend": swing.trend,
            "events": [ev_out(e) for e in swing.events[-cfg.max_items :]],
            "pivots": [piv_out(p) for p in swing_piv[-cfg.max_items :]],
            "confirmed": len(swing_piv) >= 2,
        },
        "extremes": ext,
        "premium_discount": premium_discount(ext, price),
        "order_blocks": keep(obs),
        "fvgs": keep(fvgs),
        "equal_levels": eq[-cfg.max_items :],
        "config": asdict(cfg),
    }
