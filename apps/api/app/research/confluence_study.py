"""Historical comparison of 3/5, 4/5 and 5/5 confluence thresholds on DEVELOPMENT data only.

* Every strategy signal is generated with the backtester's causal replay (bars before each 5m close).
* At each signal time the full analyzer runs on the preceding 8 days of 1-minute bars only, and the
  setup engine computes the five factors, mandatory checks and structural entry/stop/TP1.
* Tier membership: score >= k AND mandatory factors PASS AND a defensible stop, a realistic target and
  net R:R >= the minimum. (The calendar/data gates cannot be reconstructed for old bars; news exposure is
  reported as a breakdown where the stored calendar covers the date, otherwise "unknown".)
* The previously inspected final holdout (the last ``holdout_sessions`` RTH sessions) is EXCLUDED, so
  this study cannot be used to tune on it. Thresholds are compared, not optimized: the live threshold
  stays 4/5 unless genuinely new prospective data says otherwise.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.marketdata.calendar import trading_day
from app.research.backtest import BacktestParams, generate_signals
from app.research.bars import Bar
from app.research.indicators import is_rth
from app.services import analyzer, setups, signal_tracker
from app.strategies.library import STRATEGIES


def rth_sessions(bars: Sequence[Bar]) -> list[str]:
    return sorted({trading_day(b.ts).isoformat() for b in bars if is_rth(b.ts)})


def news_status_at(t: datetime, events: Sequence[tuple[datetime, str]]) -> str:
    """'news window' if a high-impact event is within -15/+15 min, 'no major event' if the stored calendar
    covers that day, else 'unknown' (absence of data is never treated as a quiet day)."""
    near = [ts for ts, imp in events if abs((ts - t).total_seconds()) <= 3 * 86400]
    if not near:
        return "unknown"
    if any(imp == "high" and abs((ts - t).total_seconds()) <= 900 for ts, imp in events):
        return "news window"
    return "no major event"


REJECTIONS = (
    "missing data",
    "no valid structural stop",
    "no valid target",
    "invalid entry zone",
    "TP1 net R:R below minimum",
    "other calculation failures",
)


def rejection(cand: dict[str, Any] | None, doc: dict[str, Any] | None) -> tuple[str | None, list[str]]:
    """(first reason in a fixed order, every applicable reason) for a candidate without tradable levels."""
    if doc is None or cand is None:
        return "other calculation failures", ["other calculation failures"]
    if not (doc.get("data") or {}).get("available"):
        return "missing data", ["missing data"]
    req = {r["key"]: r["ok"] for r in cand["requirements"]}
    lv = cand["levels"]
    every = []
    if lv["stop"] is None:
        every.append("no valid structural stop")
    if lv["tp1"] is None:
        every.append("no valid target")
    if not req["entry zone"]:
        every.append("invalid entry zone")
    if lv["stop"] is not None and lv["tp1"] is not None and not req["net R:R"]:
        every.append("TP1 net R:R below minimum")
    return (every[0] if every else None), every


def _row(x: dict[str, Any]) -> dict[str, Any]:
    from app.marketdata.calendar import ET

    return {
        "signal_time_et": x["confirmed_at"].astimezone(ET).strftime("%Y-%m-%d %H:%M ET"),
        "strategy": x["strategy_id"],
        "score": x["score"],
        "direction": x["direction"],
        "entry": x["entry"],
        "entry_zone": [x["entry_low"], x["entry_high"]],
        "stop": x["stop"],
        "stop_basis": x.get("stop_basis"),
        "tp1": x["tp1"],
        "tp1_basis": x.get("tp1_basis"),
        "tp2": x.get("tp2"),
        "net_rr_tp1": x.get("net_rr_tp1"),
        "planned_risk_usd": x["net_risk_usd"],
        "status": x["status"],
        "outcome": x.get("outcome"),
        "outcome_usd": x.get("outcome_usd"),
        "outcome_r": x.get("outcome_r"),
    }


def risk_audit(
    items: Sequence[dict[str, Any]], per_trade: float, daily_loss: float | None, max_dd: float | None
) -> dict[str, Any]:
    """Planned risk vs realized loss per trade and Apex-style constraint breaches (1 contract, closed P&L).

    Reported as found: nothing is removed from or altered in the research results.
    """
    res = sorted((x for x in items if x.get("outcome") in ("tp1", "stop", "eod")), key=lambda x: x["outcome_at"])
    over = [x for x in res if (x["net_risk_usd"] or 0) > per_trade]
    worse = [x for x in res if x["outcome_usd"] < -(x["net_risk_usd"] or 0) - 0.01]
    eq = peak = dd = 0.0
    dd_breach = None
    days: dict[str, float] = {}
    for x in res:
        eq += x["outcome_usd"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        if max_dd and dd_breach is None and peak - eq >= max_dd:
            dd_breach = x["outcome_at"]
        k = trading_day(x["outcome_at"]).isoformat()
        days[k] = days.get(k, 0.0) + x["outcome_usd"]
    risks = sorted(x["net_risk_usd"] or 0 for x in res)
    return {
        "per_trade_limit_usd": per_trade,
        "daily_loss_limit_usd": daily_loss,
        "max_drawdown_usd": max_dd,
        "resolved_trades": len(res),
        "trades_over_planned_risk_limit": len(over),
        "over_limit_trades": [_row(x) for x in over],
        "trades_losing_more_than_planned": len(worse),
        "worse_than_planned_trades": [_row(x) for x in worse],
        "max_planned_risk_usd": round(risks[-1], 2) if risks else None,
        "median_planned_risk_usd": round(risks[len(risks) // 2], 2) if risks else None,
        "closed_pnl_max_drawdown_usd": round(dd, 2),
        "drawdown_limit_first_breached_at": dd_breach.isoformat() if dd_breach else None,
        "daily_loss_limit_breach_days": sorted(k for k, v in days.items() if daily_loss and v <= -daily_loss),
        "worst_day_usd": round(min(days.values()), 2) if days else None,
    }


def r_vs_dollars(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Why mean $ and mean R can disagree in sign: R divides each trade by its own planned risk, so a few
    wide-stop trades dominate dollars but not R."""
    res = [x for x in items if x.get("outcome") in ("tp1", "stop", "eod")]
    if not res:
        return {}
    win = [x for x in res if x["outcome_usd"] > 0]
    loss = [x for x in res if x["outcome_usd"] <= 0]

    def avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 2) if xs else None

    tot_risk = sum(x["net_risk_usd"] or 0 for x in res)
    gap = abs(sum(x["outcome_usd"] for x in res) - sum(x["outcome_r"] * (x["net_risk_usd"] or 0) for x in res))
    return {
        "mean_usd": avg([x["outcome_usd"] for x in res]),
        "mean_r": round(sum(x["outcome_r"] for x in res) / len(res), 4),
        "avg_planned_risk_winners_usd": avg([x["net_risk_usd"] or 0 for x in win]),
        "avg_planned_risk_losers_usd": avg([x["net_risk_usd"] or 0 for x in loss]),
        "risk_weighted_r": round(sum(x["outcome_usd"] for x in res) / tot_risk, 4) if tot_risk else None,
        "identity_holds": gap < 0.05 * len(res) + 0.01,
        "identity": "for every trade $ = R x planned risk; mean $ and mean R differ only by risk weighting",
    }


