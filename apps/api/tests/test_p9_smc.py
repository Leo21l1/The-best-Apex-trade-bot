"""Smart-money-concepts detectors against hand-labelled fixtures, plus a no-look-ahead property."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from app.research.bars import Bar
from app.research.smc import SmcConfig, analyze, swings

T0 = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)  # Wed 10:00 ET
TICK = 0.25


def mk(rows: list[tuple[float, float, float, float]]) -> list[Bar]:
    return [Bar(T0 + timedelta(minutes=5 * i), o, h, low, c, 1000) for i, (o, h, low, c) in enumerate(rows)]


def of(a, typ):
    return [p for p in a.patterns if p.type == typ]


CFG2 = SmcConfig(pivot_left=2, pivot_right=2, sweep_window=2)
SWEEP_BASE = [
    (100, 101, 99, 100),
    (100, 102, 99.5, 101.5),
    (101.5, 105, 101, 104),  # swing high 105, confirmed two bars later
    (104, 104.5, 102, 102.5),
    (102.5, 103, 101, 101.5),
    (101.5, 102, 100, 100.5),
]


def test_swing_is_provisional_until_right_bars_close() -> None:
    bars = mk(SWEEP_BASE[:4])
    sw = [s for s in swings(bars, CFG2) if s.kind == "high"]
    assert sw and sw[0].price == 105 and sw[0].confirmed_index is None
    sw = [s for s in swings(mk(SWEEP_BASE[:5]), CFG2) if s.kind == "high"]
    assert sw[0].confirmed_index == 4


def test_buy_side_sweep_confirmed_rejection_and_no_lookahead() -> None:
    rows = [*SWEEP_BASE, (100.5, 106, 100.25, 104.5), (104.5, 104.75, 102, 102.25), (102.25, 102.5, 100, 100.25)]
    bars = mk(rows)
    sw = of(analyze("ES", "5m", bars, TICK, CFG2), "buy_side_sweep")
    assert len(sw) == 1
    p = sw[0]
    assert p.status == "confirmed_rejection" and p.direction == "bearish"
    assert p.extra["level"] == 105 and p.invalidation_price == 106
    assert p.formed_at == bars[6].ts and p.confirmed_at == bars[6].ts
    assert any("swing high at 105" in e for e in p.evidence)
    # Before the sweep bar closed, nothing is known.
    assert not of(analyze("ES", "5m", bars[:6], TICK, CFG2), "buy_side_sweep")


def test_sweep_rejection_invalidated_by_close_beyond_extreme() -> None:
    rows = [*SWEEP_BASE, (100.5, 106, 100.25, 104.5), (104.5, 107.5, 104, 107)]
    sw = of(analyze("ES", "5m", mk(rows), TICK, CFG2), "buy_side_sweep")
    assert sw[0].status == "invalidated"


def test_sweep_that_holds_is_breakout_continuation_and_candidate_before_window_ends() -> None:
    rows = [*SWEEP_BASE, (100.5, 106, 100.25, 105.5)]
    assert of(analyze("ES", "5m", mk(rows), TICK, CFG2), "buy_side_sweep")[0].status == "candidate"
    rows += [(105.5, 106.5, 105.25, 106), (106, 107, 105.5, 106.5)]
    p = of(analyze("ES", "5m", mk(rows), TICK, CFG2), "buy_side_sweep")[0]
    assert p.status == "breakout_continuation" and p.confirmed_at == mk(rows)[8].ts


FVG_ROWS = [
    (100, 100.5, 99.5, 100),
    (100, 100.5, 99.5, 100),
    (100, 100.5, 99.5, 100),
    (100, 101, 99, 100.5),  # a: high 101
    (100.5, 104, 100.5, 103.75),  # displacement
    (103.75, 105, 102, 104.5),  # c: low 102 -> gap 101..102
    (104.5, 104.75, 101.5, 103),  # half fill
]


def test_bullish_fvg_partial_fill_then_invalidation_and_inverse_gap() -> None:
    bars = mk(FVG_ROWS)
    g = of(analyze("ES", "5m", bars, TICK, CFG2), "bullish_fvg")[0]
    assert (g.bottom, g.top) == (101, 102) and g.formed_at == bars[4].ts and g.confirmed_at == bars[5].ts
    assert g.status == "partially_filled" and g.extra["fill"] == 0.5
    rows = [*FVG_ROWS, (103, 103.25, 100.25, 100.5)]
    a = analyze("ES", "5m", mk(rows), TICK, CFG2)
    assert of(a, "bullish_fvg")[0].status == "invalidated"
    inv = of(a, "inverse_fvg")[0]
    assert inv.direction == "bearish" and inv.confirmed_at == mk(rows)[7].ts


def test_tiny_gaps_are_filtered() -> None:
    # Gap 101..101.25 (1 tick): kept with a 1-tick minimum, dropped with the default 2-tick minimum.
    rows = [
        *FVG_ROWS[:3],
        (100, 101, 99, 100.5),
        (100.5, 102, 100.5, 101.75),
        (101.75, 102.5, 101.25, 102),
        (102, 102.5, 101.75, 102.25),
    ]
    loose = SmcConfig(pivot_left=2, pivot_right=2, fvg_min_ticks=1, fvg_min_atr=0)
    assert of(analyze("ES", "5m", mk(rows), TICK, loose), "bullish_fvg")
    assert not of(analyze("ES", "5m", mk(rows), TICK, CFG2), "bullish_fvg")


CFG1 = SmcConfig(pivot_left=1, pivot_right=1)
STRUCT_ROWS = [
    (100, 101, 99, 100),
    (100, 103, 99.5, 102.5),  # swing high 103
    (102.5, 102.75, 100, 100.5),
    (100.5, 101, 98, 98.5),  # swing low 98
    (98.5, 99.5, 98.25, 99.25),
    (99.25, 99.5, 97, 97.25),  # close below 98 -> bearish BOS
    (97.25, 97.5, 96.5, 97),
    (97, 98, 96.75, 97.75),  # swing high 98
    (97.75, 97.9, 97.25, 97.5),  # last down-close candle -> order block 97.25..97.9
    (97.5, 101, 97.5, 100.75),  # displacement close above 98 -> CHoCH / MSS
]


def test_bos_then_choch_with_displacement_is_mss_and_order_block() -> None:
    bars = mk(STRUCT_ROWS)
    a = analyze("ES", "5m", bars, TICK, CFG1)
    bos = of(a, "BOS")
    assert bos and bos[0].direction == "bearish" and bos[0].top == 98 and bos[0].confirmed_at == bars[5].ts
    mss = of(a, "MSS")
    assert mss and mss[0].direction == "bullish" and mss[0].top == 98 and mss[0].confirmed_at == bars[9].ts
    assert a.trend == "bullish"
    ob = of(a, "bullish_order_block")[0]
    assert (ob.bottom, ob.top) == (97.25, 97.9) and ob.formed_at == bars[8].ts and ob.status == "active"
    assert any("not confirmed institutional activity" in e for e in ob.evidence)


def test_order_block_mitigation_and_invalidation() -> None:
    rows = [*STRUCT_ROWS, (100.75, 101, 99, 100), (100, 100.25, 97.8, 98.5)]
    ob = of(analyze("ES", "5m", mk(rows), TICK, CFG1), "bullish_order_block")[0]
    assert ob.status == "mitigated" and ob.extra["retests"] == 1
    rows.append((98.5, 98.75, 96.75, 97))
    ob = of(analyze("ES", "5m", mk(rows), TICK, CFG1), "bullish_order_block")[0]
    assert ob.status == "invalidated"


def test_equal_highs_detected_and_marked_swept() -> None:
    rows = [
        (100, 101, 99, 100),
        (100, 102, 99.5, 101.5),
        (101.5, 105, 101, 104),
        (104, 104.5, 102, 102.5),
        (102.5, 103, 101, 101.5),
        (101.5, 104, 101, 103.5),
        (103.5, 105.25, 103, 104),
        (104, 104.5, 102, 102.5),
        (102.5, 103, 101, 101.5),
    ]
    a = analyze("ES", "5m", mk(rows), TICK, CFG2)
    eq = of(a, "equal_highs")
    assert eq and eq[0].top == 105.25 and eq[0].status == "active"
    a = analyze("ES", "5m", mk([*rows, (101.5, 106, 101, 105.5)]), TICK, CFG2)
    assert of(a, "equal_highs")[0].status == "swept"


def _random_bars(n: int, seed: int) -> list[Bar]:
    rnd = random.Random(seed)  # noqa: S311 - deterministic test data
    p, out = 5000.0, []
    for i in range(n):
        o = p
        c = round((o + rnd.gauss(0, 1.5)) * 4) / 4
        h = round((max(o, c) + rnd.random() * 1.5) * 4) / 4
        low = round((min(o, c) - rnd.random() * 1.5) * 4) / 4
        out.append(Bar(T0 + timedelta(minutes=5 * i), o, h, low, c, rnd.randint(100, 1000)))
        p = c
    return out


def test_no_future_leakage_property() -> None:
    cfg = SmcConfig(max_zones=10**6)
    for seed in (1, 2, 3):
        bars = _random_bars(600, seed)
        full = analyze("ES", "5m", bars, TICK, cfg)
        for k in (120, 300, 451):
            cut = bars[k - 1].ts
            part = analyze("ES", "5m", bars[:k], TICK, cfg)
            assert all((p.confirmed_at or p.formed_at) <= cut for p in part.patterns)
            ids = {p.id for p in part.patterns}
            known = [
                p for p in full.patterns if p.confirmed_at and p.confirmed_at <= cut and p.type != "momentum_divergence"
            ]
            missing = [p.type for p in known if p.id not in ids]
            assert not missing, missing


def test_deterministic_ids_and_short_series() -> None:
    bars = _random_bars(200, 9)
    a, b = analyze("ES", "5m", bars, TICK), analyze("ES", "5m", bars, TICK)
    assert [p.id for p in a.patterns] == [p.id for p in b.patterns]
    assert analyze("ES", "5m", bars[:4], TICK).patterns == []


def test_pattern_stats_harness_is_causal_and_conservative() -> None:
    from app.research.pattern_stats import pattern_stats

    bars = _random_bars(3000, 11)
    out = pattern_stats("ES", "5m", bars, TICK, horizon=12, cost_ticks=2)
    assert out["patterns"] and out["method"]["entry"] == "open of the bar after confirmation"
    for row in out["patterns"]:
        assert row["in_sample"]["n"] + row["out_of_sample"]["n"] > 0
        assert row["label"] in ("RESEARCH ONLY", "predictive (on this data)")
    # A random walk has no edge: after costs, essentially nothing should qualify as predictive.
    assert sum(r["label"] != "RESEARCH ONLY" for r in out["patterns"]) <= 1
    # Costs are charged: the baseline expectancy after costs is negative on a driftless walk.
    base = out["patterns"][0]["baseline_in_sample"]
    assert base["expectancy"] < 0.5
