"""Historical coverage, the in/out-of-sample split on short histories, the signal funnel and the
study's data-sufficiency verdicts (regressions for 'zero out-of-sample trades on 2,983 bars')."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

import pytest

from app.adapters.marketdata.simulated import SimulatedProvider
from app.research.backtest import BacktestParams, _days, run_backtest, simulate
from app.research.bars import Bar
from app.research.data_audit import audit_bars, compare_instruments
from app.research.rr_study import CONFIGS, run_study

# Tue 18:00 ET session open -> Thu 01:00 ET: one full regular session, then an overnight-only session.
START = datetime(2026, 11, 17, 23, 0, tzinfo=UTC)
END = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)


async def short_history(symbol: str = "ES") -> list[Bar]:
    c = await SimulatedProvider(clock=lambda: END).fetch_candles(symbol, START, END)
    return [Bar(x.ts, float(x.open), float(x.high), float(x.low), float(x.close), x.volume) for x in c]


async def test_overnight_only_day_is_not_the_out_of_sample_period() -> None:
    """Regression: the last 'trading day' was an overnight-only session (no 09:30-16:00 bars), so it
    became the whole out-of-sample period and could never contain a trade."""
    bars = await short_history()
    assert _days(bars) == ["2026-11-18"]  # the overnight-only 2026-11-19 is not a session for testing
    res = run_backtest(BacktestParams(strategy_id="vwap_trend", symbol="ES"), bars, "historical", sensitivity=False)
    sp = res["split"]
    assert sp["in_sample_days"] == 1 and sp["out_of_sample_days"] == 0  # one session: all in-sample, said so
    assert res["in_sample"]["trades"] == res["metrics"]["trades"]


async def test_split_keeps_a_session_on_each_side() -> None:
    end = datetime(2026, 11, 20, 6, 0, tzinfo=UTC)  # two regular sessions + an overnight-only one
    c = await SimulatedProvider(clock=lambda: end).fetch_candles("ES", START, end)
    bars = [Bar(x.ts, float(x.open), float(x.high), float(x.low), float(x.close), x.volume) for x in c]
    res = run_backtest(BacktestParams(strategy_id="vwap_trend", symbol="ES"), bars, "historical", sensitivity=False)
    sp = res["split"]
    assert sp["in_sample_days"] == 1 and sp["out_of_sample_days"] == 1 and sp["first_oos_day"] == "2026-11-19"
    assert sp["out_of_sample_bars"] > 300


async def test_signal_funnel_accounts_for_every_evaluation() -> None:
    bars = await short_history()
    for p in (
        BacktestParams(strategy_id="vwap_trend", symbol="ES"),
        BacktestParams(strategy_id="vwap_trend", symbol="ES", min_net_rr=1.5),
    ):
        st: Counter[str] = Counter()
        trades = simulate(p, bars, st)
        no_signal = sum(v for k, v in st.items() if k.startswith("no signal"))
        rejected = sum(v for k, v in st.items() if k.startswith(("signal rejected", "signal skipped")))
        assert st["evaluated"] == no_signal + st["signals"]
        assert st["signals"] == st["trades"] + rejected and st["trades"] == len(trades)
        assert st["5m bars"] == st["evaluated"] + st["outside evaluation window (09:35-15:30 ET)"]
    assert st["signal rejected: net R:R below 1.5"] > 0 and st["trades"] == 0  # vwap_trend T1 is 1R by design


async def test_missing_history_is_reported_as_such() -> None:
    bars = [b for b in await short_history() if b.ts >= datetime(2026, 11, 18, 14, 0, tzinfo=UTC)]  # from 09:00 ET
    st: Counter[str] = Counter()
    simulate(BacktestParams(strategy_id="vwap_trend", symbol="ES"), bars, st)
    assert st["no signal: missing history"] > 0


async def test_data_audit_detects_coverage_and_integrity_problems() -> None:
    bars = await short_history()
    d = audit_bars("ES", bars, START.replace(month=9), END)
    assert d["truncated_by_history"] is True and d["usable_sessions"] == 1 and d["verdict"] == "INSUFFICIENT"
    assert d["overnight_only_sessions"] == ["2026-11-19"] and d["sessions"] == 2
    assert all(v == 0 for v in d["integrity"].values())
    bad = list(bars)
    b0 = bad[100]
    bad.insert(101, b0)  # duplicate
    bad[200] = Bar(bad[200].ts.replace(second=30), 5000.1, 5001, 4999, 5000, 5)  # off-minute and off-tick
    bad[300] = Bar(bad[300].ts, 5000, 4990, 5010, 5000, 0)  # high < low, zero volume
    i = audit_bars("ES", bad, START, END)["integrity"]
    assert i["duplicate_timestamps"] == 1 and i["timestamps_not_on_the_minute"] == 1 and i["prices_off_tick"] == 1
    assert i["invalid_ohlc"] == 1 and i["zero_volume_bars"] == 1


async def test_instrument_independence_check() -> None:
    es, nq = await short_history("ES"), await short_history("NQ")
    assert compare_instruments("ES", es, "NQ", nq)["independent"] is True
    same = compare_instruments("ES", es, "NQ", es)
    assert same["independent"] is False and same["identical_bars"] == len(es)


async def test_study_says_insufficient_data_instead_of_zero_trades() -> None:
    bars = await short_history()
    res = run_study(
        bars, "ES", "historical", ["vwap_trend"], 2.5, 1.0, configs=CONFIGS[:1], requested=(START.replace(month=9), END)
    )
    row = res["rows"][0]
    assert row["verdict"].startswith("INSUFFICIENT DATA") and "signals" in row["explanation"]
    assert "no out-of-sample session" in row["explanation"]
    assert res["conclusion"].startswith("Data INSUFFICIENT") and res["data"]["usable_sessions"] == 1


@pytest.mark.parametrize("approve", [None, 0.0])
async def test_backfill_cli_never_starts_without_approval(
    approve: float | None, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.cli import backfill_cmd
    from app.core.config import get_settings
    from app.services.history import get_job

    get_settings().market_data_provider = "simulated"
    assert await backfill_cmd(["ES"], 6, approve, False) == 0
    out = capsys.readouterr().out
    assert "TOTAL ESTIMATED COST" in out
    job = await get_job()
    assert (job is None and "Estimate only" in out) if approve is None else (job and job["status"] == "queued")
