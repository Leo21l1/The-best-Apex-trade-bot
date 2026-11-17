from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import Settings
from app.core.db import get_sessionmaker
from app.marketdata.instruments import INSTRUMENTS
from app.marketdata.store import query_bars, upsert_candles
from app.marketdata.types import Timeframe
from app.research.bars import Bar, aggregate, bucket_start, closed_only
from app.research.indicators import (
    atr,
    bollinger,
    ema,
    reference_levels,
    rsi,
    session_vwap,
    sma,
)
from app.research.scoring import round_trip_cost, score_signal
from app.strategies.base import MIN_BARS_5M, MarketContext
from app.strategies.library import STRATEGIES, STRATEGY_BY_ID

END = datetime(2026, 11, 11, 21, 0, tzinfo=UTC)  # Wed 16:00 ET


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


async def sim_bars(symbol: str = "ES", days: int = 5, end: datetime = END) -> list[Bar]:
    p = SimulatedProvider(clock=lambda: end)
    cs = await p.fetch_candles(symbol, end - timedelta(days=days), end)
    return [Bar(c.ts, float(c.open), float(c.high), float(c.low), float(c.close), c.volume) for c in cs]


# --- instrument specifications -------------------------------------------------------------

EXPECTED = {  # symbol: (tick size, tick value, point value)
    "ES": ("0.25", "12.50", "50"),
    "MES": ("0.25", "1.25", "5"),
    "NQ": ("0.25", "5.00", "20"),
    "MNQ": ("0.25", "0.50", "2"),
    "YM": ("1", "5.00", "5"),
    "MYM": ("1", "0.50", "0.5"),
    "RTY": ("0.10", "5.00", "50"),
    "M2K": ("0.10", "0.50", "5"),
    "CL": ("0.01", "10.00", "1000"),
    "MCL": ("0.01", "1.00", "100"),
    "GC": ("0.10", "10.00", "100"),
    "MGC": ("0.10", "1.00", "10"),
}


@pytest.mark.parametrize("symbol", sorted(EXPECTED))
def test_contract_specifications(symbol: str) -> None:
    tick, value, point = (Decimal(x) for x in EXPECTED[symbol])
    inst = INSTRUMENTS[symbol]
    assert (inst.tick_size, inst.tick_value, inst.point_value) == (tick, value, point)
    sib = INSTRUMENTS[inst.sibling]
    assert sib.sibling == symbol and sib.is_micro != inst.is_micro
    micro, std = (inst, sib) if inst.is_micro else (sib, inst)
    assert micro.tick_value * 10 == std.tick_value  # a micro is one tenth of the standard
    assert micro.standard_equivalent == Decimal("0.1") and std.standard_equivalent == 1


def test_dollar_moves() -> None:
    es, cl, mnq = INSTRUMENTS["ES"], INSTRUMENTS["CL"], INSTRUMENTS["MNQ"]
    assert es.dollars_between(Decimal("5000.00"), Decimal("5002.50")) == Decimal("125.00")  # 10 ticks
    assert es.dollars_between(Decimal("5002.50"), Decimal("5000.00"), 3) == Decimal("375.00")
    assert cl.dollars_between(Decimal("70.00"), Decimal("70.25")) == Decimal("250.00")
    assert mnq.dollars_between(Decimal("18000"), Decimal("18010"), 4) == Decimal("80.00")
    assert es.ticks_between(Decimal("5000"), Decimal("5001")) == 4


# --- indicators ----------------------------------------------------------------------------


def test_sma_ema_known_values() -> None:
    v = [1.0, 2, 3, 4, 5, 6]
    assert sma(v, 3) == [None, None, 2.0, 3.0, 4.0, 5.0]
    e = ema(v, 3)  # seed SMA(1,2,3)=2; k=0.5
    assert e[:2] == [None, None] and e[2:] == [2.0, 3.0, 4.0, 5.0]
    assert ema([1.0, 2.0], 3) == [None, None]


def test_rsi_and_atr() -> None:
    up = [float(i) for i in range(30)]
    assert rsi(up, 14)[-1] == 100.0 and rsi(up, 14)[13] is None
    down = list(reversed(up))
    assert rsi(down, 14)[-1] == 0.0
    alt = [10.0 + (i % 2) for i in range(40)]
    assert 40 < (rsi(alt, 14)[-1] or 0) < 60
    bars = [Bar(utc(2026, 11, 11, 15, i), 10, 12, 9, 11, 1) for i in range(20)]
    a = atr(bars, 14)
    assert a[12] is None and a[13] == pytest.approx(3.0) and a[-1] == pytest.approx(3.0)
    gap = [*bars, Bar(utc(2026, 11, 11, 15, 20), 20, 21, 19, 20, 1)]  # gap: TR uses prior close
    assert atr(gap, 14)[-1] == pytest.approx((3.0 * 13 + 10.0) / 14)


