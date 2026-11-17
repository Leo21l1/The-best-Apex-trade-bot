from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from app.adapters.marketdata.simulated import SimulatedProvider
from app.research.backtest import (
    BacktestParams,
    Trade,
    evaluate_validation,
    metrics,
    monte_carlo,
    run_backtest,
    simulate,
)
from app.research.bars import Bar
from app.strategies.base import Signal

END = datetime(2026, 11, 13, 21, 0, tzinfo=UTC)


async def sim_bars(symbol: str = "ES", days: int = 25) -> list[Bar]:
    p = SimulatedProvider(clock=lambda: END)
    cs = await p.fetch_candles(symbol, END - timedelta(days=days), END)
    return [Bar(c.ts, float(c.open), float(c.high), float(c.low), float(c.close), c.volume) for c in cs]


async def test_backtest_is_reproducible_and_fingerprinted() -> None:
    bars = await sim_bars()
    p = BacktestParams("vwap_trend", "ES", monte_carlo_runs=200)
    a = run_backtest(p, bars, "simulated", sensitivity=False)
    b = run_backtest(p, bars, "simulated", sensitivity=False)
    assert a["result_sha256"] == b["result_sha256"]
    assert a["dataset"]["sha256"] == b["dataset"]["sha256"] and a["dataset"]["bars"] == len(bars)
    assert a["metrics"]["trades"] > 5
    # A different seed changes only the Monte Carlo section.
    c = run_backtest(replace(p, seed=99), bars, "simulated", sensitivity=False)
    assert c["metrics"] == a["metrics"] and c["monte_carlo"] != a["monte_carlo"]
    # Changing the data changes the fingerprint.
    d = run_backtest(p, bars[:-500], "simulated", sensitivity=False)
    assert d["dataset"]["sha256"] != a["dataset"]["sha256"]


async def test_no_look_ahead_future_bars_do_not_change_past_trades() -> None:
    bars = await sim_bars(days=25)
    cut = len(bars) - 3000
    early = simulate(BacktestParams("mtf_trend", "ES"), bars[:cut])
    full = simulate(BacktestParams("mtf_trend", "ES"), bars)
    last_exit = bars[cut - 1].ts
    # Trades that closed before the cut must be identical when more future data is added.
    closed_early = [t for t in early if t.exit_reason != "data_end"]
    in_full = [t for t in full if datetime.fromisoformat(t.exit_ts) <= last_exit][: len(closed_early)]
    assert closed_early and closed_early == in_full


async def test_trades_obey_fill_rules() -> None:
    bars = await sim_bars()
    trades = simulate(BacktestParams("vwap_trend", "ES", slippage_ticks=1), bars)
    by_ts = {b.ts.isoformat(): b for b in bars}
    for t in trades:
        s = 1 if t.direction == "long" else -1
        entry_bar = by_ts[t.entry_ts]
        assert t.entry == pytest.approx(entry_bar.open + s * 0.25)  # next-bar open + 1 tick slippage
        assert t.entry_ts >= t.signal_ts  # never fills before the signal bar closed
        if t.exit_reason == "target":
            assert t.exit == t.target
        if t.exit_reason == "stop":
            assert (t.exit - t.stop) * s <= -0.25 + 1e-9  # stop fill includes adverse slippage
        assert t.commission == 5.0 and t.pnl == pytest.approx((t.exit - t.entry) * s * 50 - 5.0, abs=0.01)
    # One position at a time: no overlapping trades.
    for a, b in pairwise(trades):
        assert b.entry_ts > a.exit_ts


def _signal_stub(monkeypatch: pytest.MonkeyPatch, sig_for: dict[str, Signal]) -> None:
    from app.strategies import library

    strat = library.STRATEGY_BY_ID["vwap_trend"]

    def fake_eval(ctx):  # type: ignore[no-untyped-def]
        from app.strategies.base import Evaluation

        return Evaluation("vwap_trend", ctx.symbol, ctx.now, (), sig_for.get(ctx.now.isoformat()))

    monkeypatch.setattr(strat, "evaluate", fake_eval)


def _flat_day(start: datetime, minutes: int, price: float = 5000.0) -> list[Bar]:
    return [Bar(start + timedelta(minutes=i), price, price + 0.5, price - 0.5, price, 100) for i in range(minutes)]