def _verdict_3(views: dict[str, Any]) -> str:
    """Is 3/5 still positive once duplicates and risk-ineligible opportunities are removed?"""
    t = views["risk_eligible_one_position"]["tiers"]["3/5 loose (research only)"]
    n, e, ci = t["resolved"], t["expectancy"], t["expectancy_ci"]
    if not n:
        return "No resolved risk-eligible 3/5 opportunities: nothing to evaluate."
    base = f"3/5, unique and risk-eligible, one position at a time: {n} resolved, ${e}/trade"
    if ci and ci[0] > 0:
        return base + f", 95% CI {ci} entirely above $0 - positive in this sample (still unvalidated research)."
    if ci and ci[1] < 0:
        return base + f", 95% CI {ci} entirely below $0 - negative in this sample."
    return base + (f", 95% CI {ci} includes $0" if ci else "") + " - not distinguishable from zero."


def run(
    symbol: str,
    m1: Sequence[Bar],
    d1: Sequence[Bar],
    *,
    holdout_sessions: int = 50,
    holdout_start: str | None = None,
    strategies: Sequence[str] | None = None,
    events: Sequence[tuple[datetime, str]] = (),
    cfg: setups.SetupConfig | None = None,
    risk_limits: dict[str, float] | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    """``holdout_start``: first session of the previously inspected holdout (from the stored research study).
    Everything from the earlier of that date and the last ``holdout_sessions`` sessions is excluded, so
    sessions added since that study can never move inspected holdout days back into development."""
    cfg = cfg or setups.SetupConfig()
    sessions = rth_sessions(m1)
    dev = sessions[:-holdout_sessions] if holdout_sessions and len(sessions) > holdout_sessions else []
    if holdout_start:
        dev = [d for d in dev if d < holdout_start]
    if not dev:
        return {
            "symbol": symbol,
            "error": f"only {len(sessions)} sessions: nothing left after excluding the "
            f"{holdout_sessions}-session holdout",
            "sessions": len(sessions),
        }
    last_dev = dev[-1]
    dev_bars = [b for b in m1 if trading_day(b.ts).isoformat() <= last_dev]
    ts_list = [b.ts for b in dev_bars]
    ids = [s.spec.id for s in STRATEGIES if not strategies or s.spec.id in strategies]
    items: list[dict[str, Any]] = []
    counts: dict[int, int] = {}
    funnel: dict[str, int] = {"signals": 0, **dict.fromkeys(REJECTIONS, 0), "mandatory failed": 0, "recorded": 0}
    nonexclusive = dict.fromkeys(REJECTIONS, 0)
    fstat = {n: {"PASS": 0, "FAIL": 0, "UNKNOWN": 0} for n in setups.FACTORS}
    score_all: dict[int, int] = {}
    for sid in ids:
        _, evs = generate_signals(BacktestParams(strategy_id=sid, symbol=symbol), dev_bars)
        for ev in evs:
            funnel["signals"] += 1
            t = ev.close_ts
            lo = bisect.bisect_left(ts_list, t - timedelta(days=analyzer.LOOKBACK_DAYS))
            hi = bisect.bisect_left(ts_list, t)
            window = dev_bars[lo:hi]  # bars that opened before the signal close only
            sg = ev.signal
            sig = {
                "strategy_id": sid,
                "direction": sg.direction,
                "entry": sg.entry,
                "entry_low": sg.entry_low,
                "entry_high": sg.entry_high,
                "stop": sg.stop,
                "targets": list(sg.targets),
                "bar_ts": sg.bar_ts.isoformat(),
                "created_at": t.isoformat(),
            }
            evals = [
                {"strategy_id": sid, "triggered": True, "side": sg.direction, "passed": 1, "total": 1, "conditions": []}
            ]
            doc: dict[str, Any] | None = None
            cand: dict[str, Any] | None = None
            try:
                doc = analyzer.build(
                    symbol,
                    window,
                    [b for b in d1 if b.ts.date() < trading_day(t)],
                    t,
                    {"quality": "historical"},
                    "historical",
                    None,
                    evals,
                    [sig],
                    {},
                    cfg.costs,
                    cfg,
                )
                cand = next(iter(doc["signal"]["candidates"]), None)
            except Exception:  # counted as "other calculation failures", never silently dropped
                doc = cand = None
            if cand is not None:
                score_all[cand["score"]] = score_all.get(cand["score"], 0) + 1
                for f in cand["factors"]:
                    fstat[f["n"]][f["status"]] += 1
            first, every = rejection(cand, doc)
            for reason in every:
                nonexclusive[reason] += 1
            if first or cand is None:
                funnel[first or "other calculation failures"] += 1
                continue
            reqs = {r["key"]: r["ok"] for r in cand["requirements"]}
            if not reqs["mandatory"]:
                funnel["mandatory failed"] += 1
                continue
            counts[cand["score"]] = counts.get(cand["score"], 0) + 1
            lv = cand["levels"]
            rec = {
                "symbol": symbol,
                "direction": sig["direction"],
                "entry": lv["entry"],
                "entry_low": lv["entry_zone"][0],
                "entry_high": lv["entry_zone"][1],
                "stop": lv["stop"],
                "tp1": lv["tp1"],
                "tp2": lv["tp2"],
                "stop_basis": lv["stop_basis"],
                "tp1_basis": lv["tp1_basis"],
                "net_rr_tp1": lv["rr_tp1"]["net"],
                "confirmed_at": t,
                "expires_at": t + timedelta(minutes=cfg.expiry_minutes),
                "net_risk_usd": lv["rr_tp1"]["net_risk"],
                "net_reward_usd": lv["rr_tp1"]["net_reward"],
                "costs_usd": lv["rr_tp1"]["costs"],
                "status": "CONFIRMED",
            }
            j = bisect.bisect_left(ts_list, t)
            res = signal_tracker.simulate(rec, dev_bars[j : j + 600])
            funnel["recorded"] += 1
            items.append(
                {**rec, **res, "strategy_id": sid, "score": cand["score"], "news_status": news_status_at(t, events)}
            )
            if progress:
                progress(sid, funnel["recorded"])
    st = signal_tracker
    detections = [x for x in items if x["score"] >= 3]
    events = st.group_opportunities(detections)
    lim = {"per_trade": 1000.0, "daily_loss": 1000.0, "max_dd": 2000.0, **(risk_limits or {})}
    eligible = [e for e in events if (e.get("net_risk_usd") or 0) <= lim["per_trade"]]
    seq_all = st.one_position_at_a_time(events)
    seq_eligible = st.one_position_at_a_time(eligible)
    views = {
        "strategy_detections": st.compare_thresholds(detections, counts),
        "unique_opportunities": st.compare_thresholds(events),
        "one_position_at_a_time": st.compare_thresholds(seq_all),
        "risk_eligible_one_position": st.compare_thresholds(seq_eligible),
    }
    cmp = views["unique_opportunities"]
    tiers = {"3/5": 3, "4/5": 4, "5/5": 5}
    return {
        "symbol": symbol,
        "generated_at": datetime.now(UTC).isoformat(),
        "engine_version": setups.ENGINE_VERSION,
        "period": {
            "first": dev[0],
            "last": last_dev,
            "sessions": len(dev),
            "excluded_holdout_sessions": len(sessions) - len(dev),
            "holdout_starts": sessions[len(dev)] if len(sessions) > len(dev) else None,
            "stored_study_holdout_start": holdout_start,
        },
        "funnel": funnel,
        "rejections_nonexclusive": nonexclusive,
        "factor_pass_rates": {
            setups.FACTORS[n]: {**v, "pass_rate": round(v["PASS"] / max(1, sum(v.values())), 4)}
            for n, v in fstat.items()
        },
        "score_distribution_all_candidates": dict(sorted(score_all.items())),
        "score_counts": counts,
        "status_counts": {
            k: sum(1 for x in items if x["status"] == k) for k in ("CONFIRMED", "EXPIRED", "MISSED", "INVALIDATED")
        },
        "comparison": cmp,
        "views": views,
        "opportunity_counts": {
            k: {
                "strategy_detections": sum(1 for x in detections if x["score"] >= v),
                "unique_opportunities": sum(1 for e in events if e["score"] >= v),
                "multi_strategy_opportunities": sum(1 for e in events if e["score"] >= v and e["detections"] > 1),
                "risk_eligible": sum(1 for e in eligible if e["score"] >= v),
                "risk_ineligible": sum(1 for e in events if e["score"] >= v and e not in eligible),
                "one_position_at_a_time": sum(1 for e in seq_all if e["score"] >= v),
                "risk_eligible_one_position": sum(1 for e in seq_eligible if e["score"] >= v),
            }
            for k, v in tiers.items()
        },
        "inconsistent_opportunities": [e["opportunity_id"] for e in events if not e["consistent"]],
        "duplicate_examples": [
            {**_row(e), "opportunity_id": e["opportunity_id"], "strategies": e["strategies"]}
            for e in events
            if e["detections"] > 1
        ][:50],
        "r_vs_dollars": {k: r_vs_dollars([e for e in events if e["score"] >= v]) for k, v in tiers.items()},
        "risk_audit": {
            k: {
                "unique_opportunities": risk_audit(
                    [e for e in events if e["score"] >= v], lim["per_trade"], lim["daily_loss"], lim["max_dd"]
                ),
                "risk_eligible_one_position": risk_audit(
                    [e for e in seq_eligible if e["score"] >= v], lim["per_trade"], lim["daily_loss"], lim["max_dd"]
                ),
                "exposure_if_every_detection_executed": signal_tracker.exposure(
                    [x for x in detections if x["score"] >= v]
                ),
                "exposure_unique_opportunities": signal_tracker.exposure([e for e in events if e["score"] >= v]),
            }
            for k, v in tiers.items()
        },
        "three_of_five_verdict": _verdict_3(views),
        "trades_4_of_5_or_better": [
            {**_row(e), "opportunity_id": e["opportunity_id"], "strategies": e["strategies"]}
            for e in events
            if e["score"] >= 4
        ],
        "notes": [
            "Development period only; the previously inspected holdout is excluded.",
            "Calendar/data gates are not reconstructed for history; news exposure is a breakdown, not a filter.",
            "1 contract per trade; no per-trade risk cap is applied to the results (violations are reported).",
            "Do not change the live threshold from this study alone.",
        ],
    }


VIEW_TITLES = {
    "strategy_detections": "Strategy-level detections (one row per strategy; the same opportunity can repeat)",
    "unique_opportunities": "Unique market opportunities (combined performance; each opportunity once)",
    "one_position_at_a_time": "Executable: one position at a time",
    "risk_eligible_one_position": "Executable and risk-eligible (planned risk <= limit), one position at a time",
}


def _table(cmp: dict[str, Any]) -> list[str]:
    out = [
        "| Threshold | Signals | Resolved | Win rate (95% CI) | Avg win | Avg loss | $/trade (95% CI) | PF | Max DD "
        "| Max loss streak | Avg R |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, t in cmp["tiers"].items():
        out.append(
            f"| {name} | {t['signals']} | {t['resolved']} | {t['win_rate']} {t['win_rate_ci95']} | {t['avg_win']} | "
            f"{t['avg_loss']} | {t['expectancy']} {t['expectancy_ci']} | {t['profit_factor']} | {t['max_drawdown']} | "
            f"{t['max_consecutive_losses']} | {t['avg_r']} |"
        )
    return [*out, "", f"Verdict: {cmp['verdict']}", ""]


def to_markdown(r: dict[str, Any]) -> str:
    if "error" in r:
        return f"# Confluence threshold study - {r['symbol']}\n\n{r['error']}\n"
    p = r["period"]
    lines = [
        f"# Confluence threshold study - {r['symbol']} ({r['engine_version']})",
        "",
        f"Development sessions {p['first']} .. {p['last']} ({p['sessions']}); {p['excluded_holdout_sessions']} "
        f"holdout sessions excluded (stored study holdout start: {p.get('stored_study_holdout_start')}).",
        "",
        f"Funnel: {r['funnel']}",
        "Rejections (non-exclusive): " + str(r["rejections_nonexclusive"]),
        "Factor pass rates: " + ", ".join(f"{k} {v['pass_rate']:.0%}" for k, v in r["factor_pass_rates"].items()),
        f"Score distribution (all candidates): {r['score_distribution_all_candidates']}",
        "",
        "## Distinct opportunity counts",
        "",
        "| Threshold | Strategy detections | Unique opportunities | Multi-strategy | Risk-eligible | Risk-ineligible "
        "| One at a time | Eligible, one at a time |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for k, c in r["opportunity_counts"].items():
        lines.append(
            f"| {k} | {c['strategy_detections']} | {c['unique_opportunities']} | {c['multi_strategy_opportunities']} | "
            f"{c['risk_eligible']} | {c['risk_ineligible']} | {c['one_position_at_a_time']} | "
            f"{c['risk_eligible_one_position']} |"
        )
    if r["inconsistent_opportunities"]:
        lines += ["", f"WARNING: inconsistent outcomes within opportunities: {r['inconsistent_opportunities']}"]
    for key, title in VIEW_TITLES.items():
        lines += ["", f"## {title}", "", *_table(r["views"][key])]
    lines += ["## Risk (limit per trade, daily loss, max drawdown from the configured rule profile)", ""]
    for k, ra in r["risk_audit"].items():
        u, e = ra["unique_opportunities"], ra["risk_eligible_one_position"]
        x1, x2 = ra["exposure_if_every_detection_executed"], ra["exposure_unique_opportunities"]
        rv = r["r_vs_dollars"].get(k) or {}
        lines.append(
            f"- {k}: unique opportunities - {u['trades_over_planned_risk_limit']} over ${u['per_trade_limit_usd']:.0f} "
            f"planned risk (max ${u['max_planned_risk_usd']}, median ${u['median_planned_risk_usd']}), "
            f"{u['trades_losing_more_than_planned']} lost more than planned, "
            f"max DD ${u['closed_pnl_max_drawdown_usd']} "
            f"(limit ${u['max_drawdown_usd']}, first breach {u['drawdown_limit_first_breached_at']}), "
            f"{len(u['daily_loss_limit_breach_days'])} daily-loss breach days. Risk-eligible one at a time - max DD "
            f"${e['closed_pnl_max_drawdown_usd']}, first breach {e['drawdown_limit_first_breached_at']}, "
            f"{len(e['daily_loss_limit_breach_days'])} daily-loss breach days. If every detection were executed: "
            f"{x1['max_concurrent_positions']} concurrent positions, ${x1['max_concurrent_planned_risk_usd']} planned "
            f"risk at once (unique: {x2['max_concurrent_positions']}, ${x2['max_concurrent_planned_risk_usd']}). "
            f"Mean ${rv.get('mean_usd')} vs mean R {rv.get('mean_r')} (winners' avg risk "
            f"${rv.get('avg_planned_risk_winners_usd')}, losers' ${rv.get('avg_planned_risk_losers_usd')})."
        )
    lines += [
        "",
        f"**3/5 after removing duplicates and risk-ineligible opportunities:** {r['three_of_five_verdict']}",
        "",
        "## Every 4/5+ opportunity",
        "",
        "| Signal (ET) | Strategies | Score | Dir | Entry | Stop | TP1 | TP2 | Risk $ | Outcome | $ | R |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in r["trades_4_of_5_or_better"]:
        lines.append(
            f"| {t['signal_time_et']} | {', '.join(t['strategies'])} | {t['score']} | {t['direction']} | "
            f"{t['entry']} | "
            f"{t['stop']} | {t['tp1']} | {t['tp2']} | {t['planned_risk_usd']} | {t['outcome'] or t['status']} | "
            f"{t['outcome_usd']} | {t['outcome_r']} |"
        )
    lines += ["", r["comparison"]["method"], "", r["comparison"]["caveat"]]
    return "\n".join(lines) + "\n"
