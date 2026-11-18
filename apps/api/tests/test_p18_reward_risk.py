"""Net reward:risk consistency: one cost model, one configurable minimum, T1 and T2 reported."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.research.backtest import BacktestParams, net_rr, simulate
from app.research.rr_study import CONFIGS, run_study, verdict
from app.services.checklist import ChecklistConfig, evaluate, reward_risk, rr_label
from app.services.confirmation import decide
from tests.test_p14_backtest_repair import seed
from tests.test_p16_confirmation import NOW, RISK, VOL, bars, readiness, run, signal


def cond(out: dict, key: str) -> dict:
    return next(c for c in out["conditions"] if c["key"] == key)


def test_default_minimum_is_conservative_and_validated() -> None:
    assert Settings().min_net_reward_risk == 1.5
    with pytest.raises(ValidationError):
        Settings(min_net_reward_risk=0.8)  # never below 1:1 net
    from app.services.readiness import config

    get_settings().min_net_reward_risk = 1.8
    assert config().min_rr == 1.8


def test_costs_follow_the_fill_policy() -> None:
    # ES long 5000, stop 4995 (5 pts), target 5010 (10 pts): gross 2.0
    r = reward_risk("ES", "long", 5000, 4995, 5010, ChecklistConfig())
    assert r["gross_ratio"] == 2.0
    assert r["reward_costs"] == 17.5  # $5 commission + 1 tick entry slippage; limit target, no exit slippage
    assert r["costs"] == 30.0  # $5 commission + entry and stop slippage
    assert r["ratio"] == round((500 - 17.5) / (250 + 30), 2)


def test_a_target_below_the_minimum_before_costs_is_explained_as_unreachable() -> None:
    out = run(signal=signal(rr_t1=1.0))  # strategy designed T1 at 1.0R
    c = cond(out, "reward_risk")
    assert out["state"] == "NO TRADE" and c["status"] == "FAIL"
    assert c["evidence"]["can_reach_minimum"] is False and "cannot reach 1.5:1 net" in c["reason"]
    assert c["name"] == "Net R:R to T1"


def test_minimum_is_configurable_and_never_silently_lowered() -> None:
    sig = signal(rr_t1=1.5)  # gross 1.5R -> net about 1.29 on ES with a 5-point stop
    default = decide(
        "ES", NOW, bars(), readiness(), sig, dict(RISK), volatility=VOL, regime={"labels": ["bullish trend"]}
    )
    assert default["state"] == "NO TRADE" and default["levels"]["min_net_rr"] == 1.5
    looser = decide(
        "ES",
        NOW,
        bars(),
        readiness(),
        sig,
        dict(RISK),
        volatility=VOL,
        regime={"labels": ["bullish trend"]},
        cfg=ChecklistConfig(min_rr=1.2),
    )
    assert looser["state"] == "LONG READY" and cond(looser, "reward_risk")["status"] == "PASS"


def test_t1_and_t2_are_both_reported() -> None:
    out = run()  # T1 2.0R, T2 3.0R gross
    lv = out["levels"]
    assert lv["rr_t1"] < lv["gross_rr_t1"] == 2.0 and lv["rr_t2"] < lv["gross_rr_t2"] == 3.0
    assert lv["rr_t2"] > lv["rr_t1"] and lv["costs_if_target"] < lv["costs_per_contract"]
    ev = cond(out, "reward_risk")["evidence"]
    assert ev["t1"]["ratio"] == lv["rr_t1"] and ev["t2"]["ratio"] == lv["rr_t2"]
    assert "T2: net" in cond(out, "reward_risk")["reason"]


def test_checklist_and_confirmation_agree() -> None:
    for rr_t1, cfg in ((2.0, ChecklistConfig()), (1.5, ChecklistConfig()), (1.5, ChecklistConfig(min_rr=1.2))):
        sig = signal(rr_t1=rr_t1)
        setup = {**sig, "detected_at_et": "10:25 ET"}
        ck = evaluate("ES", NOW, bars(), cfg, setup, {"status": "LIVE"}, {"status": "clear"}, {"known": True})
        item = next(i for i in ck["items"] if i["key"] == "reward_risk")
        out = decide(
            "ES",
            NOW,
            bars(),
            readiness(),
            sig,
            dict(RISK),
            volatility=VOL,
            regime={"labels": ["bullish trend"]},
            cfg=cfg,
        )
        c = cond(out, "reward_risk")
        assert item["evidence"]["ratio"] == c["evidence"]["t1"]["ratio"] == out["levels"]["rr_t1"]
        assert item["status"] == c["status"] and item["name"] == rr_label(cfg.min_rr)


def test_ready_wait_and_no_trade_under_the_default() -> None:
    assert run(signal=signal(rr_t1=2.0))["state"] == "LONG READY"
    assert run(signal=None)["state"] == "WAIT"
    assert run(signal=signal(rr_t1=1.5))["state"] == "NO TRADE"
    assert run("short", signal=signal("short", rr_t1=2.0))["state"] == "SHORT READY"


async def test_backtest_gate_and_exit_target_use_the_same_cost_model() -> None:
    b = await seed()
    p = BacktestParams(strategy_id="vwap_trend", symbol="ES")
    base = simulate(p, b)
    t2 = simulate(BacktestParams(strategy_id="vwap_trend", symbol="ES", exit_target=2), b)
    assert base and t2
    assert all(t.planned_rr and t.planned_rr >= 1.9 for t in t2)  # vwap_trend T2 is 2R
    gated = simulate(BacktestParams(strategy_id="vwap_trend", symbol="ES", min_net_rr=1.5), b)
    assert len(gated) <= len(base)
    for t in gated:  # every kept trade meets the gate on its signal levels
        assert net_rr(p, "ES", t.direction, t.signal_entry, t.stop, t.target) >= 1.5


async def test_study_labels_simulated_results_as_not_evidence() -> None:
    b = await seed()
    res = run_study(b, "ES", "simulated", ["vwap_trend"], 2.5, 1.0, configs=CONFIGS[:2])
    assert res["data_class"] == "SIMULATED" and res["best_supported"] is None
    assert all(r["verdict"].startswith("NOT EVIDENCE") for r in res["rows"])
    assert "No configuration has sufficient" in res["conclusion"]
    row = {"out_of_sample": {"trades": 12, "expectancy": 40.0}, "validation_passed": False, "failed_checks": []}
    assert verdict(row, simulated=False).startswith("INSUFFICIENT")
    row = {"out_of_sample": {"trades": 80, "expectancy": 25.0}, "validation_passed": True, "failed_checks": []}
    assert verdict(row, simulated=False).startswith("SUPPORTED")
    row = {"out_of_sample": {"trades": 80, "expectancy": -3.0}, "validation_passed": False, "failed_checks": ["oos"]}
    assert verdict(row, simulated=False).startswith("NOT SUPPORTED")