def test_bollinger() -> None:
    mid, up, lo = bollinger([1.0, 3.0] * 10, 20, 2.0)
    assert mid[-1] == 2.0 and up[-1] == pytest.approx(4.0) and lo[-1] == pytest.approx(0.0)
    assert mid[18] is None


def test_session_vwap_is_rth_only_and_resets_daily() -> None:
    def b(ts: datetime, px: float, vol: int) -> Bar:
        return Bar(ts, px, px, px, px, vol)

    bars = [
        b(utc(2026, 11, 10, 14, 0), 50, 5),  # 09:00 ET, before the open: ignored
        b(utc(2026, 11, 10, 14, 30), 100, 10),  # 09:30 ET
        b(utc(2026, 11, 10, 14, 35), 110, 30),
        b(utc(2026, 11, 10, 23, 0), 500, 99),  # evening session: ignored
        b(utc(2026, 11, 11, 14, 30), 200, 1),  # next day resets
    ]
    vwap, sd = session_vwap(bars)
    assert vwap == [None, 100.0, pytest.approx(107.5), None, 200.0]
    assert sd[1] == 0.0 and sd[2] == pytest.approx(4.330127, rel=1e-5)


def test_reference_levels_have_no_lookahead() -> None:
    def m(ts: datetime, hi: float, lo: float, c: float) -> Bar:
        return Bar(ts, c, hi, lo, c, 1)

    bars = [
        m(utc(2026, 11, 10, 15, 0), 110, 100, 105),  # Tue 10:00 ET (prior RTH)
        m(utc(2026, 11, 10, 20, 59), 108, 95, 96),  # Tue 15:59 ET (prior RTH close)
        m(utc(2026, 11, 10, 21, 30), 150, 50, 100),  # Tue 16:30 ET: after RTH, not overnight
        m(utc(2026, 11, 10, 23, 0), 120, 97, 99),  # Tue 18:00 ET: Wednesday's overnight
        m(utc(2026, 11, 11, 8, 0), 118, 90, 92),  # Wed 03:00 ET overnight
        m(utc(2026, 11, 11, 14, 30), 101, 98, 100),  # Wed 09:30 ET
        m(utc(2026, 11, 11, 14, 44), 104, 99, 103),  # Wed 09:44 ET
        m(utc(2026, 11, 11, 14, 45), 999, 1, 500),  # Wed 09:45 ET: outside the opening range
    ]
    lv = reference_levels(bars, utc(2026, 11, 11, 14, 50))
    assert (lv.prior_day_high, lv.prior_day_low, lv.prior_day_close) == (110, 95, 96)
    assert (lv.overnight_high, lv.overnight_low) == (120, 90)
    assert (lv.opening_range_high, lv.opening_range_low) == (104, 98) and lv.opening_range_complete
    # At 09:40 ET the 09:44 bar has not happened: the range is partial and flagged incomplete.
    early = reference_levels(bars, utc(2026, 11, 11, 14, 40))
    assert early.opening_range_high == 101 and early.opening_range_complete is False
    # Before the open there is no opening range at all.
    pre = reference_levels(bars, utc(2026, 11, 11, 14, 0))
    assert pre.opening_range_high is None and pre.overnight_low == 90


# --- aggregation ---------------------------------------------------------------------------


def test_bucket_start_examples() -> None:
    t = utc(2026, 11, 11, 15, 7)  # Wed 10:07 ET
    assert bucket_start(t, Timeframe.M5) == utc(2026, 11, 11, 15, 5)
    assert bucket_start(t, Timeframe.H1) == utc(2026, 11, 11, 15, 0)
    assert bucket_start(t, Timeframe.H4) == utc(2026, 11, 11, 15, 0)  # 18:00 ET anchored
    assert bucket_start(t, Timeframe.D1) == utc(2026, 11, 11)
    assert bucket_start(utc(2026, 11, 11, 23, 30), Timeframe.D1) == utc(2026, 11, 12)  # evening
    assert bucket_start(t, Timeframe.W1) == utc(2026, 11, 9)
    # DST: July bars bucket on New York wall time too.
    assert bucket_start(utc(2026, 7, 15, 23, 30), Timeframe.H4) == utc(2026, 7, 15, 22, 0)


