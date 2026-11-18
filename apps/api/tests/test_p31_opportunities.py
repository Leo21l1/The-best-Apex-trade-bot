"""One market opportunity detected by several strategies is counted once in combined performance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.db import get_sessionmaker
from app.models.signal import SetupSignal
from app.research import confluence_study as cs
from app.services import signal_tracker as st

T = datetime(2025, 12, 1, 15, 40, tzinfo=UTC)  # 10:40 ET


def det(
    strategy: str,
    minutes: int = 0,
    stop: float = 25294.50,
    usd: float = 2660.0,
    risk: float = 1000.0,
    score: int = 4,
    end_min: int = 45,
    **kw: Any,
) -> dict[str, Any]:
    t = T + timedelta(minutes=minutes)
    return {
        "symbol": "NQ",
        "direction": "long",
        "entry": 25344.25,
        "entry_low": 25343.0,
        "entry_high": 25345.5,
        "stop": stop,
        "tp1": 25477.75,
        "tp2": None,
        "confirmed_at": t,
        "expires_at": t + timedelta(minutes=30),
        "filled_at": t + timedelta(minutes=1),
        "outcome_at": t + timedelta(minutes=end_min),
        "status": "CONFIRMED",
        "outcome": "tp1" if usd > 0 else "stop",
        "outcome_usd": usd,
        "outcome_r": usd / risk,
        "net_risk_usd": risk,
        "costs_usd": 15.0,
        "strategy_id": strategy,
        "score": score,
        **kw,
    }


def test_three_strategies_one_plan_is_one_opportunity() -> None:
    items = [det("orb"), det("vwap_trend"), det("momentum_pullback")]
    ev = st.group_opportunities(items)
    assert len(ev) == 1 and ev[0]["detections"] == 3 and ev[0]["consistent"]
    assert ev[0]["strategies"] == ["momentum_pullback", "orb", "vwap_trend"]
    assert ev[0]["opportunity_id"] == st.opportunity_id(items[2])  # stable: same id from any member
    by_strategy = st.compare_thresholds(items)["tiers"]["4/5 balanced (live threshold)"]
    combined = st.compare_thresholds(ev)["tiers"]["4/5 balanced (live threshold)"]
    assert by_strategy["resolved"] == 3 and by_strategy["net_pnl"] == 7980.0
    assert combined["resolved"] == 1 and combined["net_pnl"] == 2660.0 and combined["signals"] == 1


def test_genuinely_different_plans_are_not_merged_but_cannot_both_be_executed() -> None:
    a = det("orb")
    b = det("smc_sweep_mss", stop=25294.25, risk=1005.0)  # one tick lower stop: a different trade plan
    later = det("vwap_trend", minutes=120)  # same levels two hours later: a separate opportunity
    ev = st.group_opportunities([a, b, later])
    assert len(ev) == 3
    seq = st.one_position_at_a_time(ev)
    # a and b start together: one account takes one (tie-break: higher score, then smaller planned risk).
    assert [e["strategies"] for e in seq] == [["orb"], ["vwap_trend"]]
    overlap = det("mean_reversion", minutes=20, stop=25300.0)  # triggers while "orb" is still open
    assert len(st.one_position_at_a_time(st.group_opportunities([a, overlap]))) == 1


def test_inconsistent_outcomes_inside_one_opportunity_are_flagged() -> None:
    ev = st.group_opportunities([det("orb"), det("vwap_trend", usd=-1000.0)])
    assert len(ev) == 1 and not ev[0]["consistent"]


def test_exposure_if_every_detection_were_executed() -> None:
    items = [det("orb"), det("vwap_trend"), det("momentum_pullback")]
    assert st.exposure(items) == {
        "positions": 3,
        "max_concurrent_positions": 3,
        "max_concurrent_planned_risk_usd": 3000.0,
    }
    assert st.exposure(st.group_opportunities(items))["max_concurrent_positions"] == 1


def test_risk_eligibility_and_three_of_five_verdict() -> None:
    wide = det("orb", risk=1200.0, usd=2400.0, score=3)
    ok = det("vwap_trend", minutes=180, risk=500.0, usd=-500.0, score=3, stop=25300.0)
    ev = st.group_opportunities([wide, ok])
    eligible = [e for e in ev if e["net_risk_usd"] <= 1000]
    assert [e["strategies"] for e in eligible] == [["vwap_trend"]]  # structural stops are never moved
    views = {"risk_eligible_one_position": st.compare_thresholds(st.one_position_at_a_time(eligible))}
    assert "not distinguishable from zero" in cs._verdict_3(views) or "negative" in cs._verdict_3(views)
    assert "nothing to evaluate" in cs._verdict_3({"risk_eligible_one_position": st.compare_thresholds([])})


async def test_live_forward_stats_count_each_opportunity_once(admin_client: httpx.AsyncClient) -> None:
    now = datetime.now(UTC) - timedelta(days=1)
    async with get_sessionmaker()() as s:
        for sid in ("orb", "vwap_trend", "momentum_pullback"):
            s.add(
                SetupSignal(
                    key=f"NQ|{sid}|long|x|Q",
                    symbol="NQ",
                    contract="NQZ5",
                    data_class="live",
                    strategy_id=sid,
                    engine_version="setups-1.1",
                    direction="long",
                    score=4,
                    qualified=True,
                    evidence="INSUFFICIENT EVIDENCE",
                    confirmed_at=now,
                    detected_at=now,
                    expires_at=now + timedelta(minutes=30),
                    entry=25344.25,
                    entry_low=25343.0,
                    entry_high=25345.5,
                    stop=25294.5,
                    tp1=25477.75,
                    tp2=None,
                    net_rr_tp1=2.6,
                    net_risk_usd=1000.0,
                    net_reward_usd=2660.0,
                    original={"costs_usd": 15.0},
                    status="CONFIRMED",
                    filled_at=now + timedelta(minutes=1),
                    outcome="tp1",
                    outcome_at=now + timedelta(minutes=40),
                    outcome_r=2.66,
                    outcome_usd=2660.0,
                )
            )
        await s.commit()
    r = (await admin_client.get("/api/signals/stats")).json()
    assert r["recorded"] == 3 and r["opportunities"] == 1 and r["multi_strategy_opportunities"] == 1
    assert r["comparison"]["tiers"]["4/5 balanced (live threshold)"]["resolved"] == 1
    assert r["strategy_detections"]["tiers"]["4/5 balanced (live threshold)"]["resolved"] == 3