@pytest.mark.parametrize(
    ("scenario", "expect_reason", "expect_exit"),
    [
        ("target_touch_only", "time", None),  # touching the target is not a fill
        ("target_through", "target", 5004.0),
        ("stop_and_target_same_bar", "stop", 4997.75),  # stop assumed first, minus 1 tick slippage
        ("gap_through_stop", "stop", 4994.75),  # gap: open 4995 minus slippage
    ],
)
def test_crafted_fill_scenarios(
    monkeypatch: pytest.MonkeyPatch, scenario: str, expect_reason: str, expect_exit: float | None
) -> None:
    start = datetime(2026, 11, 11, 14, 30, tzinfo=UTC)  # 09:30 ET
    bars = _flat_day(start, 7 * 60)  # 09:30-16:29 ET, past the 15:55 flat-by time
    sig_close = start + timedelta(minutes=30)  # 10:00 ET
    sig = Signal(
        "vwap_trend", "ES", "long", sig_close - timedelta(minutes=5), 5000.0, 5000.0, 5000.25, 4998.0, (5004.0, 5006.0)
    )
    _signal_stub(monkeypatch, {sig_close.isoformat(): sig})
    i = 32  # a bar two minutes after entry
    b = bars[i]
    if scenario == "target_touch_only":
        bars[i] = Bar(b.ts, 5000, 5004.0, 4999.5, 5000, 100)
    elif scenario == "target_through":
        bars[i] = Bar(b.ts, 5000, 5004.25, 4999.5, 5004, 100)
    elif scenario == "stop_and_target_same_bar":
        bars[i] = Bar(b.ts, 5000, 5005.0, 4997.0, 5000, 100)
    else:
        bars[i] = Bar(b.ts, 4995.0, 4995.5, 4994.0, 4995, 100)
    trades = simulate(BacktestParams("vwap_trend", "ES"), bars)
    assert len(trades) == 1
    t = trades[0]
    assert t.entry == 5000.25 and t.exit_reason == expect_reason
    if expect_exit is not None:
        assert t.exit == pytest.approx(expect_exit)
    else:  # flat by 15:55 ET at that bar's open minus slippage
        assert t.exit_ts.endswith("20:55:00+00:00") and t.exit == pytest.approx(4999.75)


def test_metrics_by_hand() -> None:
    def tr(day: str, pnl: float, r: float) -> Trade:
        return Trade("long", "", "", "", 0, 0, 0, 0, 0, "stop", 1, pnl, 5.0, r, day, 10)

    trades = [
        tr("2026-11-02", 100, 1.0),
        tr("2026-11-02", -50, -0.5),
        tr("2026-11-03", -50, -0.5),
        tr("2026-11-04", 200, 2.0),
    ]
    m = metrics(trades, trading_days=5, total_minutes=400)
    assert m["trades"] == 4 and m["win_rate"] == 0.5
    assert m["avg_win"] == 150 and m["avg_loss"] == -50
    assert m["profit_factor"] == 3.0 and m["expectancy"] == 50 and m["net_profit"] == 200
    assert m["max_drawdown"] == 100  # 100 -> 0 after two losses
    assert m["expectancy_r"] == 0.5 and m["max_drawdown_r"] == 1.0
    assert m["trades_per_day"] == 0.8 and m["exposure"] == 0.1 and m["total_commission"] == 20
    assert metrics([], 5, 100)["trades"] == 0


def test_monte_carlo_seeded() -> None:
    trades = [
        Trade("long", "", "", "", 0, 0, 0, 0, 0, "stop", 1, x, 0, 0, "d", 1)
        for x in [100, -50, 80, -40, 120, -60, 30, -20, 90, -70, 10, 50]
    ]
    a, b = monte_carlo(trades, 500, 1), monte_carlo(trades, 500, 1)
    assert a == b and a is not None
    assert a["net_profit"]["p5"] <= a["net_profit"]["p50"] <= a["net_profit"]["p95"]
    assert monte_carlo(trades[:5], 500, 1) is None


def test_validation_requires_real_data_and_out_of_sample_evidence() -> None:
    good = {"trades": 150, "profit_factor": 1.5, "expectancy_r": 0.2, "max_drawdown_r": 8, "net_profit": 1}
    oos = {**good, "trades": 40}
    folds = [{"metrics": {"net_profit": 10}}] * 3 + [{"metrics": {"net_profit": -5}}]
    assert evaluate_validation(good, oos, folds, good, "historical")["passed"] is True
    sim = evaluate_validation(good, oos, folds, good, "simulated")
    assert sim["passed"] is False and not sim["checks"][0]["passed"]
    weak = evaluate_validation(good, {**oos, "profit_factor": 1.1}, folds, good, "historical")
    assert weak["passed"] is False
    few_folds = [{"metrics": {"net_profit": 10}}, {"metrics": {"net_profit": -5}}] * 2
    assert evaluate_validation(good, oos, few_folds, good, "historical")["passed"] is False


async def test_full_experiment_contents() -> None:
    bars = await sim_bars()
    r = run_backtest(BacktestParams("vwap_trend", "ES", monte_carlo_runs=100), bars, "simulated", prior_configs=3)
    assert r["validation"]["passed"] is False  # simulated data can never validate
    assert len(r["walk_forward"]) == 4 and r["split"]["out_of_sample_days"] > 0
    assert {s["variant"] for s in r["sensitivity"]} == {
        "target 1.0R",
        "target 2.0R",
        "target 3.0R",
        "slippage 0 tick",
        "slippage 2 tick",
    }
    assert r["multiple_testing"]["configurations_tested"] == 3 + 6
    assert r["in_sample"]["trades"] + r["out_of_sample"]["trades"] == r["metrics"]["trades"]
    assert r["assumptions"] and r["engine_version"]
    with pytest.raises(ValueError, match="not eligible"):
        run_backtest(BacktestParams("orb", "CL"), bars, "simulated")
