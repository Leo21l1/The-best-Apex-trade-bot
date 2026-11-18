"""Research engine: rollover safety, statistics, execution, stress, Apex replay, study periods, API."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.marketdata.calendar import trading_day
from app.marketdata.store import upsert_candles
from app.research import rollover
from app.research.apex_replay import replay
from app.research.backtest import BacktestParams, Trade, execute, generate_signals, simulate
from app.research.bars import Bar
from app.research.data_audit import required_rth_bars
from app.research.stats import bootstrap_mean_ci, trade_stats, wilson
from app.research.study import plan_periods, run_full_study
from app.risk.engine import RuleSet
from app.services.confirmation import decide, live_config_key
from tests.test_p16_confirmation import NOW, RISK, VOL, bars, readiness, signal

RULES = RuleSet("test", Decimal(50000), Decimal(3000), Decimal(2000), Decimal(1000), Decimal(6))


# --- rollover -------------------------------------------------------------------------------------


def test_contract_inference_and_roll_dates() -> None:
    assert rollover.roll_date(2026, 9) == date(2026, 9, 10) and rollover.roll_date(2026, 12) == date(2026, 12, 10)
    assert rollover.front_contract("ES", date(2026, 9, 9)) == "ESU6"
    assert rollover.front_contract("ES", date(2026, 9, 10)) == "ESZ6"
    assert rollover.front_contract("NQ", date(2026, 12, 11)) == "NQH7"
    excl = rollover.excluded_dates("ES", date(2026, 9, 1), date(2026, 9, 30))
    assert set(excl) == {date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 14)}
    assert "ESU6 -> ESZ6" in excl[date(2026, 9, 10)]
    assert rollover.excluded_dates("CL", date(2026, 9, 1), date(2026, 9, 30)) == {}


def _series(start: datetime, end: datetime, jump_on: date | None = None, jump: float = 60.0) -> list[Bar]:
    """1-minute bars inside every CME session in [start, end); a constant price, shifted from ``jump_on``."""
    out, t, p = [], start, 5000.0
    while t < end:
        d = trading_day(t)
        bnd = rollover.session_bounds("ES", d)
        if bnd and bnd[0] <= t < bnd[1]:
            px = p + (jump if jump_on and d >= jump_on else 0.0)
            out.append(Bar(t, px, px + 0.5, px - 0.5, px, 100))
        t += timedelta(minutes=1)
    return out


def test_segments_never_mix_contracts_and_drop_the_roll_window() -> None:
    b = _series(datetime(2026, 9, 3, 22, 0, tzinfo=UTC), datetime(2026, 9, 18, 21, 0, tzinfo=UTC), date(2026, 9, 10))
    segs, excl = rollover.segments("ES", b)
    assert [c for c, _ in segs] == ["ESU6", "ESZ6"]
    days = {c: {trading_day(x.ts) for x in s} for c, s in segs}
    assert max(days["ESU6"]) < date(2026, 9, 9) and min(days["ESZ6"]) > date(2026, 9, 14)
    assert not any(d.isoformat() in excl for s in days.values() for d in s)
    disc = rollover.detect_discontinuities("ES", b)
    assert disc and all(x["explained_by_roll"] for x in disc)
    odd = _series(
        datetime(2026, 10, 1, 22, 0, tzinfo=UTC), datetime(2026, 10, 23, 21, 0, tzinfo=UTC), date(2026, 10, 15)
    )
    assert any(
        not x["explained_by_roll"] and x["date"] == "2026-10-15" for x in rollover.detect_discontinuities("ES", odd)
    )


def test_daylight_saving_week_keeps_sessions_and_regular_hours_correct() -> None:
    # 2026-11-01 (Sunday) clocks fall back: the Monday session opens at 23:00 UTC instead of 22:00 UTC.
    b = _series(datetime(2026, 10, 29, 21, 0, tzinfo=UTC), datetime(2026, 11, 4, 0, 0, tzinfo=UTC))
    by = Counter(trading_day(x.ts) for x in b)
    assert by[date(2026, 10, 30)] == by[date(2026, 11, 2)] == by[date(2026, 11, 3)] == 1380
    assert required_rth_bars("ES", date(2026, 11, 2)) == 300
    rth = sum(1 for x in b if trading_day(x.ts) == date(2026, 11, 2) and 14.5 <= x.ts.hour + x.ts.minute / 60 < 21)
    assert rth == 390  # 09:30-16:00 EST = 14:30-21:00 UTC after the change


# --- statistics -----------------------------------------------------------------------------------


def test_wilson_lower_bound_does_not_let_small_samples_win() -> None:
    small, large = wilson(9, 10), wilson(400, 600)
    assert small and large and round(small[0], 3) == 0.596 and large[0] > small[0]
    assert wilson(0, 0) is None
    assert bootstrap_mean_ci([1.0, -2.0, 3.0, 4.0]) == bootstrap_mean_ci([1.0, -2.0, 3.0, 4.0])  # seeded


def _t(pnl: float, day: str, mae: float = 0.0, **kw: object) -> Trade:
    base = dict(
        direction="long",
        signal_ts=f"{day}T15:00:00+00:00",
        entry_ts=f"{day}T15:01:00+00:00",
        exit_ts=f"{day}T15:10:00+00:00",
        signal_entry=1.0,
        entry=1.0,
        stop=0.5,
        target=2.0,
        exit=1.0,
        exit_reason="target",
        quantity=1,
        pnl=pnl,
        commission=5.0,
        r=pnl / 250,
        trading_day=day,
        bars_held=10,
        mae_usd=mae,
    )
    base.update(kw)
    return Trade(**base)  # type: ignore[arg-type]


def test_trade_stats_definitions() -> None:
    s = trade_stats([_t(100, "2026-10-01"), _t(-50, "2026-10-02"), _t(0, "2026-11-02"), _t(30, "2026-11-03")], 10)
    assert (s["wins"], s["losses"], s["breakeven"], s["trades"]) == (2, 1, 1, 4)
    assert s["win_rate"] == 0.5  # breakeven counts in the denominator
    assert s["profit_factor"] == round(130 / 50, 3) and s["net_pnl"] == 80 and s["max_drawdown"] == 50
    assert s["monthly"] == {"2026-10": {"trades": 2, "net": 50.0}, "2026-11": {"trades": 2, "net": 30.0}}
    assert s["trades_per_session"] == 0.4 and s["avg_daily_pnl"] == 8.0


# --- execution ------------------------------------------------------------------------------------


async def _sim_bars(days: int = 9) -> list[Bar]:
    end = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)
    c = await SimulatedProvider(clock=lambda: end).fetch_candles("ES", end - timedelta(days=days), end)
    return [Bar(x.ts, float(x.open), float(x.high), float(x.low), float(x.close), x.volume) for x in c]


async def test_generate_and_execute_equal_simulate_and_audit_fields_reconcile() -> None:
    b = await _sim_bars()
    p = BacktestParams(strategy_id="vwap_trend", symbol="ES")
    rp, ev = generate_signals(p, b)
    a = execute(p, rp, ev)
    assert [(t.entry_ts, t.exit_ts, t.pnl) for t in a] == [(t.entry_ts, t.exit_ts, t.pnl) for t in simulate(p, b)]
    for t in a:
        assert t.mfe_usd >= 0 and t.mae_usd >= 0 and t.risk_usd > 0
        assert abs(t.gross_pnl - (t.pnl + t.commission + t.slippage_usd)) < 0.011
        assert t.slippage_usd == (12.5 if t.exit_reason == "target" else 25.0)
        if t.exit_reason == "stop":
            assert t.mae_usd >= t.risk_usd - 1e-6  # price reached the stop


async def test_stress_parameters_only_ever_make_results_worse_or_equal() -> None:
    b = await _sim_bars()
    p = BacktestParams(strategy_id="vwap_trend", symbol="ES")
    rp, ev = generate_signals(p, b)
    base = {t.entry_ts: t for t in execute(p, rp, ev)}
    stop = {t.entry_ts: t for t in execute(replace(p, stop_slippage_extra_ticks=1.0), rp, ev)}
    for k, t in base.items():
        if k in stop and t.exit_reason == "stop" and stop[k].exit_reason == "stop":
            assert stop[k].pnl == pytest.approx(t.pnl - 12.5)
        elif k in stop and t.exit_reason == "target":
            assert stop[k].pnl == t.pnl
    late = execute(replace(p, entry_delay_bars=1), rp, ev)
    assert late and all(t.entry_ts not in base or base[t.entry_ts].signal_ts != t.signal_ts for t in late)


# --- Apex replay ----------------------------------------------------------------------------------


def test_apex_replay_pass_fail_day_stop_and_independent_attempts() -> None:
    days = [f"2026-10-{d:02d}" for d in range(1, 21)]
    wins = [_t(600, d) for d in days[:5]]  # +3,000 by day 5 -> pass
    loss = [_t(-100, days[5], mae=2500)]  # intraday excursion through the trailing threshold -> fail
    rest = [_t(-600, days[7]), _t(-600, days[7]), _t(-50, days[7])]  # day stop after -1,200 (3rd skipped)
    r = replay(wins + loss + rest, RULES, days)
    assert r["detail"][0]["result"] == "pass" and r["detail"][0]["trading_days"] == 5
    assert r["detail"][1]["result"] == "fail" and "intraday" in r["detail"][1]["reason"]
    assert r["detail"][2]["day_stops"] == 1 and r["detail"][2]["result"] == "unresolved"
    assert r["resolved"] == 2 and r["pass_rate"] == 0.5 and r["rules"]["label"] == "UNVERIFIED rule profile"


# --- study ----------------------------------------------------------------------------------------


def test_period_plan_is_chronological_and_disjoint() -> None:
    sess = [f"s{i:03d}" for i in range(260)]
    p = plan_periods(sess)
    wf = [d for b in p["walk_forward_blocks"] for d in b]
    assert p["research"] == sess[:100] and p["holdout"] == sess[-50:] and wf == sess[100:210]
    assert all(len(b) == 25 for b in p["walk_forward_blocks"][:-1]) and not p["preliminary"]
    small = plan_periods(sess[:40])
    assert small["preliminary"] and small["research"] == sess[:16] and small["holdout"] == sess[32:40]


async def test_full_study_on_simulated_data_is_never_evidence() -> None:
    b = await _sim_bars(20)
    res = run_full_study("ES", b, "simulated", RULES, 2.5, 1.0, strategies=["vwap_trend"])
    assert res["plan"]["preliminary"] and res["comparisons"] == 5
    assert all(r["evidence"] == "Insufficient evidence" for r in res["rows"])
    assert {t["period"] for t in res["trade_log"]} <= {"research", "walk_forward", "holdout"}
    wf = [t for t in res["trade_log"] if t["period"] == "walk_forward"]
    ho = [t for t in res["trade_log"] if t["period"] == "holdout"]
    if wf and ho:
        assert max(t["trading_day"] for t in wf) < min(t["trading_day"] for t in ho)  # holdout strictly later
    row = res["rows"][0]
    assert set(row["stress"]) == {"base", "slip2", "costs_high", "stop_slip", "late_entry"}
    assert all(x["small_sample"] for x in res["rankings"]["win_rate_all"] if x["trades"] < 30)


# --- live evidence gate ---------------------------------------------------------------------------


def test_ready_requires_validated_evidence_when_configured() -> None:
    def run(history: dict | None, req: bool) -> dict:
        return decide(
            "ES",
            NOW,
            bars(),
            readiness(),
            signal(),
            dict(RISK),
            history=history,
            volatility=VOL,
            regime={"labels": ["bullish trend"]},
            require_validated=req,
        )

    ready = run(None, False)
    assert ready["state"] == "LONG READY" and ready["actionable"] is True and ready["current_price"] is not None
    assert ready["config"] == "t1_net15"
    gated = run(None, True)
    assert gated["state"] == "WAIT" and any(m["key"] == "history" for m in gated["missing"])
    assert gated["actionable"] is False
    promising = {"available": True, "evidence": "Promising but unvalidated", "unmet": ["holdout"], "config": "t1_net15"}
    assert run(promising, True)["state"] == "WAIT"
    ok = {
        "available": True,
        "evidence": "Validated for paper-trading consideration",
        "config": "t1_net15",
        "wf_trades": 80,
        "study_id": 1,
    }
    assert run(ok, True)["state"] == "LONG READY"
    neg = {"available": True, "evidence": "Negative evidence", "unmet": ["expectancy -3"]}
    assert run(neg, True)["state"] == "WAIT" and run(neg, True)["evidence_classification"] == "Negative evidence"
    assert live_config_key(1.5) == "t1_net15" and live_config_key(1.2) == "t1_net12" and live_config_key(2) == "t1_all"


# --- API ------------------------------------------------------------------------------------------


async def test_research_api(admin_client: httpx.AsyncClient) -> None:
    from app.services.research_study import run_and_store

    get_settings().market_data_provider = "simulated"
    now = datetime.now(UTC)
    c = await SimulatedProvider(clock=lambda: now).fetch_candles("ES", now - timedelta(days=12), now)
    async with get_sessionmaker()() as s:
        await upsert_candles(s, c)
        await s.commit()
    assert (await admin_client.get("/api/research/studies")).json() == []
    sid, _res = await run_and_store(get_sessionmaker(), "ES", 14, ["vwap_trend"], 1)
    lst = (await admin_client.get("/api/research/studies", params={"symbol": "ES"})).json()
    assert lst[0]["id"] == sid and lst[0]["data_class"] == "SIMULATED"
    st = (await admin_client.get(f"/api/research/studies/{sid}")).json()
    assert st["headline"]["available"] is False and len(st["result"]["rows"]) == 5
    tr = (await admin_client.get(f"/api/research/studies/{sid}/trades", params={"config": "t1_all"})).json()
    assert tr["total"] == len([t for t in tr["trades"]]) or tr["total"] > len(tr["trades"])
    csv = await admin_client.get(f"/api/research/studies/{sid}/export/leaderboard.csv")
    assert csv.status_code == 200 and csv.text.startswith("strategy_id,config,direction,period")
    assert (await admin_client.get("/api/research/studies/999999")).status_code == 404
    inv = (await admin_client.get("/api/research/inventory")).json()
    assert len(inv) == 11 and all(i["eligible_for_evaluation"] for i in inv)
