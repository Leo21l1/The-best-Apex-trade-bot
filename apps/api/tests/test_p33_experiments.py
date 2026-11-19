"""Experimental hypotheses stay isolated from live; survival, dependence, costs, shared-account combine."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

from app.research import confluence_study as cs
from app.services import setups
from tests.test_p29_setups import CFG, scenario, signal
from tests.test_p31_opportunities import det


def test_live_defaults_have_every_experiment_off() -> None:
    c = setups.SetupConfig()
    assert not (c.exp_family_location or c.exp_major_targets or c.exp_15m_internal_fallback or c.exp_native_levels)
    assert cs.VARIANTS["live"] == {}


def test_h1_location_only_relaxed_for_continuation_and_breakout() -> None:
    d = scenario()
    d["context"]["pd15"] = {"zone": "premium", "position": 0.8}  # adverse for a long
    h1 = setups.SetupConfig(costs=CFG.costs, exp_family_location=True)
    assert setups.factors(d, "bullish", CFG, "continuation")[0]["status"] == "FAIL"  # live
    assert setups.factors(d, "bullish", h1, "continuation")[0]["status"] == "PASS"
    assert setups.factors(d, "bullish", h1, "breakout")[0]["status"] == "PASS"
    assert setups.factors(d, "bullish", h1, "reversal")[0]["status"] == "FAIL"  # still required
    assert setups.factors(d, "bullish", h1, "smc")[0]["status"] == "FAIL"


def test_h2_major_targets_skip_minor_levels_for_continuation_only() -> None:
    d = scenario()
    d["levels"][1]["label"] = "Equal highs (5m)"  # minor level at 21040
    d["levels"].append(
        {
            "type": "liquidity",
            "label": "Previous day high",
            "price": 21080.0,
            "side": "buy-side",
            "state": "untouched",
            "valid": True,
            "timeframe": "1d",
        }
    )
    h2 = setups.SetupConfig(costs=CFG.costs, exp_major_targets=True)
    live = setups.evaluate(d, signal("vwap_trend"), None, CFG)["levels"]
    exp = setups.evaluate(d, signal("vwap_trend"), None, h2)["levels"]
    smc = setups.evaluate(d, signal("smc_sweep_mss"), None, h2)["levels"]
    # The minor 5m EQH and the 15m INTERNAL pivot (21060) are skipped; the next major level is the PDH.
    assert live["tp1"] == 21039.75 and exp["tp1"] == 21079.75
    assert exp["tp1_basis"] == "Previous day high" and smc["tp1"] == 21039.75  # SMC family unchanged


def test_h3_uses_15m_internal_when_swing_unconfirmed() -> None:
    d = scenario()
    d["context"].update(mid="neutral", htf="neutral", bias_dir="neutral", mid_internal="bullish")
    h3 = setups.SetupConfig(costs=CFG.costs, exp_15m_internal_fallback=True)
    assert setups.factors(d, "bullish", CFG)[0]["status"] == "FAIL"
    assert setups.factors(d, "bullish", h3)[0]["status"] == "PASS"
    d2 = copy.deepcopy(d)
    d2["context"]["htf"] = "bearish"  # 1h opposite: conflict -> still neutral
    assert setups.factors(d2, "bullish", h3)[0]["status"] == "FAIL"


def test_h4_native_exits_use_the_strategy_levels() -> None:
    h4 = setups.SetupConfig(costs=CFG.costs, exp_native_levels=True)
    lv = setups.evaluate(scenario(), signal(stop=20990.0, targets=[21030.0, 21050.0]), None, h4)["levels"]
    # Native stop, exit at the strategy's SECOND target (T1 = 1.0-1.5R can never clear 1.5 net after costs).
    assert (lv["stop"], lv["tp1"], lv["tp2"]) == (20990.0, 21050.0, None) and lv["stop_basis"].startswith("strategy")
    # Same cost model: NQ 50 pts reward = $1000 - $10; 10 pts risk = $200 + $15.
    assert lv["rr_tp1"]["net"] == round(990 / 215, 2)


def _seq(rows: list[tuple[int, float]]) -> list[dict[str, Any]]:
    out = []
    for i, (day, usd) in enumerate(rows):
        t = datetime(2026, 3, day, 15, 0, tzinfo=UTC) + timedelta(minutes=i)
        out.append(
            {
                **det("orb", usd=usd),
                "confirmed_at": t,
                "outcome_at": t + timedelta(minutes=5),
                "filled_at": t,
                "expires_at": t + timedelta(minutes=30),
            }
        )
    return out


def test_survival_stops_at_the_first_trailing_breach_and_never_restarts() -> None:
    # Day 2 ends +1500 (EOD peak); day 3 drops 2000 below that peak -> terminated; day 4 is never traded.
    rows = _seq([(2, 1000), (2, 500), (3, -900), (3, -950), (4, -700), (4, 3000)])
    sv = cs.account_survival(rows, daily_loss=1000, max_dd=2000)
    # Day 3 ends at -350 (threshold 1500 - 2000 = -500 not reached); day 4's -700 takes equity to -1050.
    assert sv["terminated"] and sv["trades_after_termination"] == 1 and sv["net_before_end_usd"] == -1050.0
    rows2 = _seq([(2, 1500), (3, -900), (3, -500), (5, -700), (6, 3000)])
    sv2 = cs.account_survival(rows2, daily_loss=1000, max_dd=2000)
    assert sv2["terminated"] and sv2["trades_after_termination"] == 1 and sv2["net_before_end_usd"] == -600.0


def test_daily_loss_limit_pauses_the_rest_of_the_day() -> None:
    rows = _seq([(2, -600), (2, -500), (2, 800), (3, 200)])
    sv = cs.account_survival(rows, daily_loss=1000, max_dd=5000)
    assert sv["trades_skipped_by_daily_limit"] == 1 and sv["trades_taken"] == 3 and not sv["terminated"]


def test_winner_dependence_and_cost_sensitivity() -> None:
    rows = _seq([(2, 3000), (3, -500), (4, -500), (5, 400)])
    wd = cs.winner_dependence(rows)
    assert wd["top1_share_of_gross_profit"] == round(3000 / 3400, 3)
    assert wd["mean_without_largest_win"] == round((-500 - 500 + 400) / 3, 2)
    cost = cs.cost_sensitivity(rows, "NQ")  # NQ tick $5, commission $2.50 per side
    assert cost["plus_1_tick_slippage_each_side"] == round(2400 / 4 - 10, 2)
    assert cost["double_commission"] == round(2400 / 4 - 5, 2)


def test_combine_is_one_position_across_instruments() -> None:
    def exported(sym: str, minutes: int, usd: float) -> dict[str, Any]:
        x = det("orb", minutes=minutes, usd=usd)
        x["symbol"] = sym
        return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in x.items()}

    es = {
        "symbol": "ES",
        "risk_limits": {"per_trade": 1000, "daily_loss": 1000, "max_dd": 2000},
        "events_export": {"live": [exported("ES", 0, 500.0)]},
    }
    nq = {"symbol": "NQ", "events_export": {"live": [exported("NQ", 10, 800.0), exported("NQ", 300, -200.0)]}}
    out = cs.combine([es, nq])
    assert out["opportunities"] == 3 and out["taken_one_at_a_time"] == 2  # NQ at +10 min overlaps the ES trade
    assert out["tiers"]["3/5"]["resolved"] == 2
