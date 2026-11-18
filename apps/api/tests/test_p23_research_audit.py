"""Research audit: Risk Guardian consistency, actual contract maps, audit rules and decisions."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal

from app.research import rollover
from app.research.audit import audit_study, breakeven_win_rate, concentration
from app.research.backtest import BacktestParams, execute, generate_signals
from app.research.study import run_full_study
from app.risk.engine import RuleSet
from tests.test_p22_research_engine import _series, _sim_bars

RULES = RuleSet("test", Decimal(50000), Decimal(3000), Decimal(2000), Decimal(1000), Decimal(6))


async def test_trades_the_risk_guardian_would_size_at_zero_are_excluded() -> None:
    """Regression: the study took every signal, including ones whose 1-contract loss at the stop exceeded the
    account's risk budget - the live Risk Guardian sizes those at 0 contracts."""
    b = await _sim_bars(20)
    p = BacktestParams(strategy_id="breakout_retest", symbol="ES")
    rp, ev = generate_signals(p, b)
    every = execute(p, rp, ev)
    st: Counter[str] = Counter()
    capped = execute(BacktestParams(strategy_id="breakout_retest", symbol="ES", max_risk_usd=300.0), rp, ev, st)
    assert every and len(capped) < len(every)
    assert all(t.risk_usd + t.commission + 2 * 12.5 <= 300.0 + 1e-6 for t in capped)
    assert any("Risk Guardian would size 0" in k for k in st)
    res = run_full_study("ES", b, "historical", RULES, 2.5, 1.0, strategies=["breakout_retest"])
    assert res["risk_filter"]["max_1_contract_loss_usd"] == 1000.0
    assert all(t["risk_usd"] + t["commission"] + 25 <= 1000 + 1e-6 for t in res["trade_log"])


def test_actual_contract_map_drives_segments_and_is_compared_to_the_calendar() -> None:
    b = _series(datetime(2026, 9, 3, 22, 0, tzinfo=UTC), datetime(2026, 9, 25, 21, 0, tzinfo=UTC))
    days = sorted({rollover.trading_day(x.ts) for x in b})
    actual = {d.isoformat(): ("ESU6" if d < date(2026, 9, 16) else "ESZ6") for d in days}  # switched 4 sessions late
    segs, excl = rollover.segments("ES", b, actual)
    assert [c for c, _ in segs] == ["ESU6", "ESZ6"]
    assert "2026-09-16" in excl and "actual switch on 2026-09-16" in excl["2026-09-16"]
    assert "2026-09-10" not in excl  # the calendar window is not used when the real map is given
    cmp = rollover.compare_to_calendar("ES", actual)
    assert cmp[0]["days_from_calendar"] == 6 and cmp[0]["inside_calendar_window"] is False


def test_audit_helpers() -> None:
    c = concentration([500, 400, 300, 200, 100, -50, -60, -70])
    assert c["net"] == 1320 and c["net_without_top5_wins"] == -180 and c["depends_on_few_trades"] is True
    assert breakeven_win_rate(100.0, -150.0) == 0.6 and breakeven_win_rate(None, -1.0) is None


def _stats(trades: int, wr: float, exp: float, ci: list[float], lb: float) -> dict:
    return {
        "trades": trades,
        "win_rate": wr,
        "win_rate_ci95": [lb, wr + 0.1],
        "win_rate_lower_bound": lb,
        "expectancy": exp,
        "expectancy_ci": ci,
        "profit_factor": 1.2,
        "avg_win": 300.0,
        "avg_loss": -250.0,
        "max_drawdown": 1000.0,
    }


def _row(sid: str, wf: dict, ho: dict, evidence: str = "Promising but unvalidated") -> dict:
    full = {"all": wf}
    return {
        "strategy_id": sid,
        "config": "t1_all",
        "evidence": evidence,
        "unmet_requirements": ["x"],
        "research": {"all": wf},
        "walk_forward": full,
        "holdout": {"all": ho},
        "all_periods": full,
        "walk_forward_expectancy_ci_adjusted": [-50, 90],
        "stress": {"slip2": {"label": "2 ticks", "expectancy": -3.0, "win_rate": 0.5}},
        "walk_forward_blocks": [],
        "blocks_positive_pct": 0.5,
        "apex_replay": {
            "resolved": 3,
            "passes": 1,
            "pass_rate": 0.33,
            "pass_rate_ci95": [0.06, 0.79],
            "rules": {"label": "VERIFIED"},
        },
        "funnel": {},
    }


def test_audit_explains_headlines_and_flags_untrustworthy_candidates() -> None:
    a = _row("orb", _stats(103, 0.524, 32.14, [-40.0, 105.0], 0.428), _stats(40, 0.45, -12.0, [-80, 50], 0.3))
    b = _row("vwap_trend", _stats(12, 0.75, 400.0, [100.0, 700.0], 0.47), _stats(5, 0.6, 100, [0, 1], 0.2))
    res = {
        "symbol": "ES",
        "data_class": "HISTORICAL",
        "rows": [a, b],
        "risk_filter": {"max_1_contract_loss_usd": 1000.0},
        "rankings": {
            "win_rate_all": [{"strategy_id": "orb", "config": "t1_all", "small_sample": False}],
            "risk_adjusted_all": [{"strategy_id": "orb", "config": "t1_all", "small_sample": False}],
        },
    }
    trades = [
        {
            "strategy_id": "orb",
            "config": "t1_all",
            "period": "walk_forward",
            "trading_day": f"2026-0{m}-10",
            "pnl": p,
            "risk_usd": 300.0,
            "commission": 5.0,
            "slippage_usd": 25.0,
            "exit_reason": "stop",
            "quantity": 1,
            "direction": "long",
            "contract": "ESZ6",
            "entry_ts": "",
            "entry": 1,
            "stop": 1,
            "target": 2,
            "exit": 1,
            "mae_usd": 0,
            "stop_ticks": 20,
        }
        for m, p in ((1, 900), (2, -330), (3, -330), (4, -1500))
    ]
    out = audit_study(res, trades)
    h = out["headlines"]
    assert h["consistent"] and h["recomputed_expectancy_leader"] == "orb/t1_all"
    assert any(
        "only 12 walk-forward trades" in r for x in h["higher_raw_expectancy_not_selected"] for r in x["reasons"]
    )
    cand = out["candidates"][0]
    assert not cand["trustworthy"]
    joined = " ".join(cand["flags"])
    assert "holdout expectancy" in joined and "2 ticks" in joined and "includes zero" in joined
    assert cand["apex"]["informative"] is False
    assert out["outliers"] and out["outliers"][0]["loss_vs_planned_risk"] == 5.0  # -1500 vs $300 planned risk
    assert out["decision"]["forward_paper_trading"] == []
