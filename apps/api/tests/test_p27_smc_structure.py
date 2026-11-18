"""SMC structure engine: deterministic scenarios with known answers, and no-look-ahead proofs."""

from __future__ import annotations

import itertools
import random
from datetime import UTC, datetime, timedelta

from app.marketdata.instruments import INSTRUMENTS
from app.marketdata.types import Timeframe
from app.research.bars import Bar, aggregate, closed_only
from app.research.smc_structure import (
    StructureConfig,
    analyze_structure,
    equal_levels,
    fair_value_gaps,
    leg_pivots,
    level_state,
    order_blocks,
    premium_discount,
    structure,
    trailing_extremes,
)
from app.services.checklist import ChecklistConfig, reward_risk

T0 = datetime(2026, 11, 17, 14, 30, tzinfo=UTC)  # 09:30 ET


def path(points: list[float], step: float = 1.0, wick: float = 0.25) -> list[Bar]:
    """Bars walking linearly between the given closes (one bar per ``step`` points)."""
    closes = [points[0]]
    for a, b in itertools.pairwise(points):
        n = max(1, round(abs(b - a) / step))
        closes += [a + (b - a) * k / n for k in range(1, n + 1)]
    bars, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        bars.append(Bar(T0 + timedelta(minutes=i), o, max(o, c) + wick, min(o, c) - wick, c, 100))
        prev = c
    return bars


def bar(i: int, o: float, h: float, low: float, c: float, v: int = 100) -> Bar:
    return Bar(T0 + timedelta(minutes=i), o, h, low, c, v)


# ---------------------------------------------------------------------------------- pivots / BOS


def test_pivots_confirm_only_after_size_bars_and_label_hh_hl() -> None:
    bars = path([100, 105, 102, 108, 104, 110])
    piv = leg_pivots(bars, 2)
    # The initial leg is bearish, so the first pivot is the starting low (reference behaviour).
    assert [(p.kind, p.price, p.label) for p in piv] == [
        ("low", 99.75, ""),
        ("high", 105.25, ""),
        ("low", 101.75, "HL"),
        ("high", 108.25, "HH"),
        ("low", 103.75, "HL"),
    ]
    for p in piv:
        assert p.confirmed_index == p.index + 2
        # Not knowable one bar earlier: the pivot is absent from the truncated series.
        assert all(q.index != p.index for q in leg_pivots(bars[: p.confirmed_index], 2))


def test_bullish_bos_then_bearish_choch_and_bearish_bos() -> None:
    bars = path([100, 105, 102, 108, 104, 110, 106, 99, 102, 96])
    st = structure(bars, leg_pivots(bars, 2), "internal")
    assert [(e.direction, e.kind, e.level) for e in st.events] == [
        ("bullish", "BOS", 105.25),  # first break: continuation label
        ("bullish", "BOS", 108.25),
        ("bearish", "CHoCH", 103.75),  # the last higher low broken -> change of character
        ("bearish", "BOS", 98.75),  # the new lower low broken -> continuation
    ]
    choch = st.events[2]
    assert bars[choch.index].close < 103.75 and bars[choch.index - 1].close >= 103.75
    assert st.trend == "bearish"


def test_bearish_bos_and_bullish_choch_mirror() -> None:
    bars = path([200, 195, 198, 192, 196, 190, 194, 201, 199, 204])
    st = structure(bars, leg_pivots(bars, 2), "swing")
    assert [(e.direction, e.kind, e.level) for e in st.events] == [
        ("bearish", "BOS", 194.75),
        ("bearish", "BOS", 191.75),
        ("bullish", "CHoCH", 196.25),
        ("bullish", "BOS", 201.25),
    ]
    assert st.events[2].layer == "swing" and st.trend == "bullish"


def test_internal_layer_skips_breaks_of_the_current_swing_level() -> None:
    bars = path([100, 105, 102, 108, 104, 99])
    piv = leg_pivots(bars, 2)
    alone = structure(bars, piv, "internal")
    filtered = structure(bars, piv, "internal", exclude=piv)  # same pivots as swing -> no duplicates
    assert alone.events and not filtered.events


def test_each_pivot_breaks_once_and_pullback_is_not_a_reversal() -> None:
    bars = path([100, 105, 102, 108, 106, 109])  # pullback to 106 stays above the 101.75 low
    st = structure(bars, leg_pivots(bars, 2), "internal")
    assert all(e.direction == "bullish" for e in st.events)
    assert len({e.level for e in st.events}) == len(st.events)


def test_strong_weak_extremes_and_premium_discount() -> None:
    bars = path([100, 110, 104, 112, 106])
    piv = leg_pivots(bars, 2)
    st = structure(bars, piv, "swing")
    ext = trailing_extremes(bars, piv, st.trend)
    assert ext is not None and st.trend == "bullish"
    assert ext["top_label"] == "Weak high" and ext["bottom_label"] == "Strong low"
    pd = premium_discount({"top": 110.0, "bottom": 100.0, "basis": "x"}, 108.0)
    assert pd["zone"] == "premium" and pd["equilibrium"] == 105.0
    assert premium_discount({"top": 110.0, "bottom": 100.0, "basis": "x"}, 105.1)["zone"] == "equilibrium"
    assert premium_discount({"top": 110.0, "bottom": 100.0, "basis": "x"}, 101.0)["zone"] == "discount"
    assert premium_discount({"top": 110.0, "bottom": 100.0, "basis": "x"}, 99.0)["zone"] == "below range"