async def test_python_aggregation_matches_sql_for_every_timeframe() -> None:
    end = END
    p = SimulatedProvider(clock=lambda: end)
    candles = await p.fetch_candles("NQ", end - timedelta(days=8), end)
    async with get_sessionmaker()() as s:
        await upsert_candles(s, candles)
        await s.commit()
        bars = [Bar(c.ts, float(c.open), float(c.high), float(c.low), float(c.close), c.volume) for c in candles]
        for tf in Timeframe:
            sql = await query_bars(s, provider="simulated", symbol="NQ", timeframe=tf, limit=2000)
            py = aggregate(bars, tf)[-2000:]
            assert len(sql) == len(py) > 0, tf
            for a, b in zip(sql, py, strict=True):
                assert (a.ts, float(a.open), float(a.high), float(a.low), float(a.close), a.volume) == (
                    b.ts,
                    b.open,
                    b.high,
                    b.low,
                    b.close,
                    b.volume,
                ), tf


def test_closed_only_drops_forming_bar() -> None:
    bars = [Bar(utc(2026, 11, 11, 15, 0), 1, 1, 1, 1, 1), Bar(utc(2026, 11, 11, 15, 5), 1, 1, 1, 1, 1)]
    assert len(closed_only(bars, Timeframe.M5, utc(2026, 11, 11, 15, 9, 59))) == 1
    assert len(closed_only(bars, Timeframe.M5, utc(2026, 11, 11, 15, 10))) == 2


# --- strategies ----------------------------------------------------------------------------


def test_every_strategy_has_a_complete_specification() -> None:
    assert [s.spec.code for s in STRATEGIES] == list("ABCDEFGH")
    assert len(STRATEGY_BY_ID) == 8
    for s in STRATEGIES:
        spec = s.spec
        for field in (
            "required_data",
            "regime",
            "entry",
            "invalidation",
            "stop",
            "targets",
            "time_exit",
            "testing",
            "costs",
        ):
            assert len(getattr(spec, field)) > 5, (spec.id, field)
        assert spec.eligible_instruments


async def test_insufficient_history_and_ineligible_instruments_are_reported() -> None:
    bars = (await sim_bars())[-100:]
    ctx = MarketContext("ES", END, bars)
    for s in STRATEGIES:
        ev = s.evaluate(ctx)
        assert ev.signal is None and ev.note and "insufficient history" in ev.note
        assert str(MIN_BARS_5M) in ev.note
    cl = MarketContext("CL", END, await sim_bars("CL"))
    assert STRATEGY_BY_ID["orb"].evaluate(cl).note == "instrument not eligible"


async def test_signals_are_well_formed_explainable_and_deterministic() -> None:
    """Walk two sessions; every signal must be internally consistent and reproducible."""
    found: dict[str, int] = {}
    for symbol in ("ES", "CL"):
        bars = await sim_bars(symbol, days=6)
        tick = float(INSTRUMENTS[symbol].tick_size)
        now = utc(2026, 11, 10, 14, 35)
        while now <= END:
            if 14 <= now.hour < 21:
                sub = [b for b in bars if b.ts + timedelta(minutes=1) <= now]
                ctx = MarketContext(symbol, now, sub)
                for strat in STRATEGIES:
                    ev = strat.evaluate(ctx)
                    assert ev.conditions or ev.note
                    assert all(c.detail for c in ev.conditions)
                    sig = ev.signal
                    if sig is None:
                        continue
                    found[sig.strategy_id] = found.get(sig.strategy_id, 0) + 1
                    s = 1 if sig.direction == "long" else -1
                    assert all(c.passed for c in ev.conditions)
                    assert (sig.entry - sig.stop) * s >= tick  # stop on the losing side
                    assert all((t - sig.entry) * s > 0 for t in sig.targets)
                    assert sig.entry_low <= sig.entry <= sig.entry_high
                    for px in (sig.entry, sig.stop, *sig.targets, sig.entry_low, sig.entry_high):
                        assert abs(px / tick - round(px / tick)) < 1e-6  # on the tick grid
                    assert sig.bar_ts + timedelta(minutes=5) <= now  # triggered by a CLOSED bar
                    again = strat.evaluate(MarketContext(symbol, now, sub))
                    assert again.signal == sig
            now += timedelta(minutes=5)
    assert len(found) >= 4, found  # several strategies are reachable on the test data


