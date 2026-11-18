"""Confluence-study correctness audit: rejection breakdown, gaps, double counting, costs, R vs $, risk."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

from app.research import confluence_study as cs
from app.research.bars import Bar
from app.services import setups, signal_tracker
from app.services.checklist import ChecklistConfig, reward_risk
from tests.test_p29_setups import BASE, CFG, NOW, iso, mb, scenario, signal

COSTS = ChecklistConfig(commission_per_side={"standard": 2.5, "micro": 0.6}, slippage_ticks_per_side=1)


def test_gap_through_stop_books_the_open_not_the_stop() -> None:
    # Filled at 21000; next bar opens at 20980, 14 points below the 20994 stop.
    out = signal_tracker.simulate(BASE, [mb(1, 21004, 21005, 20999, 21003), mb(2, 20980, 20985, 20975, 20982)])
    assert out["outcome"] == "stop"
    assert out["outcome_usd"] == ((20980 - 21000) * 20 - 15)  # -415, worse than the planned -135
    assert out["outcome_r"] == round(-415 / 135, 3)
    normal = signal_tracker.simulate(BASE, [mb(1, 21004, 21005, 20999, 21003), mb(2, 20999, 21000, 20990, 20995)])
    assert normal["outcome_usd"] == -135.0 and normal["outcome_r"] == -1.0


def test_acceptance_at_the_broken_structure_level_is_counted_once() -> None:
    d = scenario()
    d["levels"][0]["state"] = "untouched"  # no sweep
    # Acceptance above the very level the 5m CHoCH broke (20995), at the same time: one event.
    d["levels"].append(
        {
            "type": "liquidity",
            "label": "Equal highs (5m)",
            "price": 20995.0,
            "side": "buy-side",
            "state": "accepted beyond",
            "state_at": iso(10),
            "valid": False,
            "timeframe": "5m",
        }
    )
    f = setups.factors(d, "bullish", CFG)
    assert f[1]["status"] == "PASS" and f[2]["status"] == "FAIL" and "same close" in f[2]["detail"]
    # A different level accepted earlier is a separate event and still counts.
    d2 = copy.deepcopy(d)
    d2["levels"][-1].update(price=20960.0, state_at=iso(40))
    assert setups.factors(d2, "bullish", CFG)[2]["status"] == "PASS"


def test_rejection_reasons_are_broken_down() -> None:
    def cand(**lv: object) -> dict:
        ev = setups.evaluate(scenario(), signal(), None, CFG)
        ev["levels"].update(lv)
        return ev

    doc = {"data": {"available": True}}
    assert cs.rejection(None, None)[0] == "other calculation failures"
    assert cs.rejection(cand(), {"data": {"available": False}})[0] == "missing data"
    assert cs.rejection(cand(stop=None), doc)[0] == "no valid structural stop"
    assert cs.rejection(cand(tp1=None), doc)[0] == "no valid target"
    both = cs.rejection(cand(stop=None, tp1=None), doc)
    assert both == ("no valid structural stop", ["no valid structural stop", "no valid target"])
    rr = setups.evaluate((lambda d: d["levels"][1].update(price=21004.0) or d)(scenario()), signal(), None, CFG)
    assert cs.rejection(rr, doc)[0] == "TP1 net R:R below minimum"
    zone = setups.evaluate((lambda d: d["context"].update(price=21030.0) or d)(scenario()), signal(), None, CFG)
    assert "invalid entry zone" in cs.rejection(zone, doc)[1]
    assert cs.rejection(setups.evaluate(scenario(), signal(), None, CFG), doc) == (None, [])


def test_costs_and_tick_math_for_es_nq_mnq() -> None:
    # ES long 5000 / stop 4990 / target 5020: $50 per point, commission $2.50 per side, 1 tick slippage $12.50.
    es = reward_risk("ES", "long", 5000, 4990, 5020, COSTS)
    assert (es["gross_reward"], es["gross_risk"]) == (1000.0, 500.0)
    assert es["net_reward"] == 1000 - 5 - 12.5 and es["net_risk"] == 500 + 5 + 25 and es["costs"] == 30.0
    nq = reward_risk("NQ", "short", 21000, 21010, 20980, COSTS)
    assert nq["net_reward"] == 400 - 5 - 5 and nq["net_risk"] == 200 + 5 + 10
    mnq = reward_risk("MNQ", "long", 21000, 20990, 21020, COSTS)
    assert mnq["net_reward"] == 40 - 1.2 - 0.5 and mnq["net_risk"] == 20 + 1.2 + 1.0
    # Rounding is always conservative: stops away from entry, targets toward entry, on the 0.25 grid.
    assert setups._round(20994.1, 0.25, "down") == 20994.0 and setups._round(20994.1, 0.25, "up") == 20994.25
    lv = setups.evaluate(scenario(), signal(), None, CFG)["levels"]
    assert all(abs(x / 0.25 - round(x / 0.25)) < 1e-9 for x in (lv["entry"], lv["stop"], lv["tp1"], lv["tp2"]))


def _trade(day: int, usd: float, risk: float, r: float | None = None, score: int = 3) -> dict:
    t = datetime(2026, 6, day, 15, 0, tzinfo=UTC)
    return {
        "symbol": "NQ",
        "direction": "long",
        "entry": 1.0,
        "entry_low": 1.0,
        "entry_high": 1.0,
        "stop": 0.0,
        "tp1": 2.0,
        "tp2": None,
        "confirmed_at": t,
        "outcome_at": t,
        "outcome": "tp1" if usd > 0 else "stop",
        "outcome_usd": usd,
        "outcome_r": r if r is not None else usd / risk,
        "net_risk_usd": risk,
        "status": "CONFIRMED",
        "strategy_id": "orb",
        "score": score,
    }


def test_r_vs_dollars_can_disagree_in_sign_and_the_identity_holds() -> None:
    # One wide-stop winner (+$2,400 on $1,200 risk = +2R) and three tight-stop losers (-$100 = -1R each).
    items = [_trade(1, 2400, 1200), _trade(2, -100, 100), _trade(3, -100, 100), _trade(4, -100, 100)]
    rv = cs.r_vs_dollars(items)
    assert rv["mean_usd"] == 525.0 and rv["mean_r"] == -0.25 and rv["identity_holds"]
    assert rv["avg_planned_risk_winners_usd"] == 1200 and rv["avg_planned_risk_losers_usd"] == 100
    bad = [{**items[0], "outcome_r": 5.0}, *items[1:]]
    assert not cs.r_vs_dollars(bad)["identity_holds"]  # any R/$ inconsistency is detected


def test_risk_audit_reports_violations_without_hiding_trades() -> None:
    items = [_trade(1, 2400, 1200), _trade(2, -1300, 1250), _trade(3, -900, 900), _trade(3, -300, 150)]
    ra = cs.risk_audit(items, per_trade=1000, daily_loss=1000, max_dd=2000)
    assert ra["resolved_trades"] == 4  # nothing removed
    assert ra["trades_over_planned_risk_limit"] == 2 and ra["max_planned_risk_usd"] == 1250
    assert ra["trades_losing_more_than_planned"] == 2  # -1300 on 1250 planned, -300 on 150 planned
    assert ra["closed_pnl_max_drawdown_usd"] == 2500.0
    assert ra["drawdown_limit_first_breached_at"] == datetime(2026, 6, 3, 15, 0, tzinfo=UTC).isoformat()
    assert ra["daily_loss_limit_breach_days"] == ["2026-06-02", "2026-06-03"] and ra["worst_day_usd"] == -1300


async def test_study_excludes_the_stored_holdout_even_when_sessions_were_added() -> None:
    from app.adapters.marketdata.simulated import SimulatedProvider

    c = await SimulatedProvider(clock=lambda: NOW).fetch_candles("ES", NOW - timedelta(days=8), NOW)
    bars = [Bar(x.ts, float(x.open), float(x.high), float(x.low), float(x.close), int(x.volume)) for x in c]
    sessions = cs.rth_sessions(bars)
    # The stored study's holdout began 4 sessions ago; only the last 2 are excluded by count.
    r = cs.run("ES", bars, [], holdout_sessions=2, holdout_start=sessions[-4], strategies=["vwap_trend"], cfg=CFG)
    assert r["period"]["last"] == sessions[-5] and r["period"]["excluded_holdout_sessions"] == 4
    assert set(cs.REJECTIONS) <= set(r["funnel"]) and set(r["rejections_nonexclusive"]) == set(cs.REJECTIONS)
    assert set(r["factor_pass_rates"]) == set(setups.FACTORS.values()) and set(r["risk_audit"]) == {"3/5", "4/5", "5/5"}
    accounted = sum(r["funnel"][k] for k in (*cs.REJECTIONS, "mandatory failed", "recorded"))
    assert accounted == r["funnel"]["signals"]  # every signal is accounted for exactly once
    md = cs.to_markdown(r)
    assert "Distinct opportunity counts" in md and "Every 4/5+ opportunity" in md
