"""Reward:risk configuration study: the same strategies and costs under alternative exit/gating rules.

Each configuration runs the full backtest (conservative fills, in-sample / out-of-sample split by
trading day, walk-forward folds, validation standards). Configurations are compared on their
OUT-OF-SAMPLE results only, and a configuration is reported as supported only when it passes the
validation standards on non-simulated data. Testing several configurations is itself a multiple
comparison; the count is passed to the validator so its Bonferroni correction applies.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.research.backtest import BacktestParams, run_backtest
from app.research.bars import Bar
from app.strategies.library import STRATEGY_BY_ID


@dataclass(frozen=True)
class RRConfig:
    key: str
    label: str
    exit_target: int
    min_net_rr: float | None


CONFIGS: tuple[RRConfig, ...] = (
    RRConfig("t1_all", "Exit at T1, every signal (as designed)", 1, None),
    RRConfig("t1_net12", "Exit at T1, only if net R:R to T1 >= 1.2", 1, 1.2),
    RRConfig("t1_net15", "Exit at T1, only if net R:R to T1 >= 1.5", 1, 1.5),
    RRConfig("t2_all", "Exit at T2, every signal", 2, None),
    RRConfig("t2_net15", "Exit at T2, only if net R:R to T2 >= 1.5", 2, 1.5),
)
MIN_OOS_TRADES = 30


def _summary(res: dict[str, Any]) -> dict[str, Any]:
    oos = res.get("out_of_sample") or {}
    ins = res.get("in_sample") or {}
    keep = ("trades", "win_rate", "expectancy", "expectancy_r", "profit_factor", "net_profit", "max_drawdown")
    return {
        "in_sample": {k: ins.get(k) for k in keep},
        "out_of_sample": {k: oos.get(k) for k in keep},
        "validation_passed": bool((res.get("validation") or {}).get("passed")),
        "failed_checks": [c["name"] for c in (res.get("validation") or {}).get("checks", []) if not c["passed"]],
    }


def verdict(row: dict[str, Any], simulated: bool) -> str:
    oos = row["out_of_sample"]
    n = oos.get("trades") or 0
    if simulated:
        return "NOT EVIDENCE: simulated data"
    if n < MIN_OOS_TRADES:
        return f"INSUFFICIENT: {n} out-of-sample trades (need {MIN_OOS_TRADES})"
    if row["validation_passed"]:
        return "SUPPORTED: passed the validation standards out of sample"
    exp = oos.get("expectancy")
    return f"NOT SUPPORTED: out-of-sample expectancy {exp}" + (
        f"; failed {', '.join(row['failed_checks'][:3])}" if row["failed_checks"] else ""
    )


def run_study(
    bars: Sequence[Bar],
    symbol: str,
    quality: str,
    strategies: Sequence[str],
    commission_per_side: float,
    slippage_ticks: float,
    configs: Sequence[RRConfig] = CONFIGS,
) -> dict[str, Any]:
    simulated = quality == "simulated"
    rows: list[dict[str, Any]] = []
    for sid in strategies:
        strat = STRATEGY_BY_ID[sid]
        if symbol not in strat.spec.eligible_instruments:
            continue
        for cfg in configs:
            p = BacktestParams(
                strategy_id=sid,
                symbol=symbol,
                commission_per_side=commission_per_side,
                slippage_ticks=slippage_ticks,
                exit_target=cfg.exit_target,
                min_net_rr=cfg.min_net_rr,
                monte_carlo_runs=200,
            )
            try:
                res = run_backtest(p, bars, quality, prior_configs=len(configs) - 1, sensitivity=False)
            except ValueError as exc:
                rows.append({"strategy_id": sid, "config": cfg.key, "error": str(exc)})
                continue
            row = {
                "strategy_id": sid,
                "config": cfg.key,
                "label": cfg.label,
                "design_t1_r": strat.target_r[0],
                **_summary(res),
            }
            row["verdict"] = verdict(row, simulated)
            rows.append(row)
    supported = [r for r in rows if r.get("verdict", "").startswith("SUPPORTED")]
    best = max(supported, key=lambda r: r["out_of_sample"].get("expectancy") or 0, default=None)
    return {
        "symbol": symbol,
        "data_class": quality.upper(),
        "bars": len(bars),
        "costs": {"commission_per_side": commission_per_side, "slippage_ticks_per_side": slippage_ticks},
        "configs": [cfg.__dict__ for cfg in configs],
        "rows": rows,
        "best_supported": best,
        "conclusion": (
            f"{best['strategy_id']} / {best['label']} has the strongest out-of-sample evidence."
            if best
            else "No configuration has sufficient out-of-sample evidence. Keep the conservative default and "
            "collect more history before relying on any of them."
        ),
        "limitations": [
            "Results describe the tested period only; they are not a forecast.",
            "Fills are modelled (1-minute bars, conservative stop-first rule); real queue position, spread "
            "and latency are not.",
            "Several configurations were compared; validation applies a Bonferroni correction for that.",
            "Continuous front-month bars; roll days are not modelled separately.",
        ],
    }