def orb_scenario(breakout_volume: int = 5000) -> tuple[list[Bar], datetime]:
    """Flat tape, a 10-point opening range, then a 5-minute close above it on heavy volume."""
    bars: list[Bar] = []
    t = utc(2026, 11, 9, 23, 0)
    open_ = utc(2026, 11, 11, 14, 30)  # Wed 09:30 ET
    from app.marketdata.calendar import is_market_open

    while t < open_:
        if is_market_open(t):
            wob = 1.0 if (t.minute % 2) else -1.0
            bars.append(Bar(t, 5000, 5001.5 + wob, 4998.5 + wob, 5000 + wob, 1000))
        t += timedelta(minutes=1)
    for i in range(15):  # opening range 4995-5005
        bars.append(Bar(open_ + timedelta(minutes=i), 5000, 5005, 4995, 5000, 1000))
    for i in range(15, 20):  # 09:45-09:50: stays inside
        bars.append(Bar(open_ + timedelta(minutes=i), 5000, 5004, 4998, 5003, 1000))
    for i in range(20, 25):  # 09:50-09:55: breakout bar closes at 5009
        bars.append(Bar(open_ + timedelta(minutes=i), 5003, 5010, 5002, 5009, breakout_volume))
    return bars, open_ + timedelta(minutes=25)


def test_opening_range_breakout_triggers_on_crafted_tape() -> None:
    bars, now = orb_scenario()
    ev = STRATEGY_BY_ID["orb"].evaluate(MarketContext("ES", now, bars))
    assert ev.signal is not None, [(c.name, c.passed, c.detail) for c in ev.conditions]
    sig = ev.signal
    assert (sig.direction, sig.entry, sig.stop) == ("long", 5009.0, 5000.0)  # stop = range midpoint
    assert sig.targets == (5018.0, 5027.0)  # 1R and 2R with R = 9 points
    assert sig.reward_risk == pytest.approx(1.0)


def test_opening_range_breakout_requires_volume_and_freshness() -> None:
    bars, now = orb_scenario(breakout_volume=1000)  # no volume expansion
    ev = STRATEGY_BY_ID["orb"].evaluate(MarketContext("ES", now, bars))
    failed = [c.name for c in ev.conditions if not c.passed]
    assert ev.signal is None and failed == ["volume >= 1.2 x average"]
    # One bar later the breakout is no longer fresh.
    bars, now = orb_scenario()
    more = [Bar(now + timedelta(minutes=i), 5009, 5012, 5008, 5011, 5000) for i in range(5)]
    ev = STRATEGY_BY_ID["orb"].evaluate(MarketContext("ES", now + timedelta(minutes=5), bars + more))
    assert ev.signal is None and "fresh breakout" in [c.name for c in ev.conditions if not c.passed]


def test_liquidity_sweep_triggers_on_crafted_tape() -> None:
    bars, _ = orb_scenario()
    bars = [b for b in bars if b.ts < utc(2026, 11, 11, 14, 50)]
    # Overnight low on this tape is 4997.5. Sweep it and close back above with a long lower wick.
    start = utc(2026, 11, 11, 14, 50)
    sweep = [Bar(start + timedelta(minutes=i), 5000, 5001, 4994, 5000.5, 1500) for i in range(5)]
    ev = STRATEGY_BY_ID["sweep_reversal"].evaluate(MarketContext("ES", start + timedelta(minutes=5), bars + sweep))
    assert ev.signal is not None, [(c.name, c.passed, c.detail) for c in ev.conditions]
    assert ev.signal.direction == "long" and ev.signal.stop < 4994 and ev.signal.entry == 5000.5


# --- scoring -------------------------------------------------------------------------------


def test_assumed_round_trip_costs() -> None:
    s = Settings()
    assert round_trip_cost(MarketContext("ES", END, []), s) == 30.00  # 2 x (2.50 + 12.50)
    assert round_trip_cost(MarketContext("MES", END, []), s) == 3.70  # 2 x (0.60 + 1.25)
    assert round_trip_cost(MarketContext("CL", END, []), s) == 25.00
    free = Settings(cost_commission_per_side_standard=0, cost_slippage_ticks_per_side=0)
    assert round_trip_cost(MarketContext("ES", END, []), free) == 0


def test_score_is_bounded_and_itemised() -> None:
    bars, now = orb_scenario()
    ctx = MarketContext("ES", now, bars)
    sig = STRATEGY_BY_ID["orb"].evaluate(ctx).signal
    assert sig is not None
    score, parts = score_signal(ctx, sig, 30.0)
    assert 0 <= score <= 100 and sum(p["max"] for p in parts) == 100
    assert score == pytest.approx(sum(p["points"] for p in parts))
    assert all(0 <= p["points"] <= p["max"] and p["detail"] for p in parts)
    by = {p["name"]: p for p in parts}
    assert by["Reward:risk to first target"]["points"] == 12.5  # 1R of a 2R cap
    # ES risk 9 points = $450; $30 cost = 6.7% -> 20 * (1 - 0.0667/0.25)
    assert by["Cost efficiency"]["points"] == pytest.approx(14.7, abs=0.05)
    worse, _ = score_signal(ctx, sig, 200.0)
    assert worse < score
