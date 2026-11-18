"""Independent audit of a stored research study (read-only): is each finding trustworthy?

Checks, from the study's own statistics and complete trade ledger:
1. Headlines: recompute the declared rankings and explain why higher raw-expectancy rows lost.
2. Candidates: walk-forward vs untouched holdout vs stressed costs; monthly stability; how much of the
   net comes from a few trades; the break-even win rate implied by the average win and loss.
3. Outlier losses: losses beyond the planned stop (gap fills) and trades whose 1-contract risk exceeds
   the account risk budget (the live Risk Guardian would size them at 0).
4. Apex replay reliability: resolved attempts and interval width.
5. Rollover: contract source, excluded sessions, unexplained discontinuities.
6. Strict R:R configurations: signals rejected because the strategy's T1 design cannot reach the
   minimum net R:R (structural, not a data problem).
7. Decision summary with explicit rules (never upgrades a classification).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from app.research.stats import wilson

MIN_TRADES = 30
DEFAULT_BUDGET = 1000.0  # default profile: smaller of the $1,000 daily loss limit and the $2,000 drawdown


def _loss_1c(t: dict[str, Any]) -> float:
    """1-contract loss at the stop incl. costs (planned risk + commission + 2 slippage fills)."""
    q = t.get("quantity") or 1
    slip_per_fill = t.get("slippage_usd", 0) / (2 if t.get("exit_reason") != "target" else 1) / q
    return t.get("risk_usd", 0) / q + t.get("commission", 0) / q + 2 * slip_per_fill


def concentration(pnls: Sequence[float]) -> dict[str, Any]:
    net = sum(pnls)
    wins = sorted((x for x in pnls if x > 0), reverse=True)
    top5 = sum(wins[:5])
    return {
        "net": round(net, 2),
        "largest_win": round(wins[0], 2) if wins else None,
        "top5_wins_sum": round(top5, 2),
        "net_without_top5_wins": round(net - top5, 2),
        "top5_share_of_net": round(top5 / net, 3) if net > 0 else None,
        "depends_on_few_trades": net > 0 and net - top5 <= 0,
    }


def breakeven_win_rate(avg_win: float | None, avg_loss: float | None) -> float | None:
    if not avg_win or avg_loss is None or avg_loss >= 0:
        return None
    return round(-avg_loss / (avg_win - avg_loss), 4)


def _monthly(trades: Sequence[dict[str, Any]]) -> dict[str, dict[str, float]]:
    m: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        m[t["trading_day"][:7]].append(t["pnl"])
    return {k: {"trades": len(v), "net": round(sum(v), 2)} for k, v in sorted(m.items())}


def audit_study(res: dict[str, Any], trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows = res["rows"]
    by_key = {(r["strategy_id"], r["config"]): r for r in rows}
    tl: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        tl[(t["strategy_id"], t["config"])].append(t)
    out: dict[str, Any] = {
        "symbol": res["symbol"],
        "data_class": res["data_class"],
        "study_generated_at": res.get("generated_at"),
    }

    # 1. Headlines vs declared ranking rules
    def eligible(r: dict[str, Any]) -> bool:
        return (r["walk_forward"]["all"]["trades"] or 0) >= MIN_TRADES

    wf = [r for r in rows if eligible(r)]
    by_lb = sorted(wf, key=lambda r: -(r["walk_forward"]["all"]["win_rate_lower_bound"] or 0))
    by_ci = sorted(wf, key=lambda r: -((r["walk_forward"]["all"]["expectancy_ci"] or [-1e18])[0]))
    by_raw = sorted(rows, key=lambda r: -(r["walk_forward"]["all"]["expectancy"] or -1e18))
    rk = res.get("rankings", {})
    stored_wr = next((x for x in rk.get("win_rate_all", []) if not x["small_sample"]), None)
    stored_ra = next((x for x in rk.get("risk_adjusted_all", []) if not x["small_sample"]), None)

    def ident(r: dict[str, Any] | None) -> str | None:
        return f"{r['strategy_id']}/{r['config']}" if r else None

    win_top = ident(by_lb[0]) if by_lb else None
    exp_top = ident(by_ci[0]) if by_ci else None
    why_not = []
    for r in by_raw[:6]:
        w = r["walk_forward"]["all"]
        if ident(r) == exp_top:
            continue
        reasons = []
        if (w["trades"] or 0) < MIN_TRADES:
            reasons.append(f"only {w['trades']} walk-forward trades (< {MIN_TRADES})")
        elif by_ci:
            reasons.append(
                f"lower end of its expectancy interval {w['expectancy_ci']} is below the leader's "
                f"{by_ci[0]['walk_forward']['all']['expectancy_ci']}"
            )
        why_not.append(
            {"row": ident(r), "wf_trades": w["trades"], "wf_expectancy": w["expectancy"], "reasons": reasons}
        )
    out["headlines"] = {
        "win_rate_rule": "walk-forward, all directions, >= 30 trades, sorted by the Wilson 95% lower bound",
        "expectancy_rule": "walk-forward, all directions, >= 30 trades, sorted by the lower end of the 95% bootstrap "
        "expectancy interval",
        "recomputed_win_rate_leader": win_top,
        "stored_win_rate_leader": f"{stored_wr['strategy_id']}/{stored_wr['config']}" if stored_wr else None,
        "recomputed_expectancy_leader": exp_top,
        "stored_expectancy_leader": f"{stored_ra['strategy_id']}/{stored_ra['config']}" if stored_ra else None,
        "consistent": (stored_wr is None or win_top == f"{stored_wr['strategy_id']}/{stored_wr['config']}")
        and (stored_ra is None or exp_top == f"{stored_ra['strategy_id']}/{stored_ra['config']}"),
        "higher_raw_expectancy_not_selected": why_not,
    }

    # 2. Candidate deep-dives: headline leaders + every positive walk-forward row with >= 30 trades
    cands = {k for k in (win_top, exp_top) if k}
    cands |= {ident(r) for r in wf if (r["walk_forward"]["all"]["expectancy"] or 0) > 0}
    budget = (res.get("risk_filter") or {}).get("max_1_contract_loss_usd")
    out["candidates"] = []
    for key in sorted(c for c in cands if c):
        sid, cfg = key.split("/")
        r = by_key[(sid, cfg)]
        w, h, ins = r["walk_forward"]["all"], r["holdout"]["all"], r["research"]["all"]
        ledger = tl[(sid, cfg)]
        wf_trades = [t for t in ledger if t["period"] == "walk_forward"]
        ho_trades = [t for t in ledger if t["period"] == "holdout"]
        months = _monthly(wf_trades)
        pm = [v for v in months.values() if v["trades"]]
        bew = breakeven_win_rate(w["avg_win"], w["avg_loss"])
        flags = []
        if (h["trades"] or 0) < 10:
            flags.append(f"holdout has only {h['trades']} trades: it cannot confirm anything")
        elif (h["expectancy"] or 0) <= 0:
            flags.append(
                f"holdout expectancy ${h['expectancy']} is not positive: the walk-forward edge did not persist"
            )
        for sc in ("slip2", "costs_high", "stop_slip", "late_entry"):
            e = r["stress"].get(sc, {}).get("expectancy")
            if e is not None and e <= 0:
                flags.append(f"negative under stress '{r['stress'][sc]['label']}' (${e}/trade)")
        conc = concentration([t["pnl"] for t in wf_trades])
        if conc["depends_on_few_trades"]:
            flags.append("walk-forward net is not positive without its 5 largest winners")
        if pm and sum(1 for v in pm if v["net"] > 0) / len(pm) < 0.6:
            flags.append(f"only {sum(1 for v in pm if v['net'] > 0)} of {len(pm)} walk-forward months positive")
        ci = w["expectancy_ci"]
        if ci and ci[0] <= 0:
            flags.append(f"95% expectancy interval {ci} includes zero")
        if bew is not None and w["win_rate_ci95"] and w["win_rate_ci95"][0] <= bew:
            flags.append(f"win-rate interval {w['win_rate_ci95']} includes the break-even win rate {bew}")
        over_budget = [t for t in ledger if _loss_1c(t) > (budget or DEFAULT_BUDGET)]
        out["candidates"].append(
            {
                "row": key,
                "evidence": r["evidence"],
                "unmet_requirements": r.get("unmet_requirements", []),
                "research": {k: ins[k] for k in ("trades", "win_rate", "expectancy")},
                "walk_forward": {
                    k: w[k]
                    for k in (
                        "trades",
                        "win_rate",
                        "win_rate_ci95",
                        "expectancy",
                        "expectancy_ci",
                        "profit_factor",
                        "avg_win",
                        "avg_loss",
                        "max_drawdown",
                    )
                },
                "walk_forward_expectancy_ci_adjusted": r.get("walk_forward_expectancy_ci_adjusted"),
                "breakeven_win_rate": bew,
                "holdout": {k: h[k] for k in ("trades", "win_rate", "win_rate_ci95", "expectancy", "expectancy_ci")},
                "holdout_trades_listed": len(ho_trades),
                "stress": {
                    k: {"expectancy": v["expectancy"], "win_rate": v["win_rate"]} for k, v in r["stress"].items()
                },
                "blocks": r["walk_forward_blocks"],
                "blocks_positive_pct": r["blocks_positive_pct"],
                "monthly_walk_forward": months,
                "concentration": conc,
                "apex": _apex(r["apex_replay"]),
                "flags": flags,
                "trades_over_risk_budget": len(over_budget),
                "trustworthy": not flags and r["evidence"].startswith("Validated"),
            }
        )

    # 3. Outlier losses
    out["outliers"] = []
    for t in trades:
        cost = t.get("commission", 0) + t.get("slippage_usd", 0)
        if t["pnl"] < 0 and t.get("risk_usd") and -t["pnl"] > 1.5 * (t["risk_usd"] + cost):
            out["outliers"].append(
                {
                    **{
                        k: t.get(k)
                        for k in (
                            "strategy_id",
                            "config",
                            "period",
                            "direction",
                            "contract",
                            "entry_ts",
                            "entry",
                            "stop",
                            "exit_ts",
                            "exit",
                            "exit_reason",
                            "pnl",
                            "risk_usd",
                            "mae_usd",
                        )
                    },
                    "loss_vs_planned_risk": round(-t["pnl"] / t["risk_usd"], 2),
                }
            )
    largest = sorted(trades, key=lambda t: t["pnl"])[:10]
    out["largest_losses"] = [
        {
            **{
                k: t.get(k)
                for k in (
                    "strategy_id",
                    "config",
                    "period",
                    "direction",
                    "contract",
                    "entry_ts",
                    "entry",
                    "stop",
                    "target",
                    "exit",
                    "exit_reason",
                    "pnl",
                    "risk_usd",
                    "mae_usd",
                    "stop_ticks",
                )
            }
        }
        for t in largest
        if t["pnl"] < 0
    ]
    if budget is None:
        over = [t for t in trades if _loss_1c(t) > DEFAULT_BUDGET]
        out["risk_budget_note"] = (
            f"This study predates the Risk Guardian filter: {len(over)} of {len(trades)} trades had a 1-contract "
            "loss at stop above $1,000 (the default risk budget) and would have been sized at 0 contracts live. "
            "Re-run the study."
        )
    else:
        out["risk_budget_note"] = f"Risk Guardian filter applied: 1-contract loss at stop <= ${budget:,.0f}."

    # 5. Rollover
    ro = res.get("rollover", {})
    out["rollover"] = {
        "contract_source": ro.get("contract_source", "CME roll calendar (inferred)"),
        "segments": [s["contract"] for s in ro.get("segments", [])],
        "excluded_sessions": len(ro.get("excluded_sessions", {})),
        "unexplained_discontinuities": [d for d in ro.get("discontinuities", []) if not d.get("explained_by_roll")],
    }

    # 6. Strict configurations
    strict = []
    for r in rows:
        if r["config"] not in ("t1_net15", "t1_net12"):
            continue
        f = r.get("funnel", {})
        rej = sum(v for k, v in f.items() if k.startswith("signal rejected: net R:R"))
        sig = f.get("signals", 0)
        strict.append(
            {
                "row": f"{r['strategy_id']}/{r['config']}",
                "signals": sig,
                "rejected_by_net_rr": rej,
                "trades": r["all_periods"]["all"]["trades"],
                "structural": sig > 0 and rej >= sig - f.get("signal skipped: position already open", 0),
            }
        )
    out["strict_configs"] = strict

    # 7. Decision summary
    neg = sorted(f"{r['strategy_id']}/{r['config']}" for r in rows if r["evidence"] == "Negative evidence")
    val = sorted(f"{r['strategy_id']}/{r['config']}" for r in rows if r["evidence"].startswith("Validated"))
    paper = [c["row"] for c in out["candidates"] if c["trustworthy"]]
    watch = [
        c["row"]
        for c in out["candidates"]
        if not c["trustworthy"]
        and c["walk_forward"]["expectancy"]
        and c["walk_forward"]["expectancy"] > 0
        and (c["holdout"]["expectancy"] or 0) > 0
        and len(c["flags"]) <= 2
    ]
    out["decision"] = {
        "validated": val,
        "forward_paper_trading": paper,
        "watch_list_research_only": watch,
        "negative_evidence": neg,
        "rule": "forward paper trading only for rows classified Validated with no audit flags; watch list = positive "
        "walk-forward and holdout with at most two flags (research only, no live signals)",
    }
    return out


def _apex(a: dict[str, Any]) -> dict[str, Any]:
    ci = a.get("pass_rate_ci95")
    resolved = a.get("resolved") or 0
    return {
        "rules": a.get("rules", {}).get("label"),
        "resolved_attempts": resolved,
        "passes": a.get("passes"),
        "pass_rate": a.get("pass_rate"),
        "pass_rate_ci95": ci,
        "ci_width": round(ci[1] - ci[0], 3) if ci else None,
        "informative": resolved >= 10 and bool(ci) and ci[1] - ci[0] <= 0.5,
        "note": "fewer than 10 resolved attempts or an interval wider than 50 points: the pass rate is not informative"
        if not (resolved >= 10 and ci and ci[1] - ci[0] <= 0.5)
        else "",
    }


def audit_md(a: dict[str, Any]) -> str:
    h = a["headlines"]
    L = [
        f"# Research audit - {a['symbol']} ({a['data_class']})",
        "",
        f"Study generated {a['study_generated_at']}. {a['risk_budget_note']}",
        "",
        "## 1. Headlines",
        "",
        f"- Win-rate rule: {h['win_rate_rule']}. Leader: **{h['recomputed_win_rate_leader']}** "
        f"(stored headline: {h['stored_win_rate_leader']})",
        f"- Expectancy rule: {h['expectancy_rule']}. Leader: **{h['recomputed_expectancy_leader']}** "
        f"(stored: {h['stored_expectancy_leader']})",
        f"- Consistent with the declared rules: **{h['consistent']}**",
        "",
        "Higher raw walk-forward expectancy, not selected:",
    ]
    L += [
        f"- {x['row']}: ${x['wf_expectancy']}/trade over {x['wf_trades']} trades - {'; '.join(x['reasons'])}"
        for x in h["higher_raw_expectancy_not_selected"]
    ] or ["- none"]
    L += ["", "## 2. Candidates (walk-forward vs holdout vs stress)", ""]
    for c in a["candidates"]:
        w, ho = c["walk_forward"], c["holdout"]
        L += [
            f"### {c['row']} - {c['evidence']}",
            f"- Walk-forward: {w['trades']} trades, win {w['win_rate']} {w['win_rate_ci95']}, ${w['expectancy']}/trade "
            f"{w['expectancy_ci']} (adjusted {c['walk_forward_expectancy_ci_adjusted']}), PF {w['profit_factor']}, "
            f"break-even win rate {c['breakeven_win_rate']}",
            f"- Holdout: {ho['trades']} trades, win {ho['win_rate']}, ${ho['expectancy']}/trade {ho['expectancy_ci']}",
            "- Stress $/trade: " + ", ".join(f"{k} {v['expectancy']}" for k, v in c["stress"].items()),
            "- Months (walk-forward): "
            + ", ".join(f"{m} {v['net']:+.0f} ({v['trades']})" for m, v in c["monthly_walk_forward"].items()),
            f"- Concentration: net {c['concentration']['net']}, without top 5 winners "
            f"{c['concentration']['net_without_top5_wins']}",
            f"- Apex replay: {c['apex']['passes']}/{c['apex']['resolved_attempts']} passed, "
            f"CI {c['apex']['pass_rate_ci95']} "
            f"({c['apex']['rules']}) {c['apex']['note']}",
            "- Flags: " + ("; ".join(c["flags"]) or "none"),
            f"- Trustworthy for paper trading: **{c['trustworthy']}**",
            "",
        ]
    L += [
        "## 3. Largest losses",
        "",
        "| row | period | entry (UTC) | entry | stop | exit | reason | net | planned risk | MAE |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    L += [
        f"| {t['strategy_id']}/{t['config']} | {t['period']} | {t['entry_ts']} | {t['entry']} | {t['stop']} | "
        f"{t['exit']} | "
        f"{t['exit_reason']} | {t['pnl']} | {t['risk_usd']} | {t['mae_usd']} |"
        for t in a["largest_losses"]
    ]
    L += ["", f"Losses beyond 1.5x planned risk + costs (gap fills): {len(a['outliers'])}", ""]
    ro = a["rollover"]
    L += [
        "## 5. Rollover",
        "",
        f"- Contract source: {ro['contract_source']}; segments {ro['segments']}; "
        f"{ro['excluded_sessions']} sessions excluded; unexplained discontinuities: "
        f"{ro['unexplained_discontinuities'] or 'none'}",
        "",
        "## 6. Strict net R:R configurations",
        "",
    ]
    L += [
        f"- {s['row']}: {s['signals']} signals, {s['rejected_by_net_rr']} rejected by net R:R, {s['trades']} trades"
        + (" - structural (T1 design below the minimum)" if s["structural"] else "")
        for s in a["strict_configs"]
    ]
    d = a["decision"]
    L += [
        "",
        "## 7. Decision",
        "",
        f"- Validated: {d['validated'] or 'none'}",
        f"- Forward paper trading: {d['forward_paper_trading'] or 'none'}",
        f"- Watch list (research only): {d['watch_list_research_only'] or 'none'}",
        f"- Negative evidence: {d['negative_evidence'] or 'none'}",
        f"- Rule: {d['rule']}",
    ]
    return "\n".join(L) + "\n"


__all__ = ["audit_md", "audit_study", "breakeven_win_rate", "concentration", "wilson"]
