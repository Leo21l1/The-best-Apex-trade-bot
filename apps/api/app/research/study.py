"""Full research study: every eligible strategy x pre-registered configuration on one instrument.

Pipeline (deterministic, reproducible from the stored bars):
1. Data audit and roll handling (``rollover.segments``): roll windows excluded, history split into
   per-contract segments, only usable regular-hours sessions evaluated.
2. Chronological periods by usable session: RESEARCH (first 100), WALK-FORWARD blocks of 25,
   final HOLDOUT (last 50). With fewer sessions the same proportions are scaled down and the study
   is labelled PRELIMINARY. Strategy rules and configurations are fixed in advance: nothing is
   fitted, and the holdout is only used to confirm, never to choose.
3. Signals are generated once per strategy and segment; every configuration and stress scenario
   re-executes the same signals (``backtest.generate_signals`` / ``backtest.execute``).
4. Statistics per period and direction with uncertainty (``stats``), stress scenarios, Apex-style
   evaluation replay on out-of-sample trades (``apex_replay``), and an evidence classification.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any

from app.research.apex_replay import replay
from app.research.backtest import BacktestParams, Trade, execute, generate_signals, json_safe
from app.research.bars import Bar
from app.research.data_audit import audit_bars
from app.research.rollover import detect_discontinuities, segments
from app.research.rr_study import CONFIGS, RRConfig
from app.research.stats import bootstrap_mean_ci, breakdown, trade_stats
from app.research.validation import ValidationStandards
from app.risk.engine import RuleSet
from app.strategies.library import STRATEGY_BY_ID

RESEARCH_SESSIONS, BLOCK_SESSIONS, HOLDOUT_SESSIONS, FULL_SESSIONS = 100, 25, 50, 250
DIRECTIONS = ("all", "long", "short")
EVIDENCE = (
    "Insufficient evidence",
    "Negative evidence",
    "Promising but unvalidated",
    "Validated for paper-trading consideration",
)


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    changes: dict[str, float]


def scenarios(commission: float, slippage: float) -> tuple[Scenario, ...]:
    return (
        Scenario("base", f"${commission:g}/side commission, {slippage:g} tick slippage", {}),
        Scenario("slip2", "2 ticks adverse slippage per applicable fill", {"slippage_ticks": 2.0}),
        Scenario(
            "costs_high",
            "commission x1.5 and 2 ticks slippage",
            {"commission_per_side": commission * 1.5, "slippage_ticks": 2.0},
        ),
        Scenario("stop_slip", "+1 extra tick on stop fills", {"stop_slippage_extra_ticks": 1.0}),
        Scenario("late_entry", "entry one 1-minute bar later", {"entry_delay_bars": 1}),
    )


def plan_periods(sessions: Sequence[str]) -> dict[str, Any]:
    n = len(sessions)
    if n >= FULL_SESSIONS:
        r, h, blk = RESEARCH_SESSIONS, HOLDOUT_SESSIONS, BLOCK_SESSIONS
    else:
        r, h = round(n * RESEARCH_SESSIONS / FULL_SESSIONS), round(n * HOLDOUT_SESSIONS / FULL_SESSIONS)
        blk = max(5, round(n * BLOCK_SESSIONS / FULL_SESSIONS))
    wf = list(sessions[r : n - h]) if n - h > r else []
    blocks = [wf[i : i + blk] for i in range(0, len(wf), blk)]
    return {
        "sessions": n,
        "research": list(sessions[:r]),
        "walk_forward_blocks": blocks,
        "holdout": list(sessions[n - h :]) if h else [],
        "preliminary": n < FULL_SESSIONS,
        "rule": f"chronological: research {r}, walk-forward blocks of {blk}, final holdout {h} sessions"
        + (f" (scaled from 100/25/50 because only {n} usable sessions; PRELIMINARY)" if n < FULL_SESSIONS else ""),
    }


def _period_of(plan: dict[str, Any]) -> dict[str, str]:
    m = {d: "research" for d in plan["research"]}
    for k, blk in enumerate(plan["walk_forward_blocks"], 1):
        for d in blk:
            m[d] = f"wf{k}"
    for d in plan["holdout"]:
        m[d] = "holdout"
    return m


def _strategy_job(args: tuple[Any, ...]) -> dict[str, Any]:
    """One strategy on one instrument: all configurations and scenarios. Runs in a worker process."""
    symbol, sid, segs, configs, scen, commission, slippage, period_of, max_risk = args
    gen_stats: Counter[str] = Counter()
    gen = []
    base = BacktestParams(
        strategy_id=sid, symbol=symbol, commission_per_side=commission, slippage_ticks=slippage, max_risk_usd=max_risk
    )
    for contract, bars in segs:
        rp, events = generate_signals(base, bars, gen_stats)
        gen.append((contract, rp, events))
    out: dict[str, Any] = {"strategy_id": sid, "signals": dict(gen_stats), "configs": {}}
    for cfg in configs:
        out["configs"][cfg.key] = {}
        for sc in scen:
            p = replace(base, exit_target=cfg.exit_target, min_net_rr=cfg.min_net_rr, **sc.changes)
            ex_stats: Counter[str] = Counter()
            trades: list[Trade] = []
            for contract, rp, events in gen:
                trades += execute(p, rp, events, ex_stats, contract)
            kept = [t for t in trades if t.trading_day in period_of]
            ex_stats["trades on excluded or incomplete sessions"] = len(trades) - len(kept)
            out["configs"][cfg.key][sc.key] = {"trades": [asdict(t) for t in kept], "funnel": dict(ex_stats)}
    return out


def _as_trades(rows: Sequence[dict[str, Any]]) -> list[Trade]:
    return [Trade(**r) for r in rows]


def _classify(
    row: dict[str, Any],
    comparisons: int,
    data_ok: bool,
    preliminary: bool,
    simulated: bool,
    standards: ValidationStandards,
) -> tuple[str, list[str]]:
    wf, ho, total = row["walk_forward"]["all"], row["holdout"]["all"], row["all_periods"]["all"]
    unmet: list[str] = []
    if simulated:
        return "Insufficient evidence", ["simulated data can never validate a strategy"]
    if (wf["trades"] or 0) < standards.min_out_of_sample_trades:
        return "Insufficient evidence", [
            f"{wf['trades']} walk-forward trades (need {standards.min_out_of_sample_trades})"
        ]
    if (wf["expectancy"] or 0) <= 0:
        return "Negative evidence", [f"walk-forward expectancy ${wf['expectancy']} per trade"]
    ci = row.get("walk_forward_expectancy_ci_adjusted")
    checks = [
        (total["trades"] >= standards.min_trades, f">= {standards.min_trades} trades in total ({total['trades']})"),
        (
            ci is not None and ci[0] > 0,
            f"walk-forward expectancy interval above 0 at the multiple-comparison "
            f"adjusted level ({comparisons} comparisons): {ci}",
        ),
        ((wf["profit_factor"] or 0) >= standards.min_profit_factor, f"profit factor >= {standards.min_profit_factor}"),
        ((wf["avg_r"] or 0) >= standards.min_expectancy_r, f"average R >= {standards.min_expectancy_r}"),
        (
            row["blocks_positive_pct"] is not None and row["blocks_positive_pct"] >= 0.6,
            ">= 60% of walk-forward blocks positive",
        ),
        ((row["stress"].get("slip2", {}).get("expectancy") or 0) > 0, "positive with 2 ticks slippage"),
        ((ho["trades"] or 0) >= 10 and (ho["expectancy"] or 0) > 0, "holdout: >= 10 trades and positive"),
        (data_ok, "data verdict SUFFICIENT"),
        (not preliminary, "full 250-session plan (not preliminary)"),
    ]
    unmet = [name for ok, name in checks if not ok]
    return (EVIDENCE[3], []) if not unmet else (EVIDENCE[2], unmet)


def run_full_study(
    symbol: str,
    bars: Sequence[Bar],
    quality: str,
    rules: RuleSet,
    commission: float,
    slippage: float,
    strategies: Sequence[str] | None = None,
    configs: Sequence[RRConfig] = CONFIGS,
    workers: int = 1,
    progress: Callable[[str], None] | None = None,
    requested: tuple[datetime, datetime] | None = None,
    provenance: dict[str, int] | None = None,
    enforce_risk_budget: bool = True,
    rules_provenance: list[dict[str, Any]] | None = None,
    contract_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    say = progress or (lambda _m: None)
    standards = ValidationStandards()
    sims = quality == "simulated"
    start, end = requested or ((bars[0].ts, bars[-1].ts) if bars else (datetime.now(UTC), datetime.now(UTC)))
    audit = audit_bars(symbol, bars, start, end, provenance)
    if contract_map is not None:
        from app.research.contract_map import require_coverage

        # Every usable session needs its actual contract; nothing is filled from the calendar.
        require_coverage(symbol, contract_map, audit.get("usable_session_dates", []))
    segs, excluded = segments(symbol, bars, contract_map)
    usable = [d for d in audit.get("usable_session_dates", []) if d not in excluded]
    plan = plan_periods(usable)
    period_of = _period_of(plan)
    strategies = [
        s for s in (strategies or list(STRATEGY_BY_ID)) if symbol in STRATEGY_BY_ID[s].spec.eligible_instruments
    ]
    scen = scenarios(commission, slippage)
    say(
        f"{symbol}: {len(bars)} bars, {len(usable)} usable sessions after roll exclusion, "
        f"{len(segs)} contract segment(s); {len(strategies)} strategies x {len(configs)} configs x "
        f"{len(scen)} scenarios"
    )
    # Risk Guardian consistency: at full account health one contract may lose at most the smaller of the
    # daily loss limit and the drawdown allowance; signals above that would be sized at 0 live.
    budget = [float(x) for x in (rules.daily_loss_limit, rules.max_drawdown) if x]
    max_risk = min(budget) if (enforce_risk_budget and budget) else None
    jobs = [(symbol, sid, segs, list(configs), scen, commission, slippage, period_of, max_risk) for sid in strategies]
    results: list[dict[str, Any]] = []
    if workers > 1 and len(jobs) > 1:
        import multiprocessing as mp

        with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as ex:
            for r in ex.map(_strategy_job, jobs):
                say(f"  {symbol} {r['strategy_id']}: done")
                results.append(r)
    else:
        for j in jobs:
            say(f"  {symbol} {j[1]}: running")
            results.append(_strategy_job(j))
    comparisons = max(1, len(strategies) * len(configs))
    level = 1 - 0.05 / comparisons
    wf_days = [d for b in plan["walk_forward_blocks"] for d in b]
    oos_days = wf_days + plan["holdout"]
    rows: list[dict[str, Any]] = []
    trade_log: list[dict[str, Any]] = []
    for r in results:
        sid = r["strategy_id"]
        for cfg in configs:
            per = r["configs"][cfg.key]
            base_rows = per["base"]["trades"]
            for t in base_rows:
                trade_log.append({**t, "config": cfg.key, "period": _period_label(period_of.get(t["trading_day"]))})
            trades = _as_trades(base_rows)
            row: dict[str, Any] = {
                "strategy_id": sid,
                "config": cfg.key,
                "config_label": cfg.label,
                "funnel": {**r["signals"], **per["base"]["funnel"]},
            }
            groups = {
                "research": (set(plan["research"]), len(plan["research"])),
                "walk_forward": (set(wf_days), len(wf_days)),
                "holdout": (set(plan["holdout"]), len(plan["holdout"])),
                "all_periods": (set(period_of), len(period_of)),
            }
            for gname, (days, n_sessions) in groups.items():
                row[gname] = {}
                for d in DIRECTIONS:
                    ts = [t for t in trades if t.trading_day in days and (d == "all" or t.direction == d)]
                    st = trade_stats(ts, n_sessions)
                    if gname != "walk_forward" or d != "all":
                        st.pop("equity_curve", None)
                    row[gname][d] = st
            wf_tr = [t for t in trades if t.trading_day in set(wf_days)]
            row["walk_forward_expectancy_ci_adjusted"] = bootstrap_mean_ci([t.pnl for t in wf_tr], level)
            row["walk_forward_ci_level"] = round(level, 5)
            blocks = []
            for k, blk in enumerate(plan["walk_forward_blocks"], 1):
                bt = [t for t in trades if t.trading_day in set(blk)]
                blocks.append(
                    {
                        "block": k,
                        "first": blk[0],
                        "last": blk[-1],
                        "trades": len(bt),
                        "net": round(sum(t.pnl for t in bt), 2),
                    }
                )
            row["walk_forward_blocks"] = blocks
            traded = [b for b in blocks if b["trades"]]
            row["blocks_positive_pct"] = (
                round(sum(1 for b in traded if b["net"] > 0) / len(traded), 3) if traded else None
            )
            row["by_regime"] = breakdown(wf_tr, "regime")
            row["by_session"] = breakdown(wf_tr, "session")
            row["by_hour_et"] = breakdown(wf_tr, "hour_et")
            row["stress"] = {}
            for sc in scen:
                st_tr = [t for t in _as_trades(per[sc.key]["trades"]) if t.trading_day in set(wf_days)]
                s2 = trade_stats(st_tr, len(wf_days))
                row["stress"][sc.key] = {
                    "label": sc.label,
                    **{
                        k: s2[k]
                        for k in ("trades", "win_rate", "expectancy", "profit_factor", "net_pnl", "max_drawdown")
                    },
                }
            row["apex_replay"] = replay([t for t in trades if t.trading_day in set(oos_days)], rules, oos_days)
            ev, unmet = _classify(
                row, comparisons, audit.get("verdict") == "SUFFICIENT", plan["preliminary"], sims, standards
            )
            row["evidence"], row["unmet_requirements"] = ev, unmet
            rows.append(row)
    rankings = _rankings(rows, standards)
    return json_safe(
        {
            "symbol": symbol,
            "data_class": quality.upper(),
            "generated_at": datetime.now(UTC).isoformat(),
            "data": audit,
            "rollover": {
                "contract_source": "Databento symbology (actual)" if contract_map else "CME roll calendar (inferred)",
                "excluded_sessions": excluded,
                "segments": [
                    {"contract": c, "first": b[0].ts.isoformat(), "last": b[-1].ts.isoformat(), "bars": len(b)}
                    for c, b in segs
                ],
                "discontinuities": detect_discontinuities(symbol, bars),
            },
            "plan": {k: v for k, v in plan.items() if k != "walk_forward_blocks"}
            | {"walk_forward_blocks": [[b[0], b[-1], len(b)] for b in plan["walk_forward_blocks"]]},
            "costs": {
                "commission_per_side": commission,
                "slippage_ticks_per_side": slippage,
                "scenarios": [{"key": s.key, "label": s.label} for s in scen],
            },
            "comparisons": comparisons,
            "risk_filter": {
                "max_1_contract_loss_usd": max_risk,
                "rule": "signals whose 1-contract loss at the stop (incl. round-trip costs) exceeds the smaller of the "
                "daily loss limit and the drawdown allowance are skipped: the live Risk Guardian would size them at 0",
            },
            "rules_verification": rules_provenance or [],
            "standards": asdict(standards),
            "rows": rows,
            "rankings": rankings,
            "trade_log": trade_log,
            "definitions": {
                "win": "net P&L > 0 after commission and slippage; loss < 0; breakeven = 0; "
                "win rate = wins / all trades",
                "win_rate_ci": "Wilson 95% interval; rankings use its lower bound",
                "expectancy": "average net P&L per trade, USD, 1 contract",
                "periods": plan["rule"],
                "selection": "rankings and classifications use walk-forward results; the holdout only confirms",
            },
        }
    )


def _period_label(p: str | None) -> str:
    return "walk_forward" if p and p.startswith("wf") else (p or "excluded")


def _rankings(rows: Sequence[dict[str, Any]], standards: ValidationStandards) -> dict[str, Any]:
    def cand(direction: str, period: str = "walk_forward") -> list[dict[str, Any]]:
        out = []
        for r in rows:
            s = r[period][direction]
            if not s["trades"]:
                continue
            out.append(
                {
                    "strategy_id": r["strategy_id"],
                    "config": r["config"],
                    "direction": direction,
                    "trades": s["trades"],
                    "win_rate": s["win_rate"],
                    "win_rate_lower_bound": s["win_rate_lower_bound"],
                    "win_rate_ci95": s["win_rate_ci95"],
                    "expectancy": s["expectancy"],
                    "expectancy_ci": s["expectancy_ci"],
                    "profit_factor": s["profit_factor"],
                    "max_drawdown": s["max_drawdown"],
                    "sharpe_daily": s["sharpe_daily"],
                    "small_sample": s["trades"] < standards.min_out_of_sample_trades,
                    "evidence": r["evidence"],
                }
            )
        return out

    res: dict[str, Any] = {}
    for d in DIRECTIONS:
        c = cand(d)
        res[f"win_rate_{d}"] = sorted(c, key=lambda x: (-(x["win_rate_lower_bound"] or 0), -(x["trades"])))
        res[f"risk_adjusted_{d}"] = sorted(
            c, key=lambda x: (-((x["expectancy_ci"] or [-math.inf])[0]), -(x["expectancy"] or 0))
        )
        res[f"in_sample_win_rate_{d}"] = sorted(
            cand(d, "research"), key=lambda x: (-(x["win_rate_lower_bound"] or 0), -(x["trades"]))
        )
    res["note"] = (
        "Out-of-sample = walk-forward blocks. Win-rate ranking uses the Wilson lower bound; rows "
        "with fewer than 30 trades carry small_sample=true. Risk-adjusted ranking uses the lower "
        "end of the bootstrap expectancy interval. In-sample ranking is listed separately."
    )
    return res