# ------------------------------------------------------------------------------------------- FVG


def _fvg_bars(after: list[tuple[float, float, float, float]]) -> list[Bar]:
    base = [bar(i, 100, 100.5, 99.5, 100) for i in range(30)]
    gap = [
        bar(30, 100, 101.0, 99.75, 100.75),
        bar(31, 100.75, 104.0, 100.75, 103.75),
        bar(32, 103.75, 104.5, 102.5, 104),
    ]
    rest = [bar(33 + k, *x) for k, x in enumerate(after)]
    return base + gap + rest


def test_bullish_fvg_formation_and_mitigation_states() -> None:
    tick = 0.25
    cfg = StructureConfig()
    g = fair_value_gaps(_fvg_bars([]), tick, cfg)
    assert len(g) == 1 and g[0].type == "bullish_fvg" and (g[0].bottom, g[0].top) == (101.0, 102.5)
    assert g[0].status == "unmitigated" and g[0].confirmed_at == T0 + timedelta(minutes=32)
    part = fair_value_gaps(_fvg_bars([(104, 104.2, 101.8, 103)]), tick, cfg)[0]
    assert part.status == "partially mitigated" and part.retests == 1 and 0 < part.flags["fill"] < 1
    full = fair_value_gaps(_fvg_bars([(104, 104.2, 100.9, 101.5)]), tick, cfg)[0]
    assert full.status == "fully mitigated"
    inval = fair_value_gaps(_fvg_bars([(104, 104.2, 100.0, 100.5)]), tick, cfg)[0]
    assert inval.status == "invalidated"


def test_tiny_gaps_are_ignored() -> None:
    bars = [bar(i, 100, 100.5, 99.5, 100) for i in range(30)]
    bars += [bar(30, 100, 100.5, 99.75, 100.4), bar(31, 100.4, 100.9, 100.4, 100.8), bar(32, 100.8, 101, 100.75, 101)]
    assert fair_value_gaps(bars, 0.25, StructureConfig()) == []  # 0.25-pt gap < 2 ticks / ATR minimum


def test_bearish_fvg_mirror() -> None:
    bars = [bar(i, 100, 100.5, 99.5, 100) for i in range(30)]
    bars += [bar(30, 100, 100.25, 99, 99.25), bar(31, 99.25, 99.25, 96, 96.25), bar(32, 96.25, 97.5, 95.5, 96)]
    g = fair_value_gaps(bars, 0.25, StructureConfig())
    assert len(g) == 1 and g[0].type == "bearish_fvg" and (g[0].bottom, g[0].top) == (97.5, 99.0)


# ---------------------------------------------------------------------------------- order blocks


def test_order_block_from_break_then_invalidated_by_wick_or_close() -> None:
    bars = path([100, 105, 102, 108])
    piv = leg_pivots(bars, 2)
    st = structure(bars, piv, "internal")
    obs = order_blocks(bars, st.events[:1], StructureConfig(vol_period=14), "internal")
    assert len(obs) == 1 and obs[0].type == "bullish_order_block" and obs[0].status == "fresh"
    ob = obs[0]
    assert ob.bottom == min(b.low for b in bars[st.events[0].pivot_index : st.events[0].index])
    after = [*bars, bar(len(bars), 108, 108, ob.top - 0.25, 107)]
    assert order_blocks(after, st.events[:1], StructureConfig(vol_period=14), "internal")[0].status == "mitigated"
    wick = [*bars, bar(len(bars), 108, 108, ob.bottom - 0.5, ob.bottom + 0.5)]
    assert order_blocks(wick, st.events[:1], StructureConfig(vol_period=14), "internal")[0].status == "invalidated"
    by_close = order_blocks(wick, st.events[:1], StructureConfig(vol_period=14, ob_mitigation="close"), "internal")
    assert by_close[0].status == "mitigated"  # the wick alone does not invalidate with close mitigation


def test_high_volatility_candle_cannot_define_the_block() -> None:
    bars = [bar(i, 100 + 0.1 * i, 100.3 + 0.1 * i, 99.8 + 0.1 * i, 100.1 + 0.1 * i) for i in range(40)]
    bars.append(bar(40, 104, 104.2, 90, 103.5))  # spike low: range >> 2x volatility
    bars.append(bar(41, 103.5, 104, 103, 103.8))
    from app.research.smc_structure import StructureEvent

    ev = StructureEvent(42 - 1, "bullish", "BOS", 103.9, 38, "internal")
    ob = order_blocks(bars, [ev], StructureConfig(vol_period=14), "internal")[0]
    assert ob.bottom > 90  # the 90 wick was filtered out


# ---------------------------------------------------------------------------------- liquidity


def test_equal_highs_and_level_states_sweep_accept_breach() -> None:
    bars = path([100, 105, 102, 105, 101], step=0.5)
    eq = equal_levels(bars, 0.25, StructureConfig(equal_size=2))
    assert eq and eq[-1]["type"] == "EQH" and eq[-1]["price"] == 105.25
    tick = 0.25
    base = path([100, 104, 102], step=0.5)
    n = len(base)
    swept = [*base, bar(n, 102, 105.5, 102, 104.5)]  # wick above 105, close back below
    assert level_state(swept, 105.0, "buy", 0, 1.0, tick)["state"] == "swept and reclaimed"
    accepted = [*base, bar(n, 104, 106, 104, 105.75), bar(n + 1, 105.75, 106.5, 105.5, 106)]
    assert level_state(accepted, 105.0, "buy", 0, 1.0, tick)["state"] == "accepted beyond"
    breached = [*base, bar(n, 104, 106, 104, 105.75)]
    assert level_state(breached, 105.0, "buy", 0, 1.0, tick)["state"] == "breached"
    assert level_state(base, 102.5, "buy", n - 1, 1.0, tick)["state"] == "approaching"
    assert level_state(base, 110.0, "buy", n - 1, 1.0, tick)["state"] == "untouched"
    sell = [*base, bar(n, 102, 102, 98.5, 100.5)]
    assert level_state(sell, 99.0, "sell", 0, 1.0, tick)["state"] == "swept and reclaimed"


# ----------------------------------------------------------------------------------- causality


def _random_bars(n: int, seed: int = 7) -> list[Bar]:
    rnd = random.Random(seed)  # noqa: S311 (test data)
    px, out = 5000.0, []
    for i in range(n):
        o = px
        c = round((o + rnd.gauss(0, 2)) * 4) / 4
        h = max(o, c) + round(abs(rnd.gauss(0, 1)) * 4) / 4
        low = min(o, c) - round(abs(rnd.gauss(0, 1)) * 4) / 4
        out.append(Bar(T0 + timedelta(minutes=i), o, h, low, c, 100 + i % 50))
        px = c
    return out


def test_no_lookahead_events_and_gaps_never_change_when_future_bars_arrive() -> None:
    bars = _random_bars(400)
    cfg = StructureConfig(swing_size=20, max_items=10_000)
    full = analyze_structure(bars, 0.25, cfg)
    for k in (120, 200, 333):
        part = analyze_structure(bars[:k], 0.25, cfg)
        cut = bars[k - 1].ts.isoformat()
        for layer in ("internal", "swing"):
            a = [e for e in full[layer]["events"] if e["time"] <= cut]
            b = part[layer]["events"]
            assert [(e["time"], e["kind"], e["level"]) for e in a][-len(b) :] == [
                (e["time"], e["kind"], e["level"]) for e in b
            ]
        fa = {(z["confirmed_at"], z["top"], z["bottom"]) for z in full["fvgs"] if z["confirmed_at"] <= cut}
        fb = {(z["confirmed_at"], z["top"], z["bottom"]) for z in part["fvgs"]}
        assert fb <= fa and all(z["confirmed_at"] <= cut for z in part["fvgs"])
        for p in part["internal"]["pivots"]:
            assert p["confirmed_at"] <= cut


def test_higher_timeframe_aggregation_and_closed_only() -> None:
    m1 = [bar(i, 100 + i, 100.5 + i, 99.5 + i, 100.25 + i, 10) for i in range(12)]
    m5 = aggregate(m1, Timeframe.M5)
    assert m5[0].open == 100 and m5[0].close == 104.25 and m5[0].high == 104.5 and m5[0].volume == 50
    now = m1[-1].ts + timedelta(minutes=1)  # 12 minutes in: the third 5m bar is still forming
    closed = closed_only(m5, Timeframe.M5, now)
    assert len(closed) == 2 and closed[-1].ts == T0 + timedelta(minutes=5)


def test_mnq_es_nq_contract_math() -> None:
    es, nq, mnq = INSTRUMENTS["ES"], INSTRUMENTS["NQ"], INSTRUMENTS["MNQ"]
    assert (float(es.point_value), float(nq.point_value), float(mnq.point_value)) == (50.0, 20.0, 2.0)
    assert float(mnq.tick_size) == 0.25 and float(mnq.tick_value) == 0.5 and mnq.is_micro
    cfg = ChecklistConfig(commission_per_side={"standard": 2.5, "micro": 0.6}, slippage_ticks_per_side=1)
    r = reward_risk("MNQ", "long", 20000.0, 19990.0, 20020.0, cfg)
    assert r["gross_reward"] == 40.0 and r["gross_risk"] == 20.0
    assert r["net_reward"] == round(40 - 1.2 - 0.5, 2) and r["net_risk"] == round(20 + 1.2 + 1.0, 2)
