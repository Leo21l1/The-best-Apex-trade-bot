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


VARIANTS: dict[str, dict[str, bool]] = {
    "live": {},
    "H1 family location": {"exp_family_location": True},
    "H2 major targets": {"exp_major_targets": True},
    "H3 15m internal fallback": {"exp_15m_internal_fallback": True},
    "H4 native stop + T2": {"exp_native_levels": True},
    "H1+H2+H3": {"exp_family_location": True, "exp_major_targets": True, "exp_15m_internal_fallback": True},
}


def account_survival(items: Sequence[dict[str, Any]], daily_loss: float | None, max_dd: float | None) -> dict[str, Any]:
    """Replay resolved trades (time order) on ONE simulated account and stop at the first hard breach.

    * Trailing drawdown (EOD style): the threshold is the highest end-of-day closed balance minus
      ``max_dd``; closed equity at or below it terminates the account - nothing after it is traded and the
      account is never restarted.
    * Daily loss limit: once the day's closed P&L reaches -``daily_loss`` no further trades that day.
    Intratrade adverse excursion is not modelled (closed P&L only), so real breaches can come earlier.
    """
    res = sorted((x for x in items if x.get("outcome") in ("tp1", "stop", "eod")), key=lambda x: x["outcome_at"])
    eq = 0.0
    eod_peak = 0.0
    day = None
    day_pnl = 0.0
    taken = skipped_day = 0
    out: dict[str, Any] = {"terminated": False, "terminated_at": None, "trades_after_termination": 0}
    for i, x in enumerate(res):
        d = trading_day(x["outcome_at"]).isoformat()
        if d != day:
            eod_peak = max(eod_peak, eq)  # previous day's close updates the trailing threshold
            day, day_pnl = d, 0.0
        if daily_loss and day_pnl <= -daily_loss:
            skipped_day += 1
            continue
        eq += x["outcome_usd"]
        day_pnl += x["outcome_usd"]
        taken += 1
        if max_dd and eq <= eod_peak - max_dd:
            out.update(
                terminated=True, terminated_at=x["outcome_at"].isoformat(), trades_after_termination=len(res) - i - 1
            )
            break
    pnl_taken = round(eq, 2)
    return {
        **out,
        "trades_taken": taken,
        "trades_skipped_by_daily_limit": skipped_day,
        "net_before_end_usd": pnl_taken,
        "rules": {"daily_loss_usd": daily_loss, "trailing_drawdown_usd": max_dd, "trailing": "end-of-day"},
        "note": "Closed-trade P&L only; never restarted after a breach.",
    }


def winner_dependence(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """How much the result depends on the largest winners."""
    res = sorted((x["outcome_usd"] for x in items if x.get("outcome") in ("tp1", "stop", "eod")), reverse=True)
    if not res:
        return {}
    total = sum(res)
    wins = [v for v in res if v > 0]

    def mean_without(k: int) -> float | None:
        rest = res[k:]
        return round(sum(rest) / len(rest), 2) if rest else None

    return {
        "net_usd": round(total, 2),
        "largest_win_usd": wins[0] if wins else None,
        "top1_share_of_gross_profit": round(wins[0] / sum(wins), 3) if wins else None,
        "top3_share_of_gross_profit": round(sum(wins[:3]) / sum(wins), 3) if wins else None,
        "mean_without_largest_win": mean_without(1),
        "mean_without_3_largest_wins": mean_without(3),
    }


def cost_sensitivity(items: Sequence[dict[str, Any]], symbol: str) -> dict[str, Any]:
    """Mean $/trade under extra costs: +1 tick slippage per side, and doubled commission."""
    from app.marketdata.instruments import INSTRUMENTS
    from app.services.analyzer import _cost_cfg

    inst = INSTRUMENTS[symbol]
    tick_usd = float(inst.tick_value)
    comm = _cost_cfg().commission_per_side["micro" if inst.is_micro else "standard"] * 2
    res = [x["outcome_usd"] for x in items if x.get("outcome") in ("tp1", "stop", "eod")]
    if not res:
        return {}
    n = len(res)
    return {
        "base_mean_usd": round(sum(res) / n, 2),
        "plus_1_tick_slippage_each_side": round(sum(res) / n - 2 * tick_usd, 2),
        "double_commission": round(sum(res) / n - comm, 2),
        "both": round(sum(res) / n - 2 * tick_usd - comm, 2),
    }


def _category(text: str | None) -> str:
    if not text:
        return "none"
    for key in (
        "Equal highs",
        "Equal lows",
        "Previous day",
        "Previous week",
        "Previous month",
        "Overnight",
        "Prior RTH",
        "15m swing",
        "15m internal",
        "Bearish",
        "Bullish",
        "sweep extreme",
        "FVG invalidation",
        "order block invalidation",
        "5m swing",
        "15m swing",
        "strategy",
    ):
        if key.lower() in text.lower():
            return key
    return text.split(" ")[0]


def _f1_reason(f: dict[str, Any]) -> str:
    if f["status"] == "PASS":
        return "pass"
    if f["status"] == "UNKNOWN":
        return "unknown (no confirmed 1h/15m structure)"
    if "poor location" in f["detail"]:
        return "aligned bias, adverse premium/discount location"
    if "neutral" in f["detail"].split(",")[0]:
        return "bias neutral (1h/15m unclear or conflicting)"
    return "bias opposite to the signal"


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[len(xs) // 2], 3)


def _pipeline(
    items: list[dict[str, Any]], counts: dict[int, int], lim: dict[str, float], symbol: str
) -> dict[str, Any]:
    st = signal_tracker
    detections = [x for x in items if x["score"] >= 3]
    events = st.group_opportunities(detections)
    eligible = [e for e in events if (e.get("net_risk_usd") or 0) <= lim["per_trade"]]
    seq_all = st.one_position_at_a_time(events)
    seq_eligible = st.one_position_at_a_time(eligible)
    tiers = {"3/5": 3, "4/5": 4, "5/5": 5}
    views = {
        "strategy_detections": st.compare_thresholds(detections, counts),
        "unique_opportunities": st.compare_thresholds(events),
        "one_position_at_a_time": st.compare_thresholds(seq_all),
        "risk_eligible_one_position": st.compare_thresholds(seq_eligible),
    }
    per_tier = {}
    for k, v in tiers.items():
        se = [e for e in seq_eligible if e["score"] >= v]
        per_tier[k] = {
            "survival": account_survival(se, lim["daily_loss"], lim["max_dd"]),
            "winner_dependence": winner_dependence(se),
            "cost_sensitivity": cost_sensitivity(se, symbol),
        }
    return {
        "detections": detections,
        "events": events,
        "eligible": eligible,
        "seq_all": seq_all,
        "seq_eligible": seq_eligible,
        "views": views,
        "per_tier": per_tier,
    }


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
    variants: dict[str, dict[str, bool]] | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    """``holdout_start``: first session of the previously inspected holdout (from the stored research study).
    Everything from the earlier of that date and the last ``holdout_sessions`` sessions is excluded, so
    sessions added since that study can never move inspected holdout days back into development.

    ``variants``: the live rules plus pre-registered experimental hypotheses, all evaluated on the SAME
    signals and analyzer documents, with identical outcome rules. Experimental variants never affect live.
    """
    from dataclasses import replace

    cfg = cfg or setups.SetupConfig()
    variants = variants if variants is not None else VARIANTS
    vcfg = {name: replace(cfg, **flags) for name, flags in variants.items()}
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
    items: dict[str, list[dict[str, Any]]] = {v: [] for v in vcfg}
    counts: dict[str, dict[int, int]] = {v: {} for v in vcfg}
    funnels: dict[str, dict[str, int]] = {
        v: {"signals": 0, **dict.fromkeys(REJECTIONS, 0), "mandatory failed": 0, "recorded": 0} for v in vcfg
    }
    nonexclusive = dict.fromkeys(REJECTIONS, 0)
    fstat = {n: {"PASS": 0, "FAIL": 0, "UNKNOWN": 0} for n in setups.FACTORS}
    f1_reasons: dict[str, int] = {}
    by_family: dict[str, dict[str, int]] = {}
    score_all: dict[int, int] = {}
    rr_diag: dict[str, Any] = {
        "tp1_basis": {},
        "stop_basis": {},
        "risk_atr": [],
        "reward_atr": [],
        "gross_ok_net_fail": 0,
        "tp2_would_pass": 0,
        "rejected": 0,
    }
    sim_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
    for sid in ids:
        _, evs = generate_signals(BacktestParams(strategy_id=sid, symbol=symbol), dev_bars)
        fam = setups.FAMILY_OF.get(sid, "other")
        for ev in evs:
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
            except Exception:  # counted as "other calculation failures", never silently dropped
                doc = None
            atr5 = ((doc or {}).get("context") or {}).get("atr5") or None
            for vname, vc in vcfg.items():
                funnel = funnels[vname]
                funnel["signals"] += 1
                cand: dict[str, Any] | None = None
                if doc is not None:
                    try:
                        cand = setups.evaluate(doc, sig, {}, vc)
                    except Exception:
                        cand = None
                live = vname == "live"
                if live and cand is not None:
                    score_all[cand["score"]] = score_all.get(cand["score"], 0) + 1
                    for f in cand["factors"]:
                        fstat[f["n"]][f["status"]] += 1
                    r1 = _f1_reason(cand["factors"][0])
                    f1_reasons[r1] = f1_reasons.get(r1, 0) + 1
                first, every = rejection(cand, doc)
                if live:
                    for reason in every:
                        nonexclusive[reason] += 1
                    fb = by_family.setdefault(
                        fam, {"signals": 0, "rr_rejected": 0, "mandatory_failed": 0, "recorded": 0}
                    )
                    fb["signals"] += 1
                    if first == "TP1 net R:R below minimum" and cand is not None:
                        lvd = cand["levels"]
                        fb["rr_rejected"] += 1
                        rr_diag["rejected"] += 1
                        tb, sb = _category(lvd["tp1_basis"]), _category(lvd["stop_basis"])
                        rr_diag["tp1_basis"][tb] = rr_diag["tp1_basis"].get(tb, 0) + 1
                        rr_diag["stop_basis"][sb] = rr_diag["stop_basis"].get(sb, 0) + 1
                        if atr5 and lvd["risk_points"]:
                            rr_diag["risk_atr"].append(lvd["risk_points"] / atr5)
                            rr_diag["reward_atr"].append(abs(lvd["tp1"] - lvd["entry"]) / atr5)
                        g = (lvd["rr_tp1"] or {}).get("gross")
                        if g is not None and g >= vc.min_net_rr:
                            rr_diag["gross_ok_net_fail"] += 1
                        if (lvd["rr_tp2"] or {}).get("net") and lvd["rr_tp2"]["net"] >= vc.min_net_rr:
                            rr_diag["tp2_would_pass"] += 1
                if first or cand is None:
                    funnel[first or "other calculation failures"] += 1
                    continue
                reqs = {r["key"]: r["ok"] for r in cand["requirements"]}
                if not reqs["mandatory"]:
                    funnel["mandatory failed"] += 1
                    if live:
                        by_family[fam]["mandatory_failed"] += 1
                    continue
                counts[vname][cand["score"]] = counts[vname].get(cand["score"], 0) + 1
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
                key = (t, rec["direction"], rec["entry"], rec["stop"], rec["tp1"])
                if key not in sim_cache:
                    j = bisect.bisect_left(ts_list, t)
                    sim_cache[key] = signal_tracker.simulate(rec, dev_bars[j : j + 600])
                funnel["recorded"] += 1
                if live:
                    by_family[fam]["recorded"] += 1
                items[vname].append(
                    {
                        **rec,
                        **sim_cache[key],
                        "strategy_id": sid,
                        "score": cand["score"],
                        "news_status": news_status_at(t, events),
                        "regime": ev.regime,
                        "family": fam,
                    }
                )
            if progress:
                progress(sid, funnels["live"]["recorded"])
    lim = {"per_trade": 1000.0, "daily_loss": 1000.0, "max_dd": 2000.0, **(risk_limits or {})}
    pipes = {v: _pipeline(items[v], counts[v], lim, symbol) for v in vcfg}
    p = pipes["live"]
    tiers = {"3/5": 3, "4/5": 4, "5/5": 5}
    live_events, live_eligible, seq_all, seq_eligible = p["events"], p["eligible"], p["seq_all"], p["seq_eligible"]
    detections = p["detections"]
    rr = rr_diag
    rr_summary = {
        "rejected_for_rr": rr["rejected"],
        "tp1_basis": dict(sorted(rr["tp1_basis"].items(), key=lambda kv: -kv[1])),
        "stop_basis": dict(sorted(rr["stop_basis"].items(), key=lambda kv: -kv[1])),
        "median_risk_atr5": _median(rr["risk_atr"]),
        "median_tp1_distance_atr5": _median(rr["reward_atr"]),
        "gross_rr_ok_but_net_fails_after_costs": rr["gross_ok_net_fail"],
        "tp2_would_meet_minimum": rr["tp2_would_pass"],
    }
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
        "risk_limits": lim,
        "funnel": funnels["live"],
        "rejections_nonexclusive": nonexclusive,
        "rr_diagnostics": rr_summary,
        "factor_pass_rates": {
            setups.FACTORS[n]: {**v, "pass_rate": round(v["PASS"] / max(1, sum(v.values())), 4)}
            for n, v in fstat.items()
        },
        "htf_bias_fail_reasons": dict(sorted(f1_reasons.items(), key=lambda kv: -kv[1])),
        "by_family": by_family,
        "score_distribution_all_candidates": dict(sorted(score_all.items())),
        "score_counts": counts["live"],
        "status_counts": {
            k: sum(1 for x in items["live"] if x["status"] == k)
            for k in ("CONFIRMED", "EXPIRED", "MISSED", "INVALIDATED")
        },
        "comparison": p["views"]["unique_opportunities"],
        "views": p["views"],
        "per_tier": p["per_tier"],
        "opportunity_counts": {
            k: {
                "strategy_detections": sum(1 for x in detections if x["score"] >= v),
                "unique_opportunities": sum(1 for e in live_events if e["score"] >= v),
                "multi_strategy_opportunities": sum(1 for e in live_events if e["score"] >= v and e["detections"] > 1),
                "risk_eligible": sum(1 for e in live_eligible if e["score"] >= v),
                "risk_ineligible": sum(1 for e in live_events if e["score"] >= v and e not in live_eligible),
                "one_position_at_a_time": sum(1 for e in seq_all if e["score"] >= v),
                "risk_eligible_one_position": sum(1 for e in seq_eligible if e["score"] >= v),
            }
            for k, v in tiers.items()
        },
        "inconsistent_opportunities": [e["opportunity_id"] for e in live_events if not e["consistent"]],
        "duplicate_examples": [
            {**_row(e), "opportunity_id": e["opportunity_id"], "strategies": e["strategies"]}
            for e in live_events
            if e["detections"] > 1
        ][:50],
        "r_vs_dollars": {k: r_vs_dollars([e for e in live_events if e["score"] >= v]) for k, v in tiers.items()},
        "risk_audit": {
            k: {
                "unique_opportunities": risk_audit(
                    [e for e in live_events if e["score"] >= v], lim["per_trade"], lim["daily_loss"], lim["max_dd"]
                ),
                "risk_eligible_one_position": risk_audit(
                    [e for e in seq_eligible if e["score"] >= v], lim["per_trade"], lim["daily_loss"], lim["max_dd"]
                ),
                "exposure_if_every_detection_executed": signal_tracker.exposure(
                    [x for x in detections if x["score"] >= v]
                ),
                "exposure_unique_opportunities": signal_tracker.exposure([e for e in live_events if e["score"] >= v]),
            }
            for k, v in tiers.items()
        },
        "three_of_five_verdict": _verdict_3(p["views"]),
        "experiments": {
            name: {
                "flags": variants[name],
                "funnel": funnels[name],
                "risk_eligible_one_position": {
                    k: {
                        kk: t.get(kk)
                        for kk in (
                            "signals",
                            "resolved",
                            "win_rate",
                            "win_rate_ci95",
                            "avg_win",
                            "avg_loss",
                            "expectancy",
                            "expectancy_ci",
                            "profit_factor",
                            "avg_r",
                            "max_drawdown",
                            "max_consecutive_losses",
                        )
                    }
                    for k, t in pipes[name]["views"]["risk_eligible_one_position"]["tiers"].items()
                },
                "per_tier": pipes[name]["per_tier"],
                "verdict": pipes[name]["views"]["risk_eligible_one_position"]["verdict"],
            }
            for name in vcfg
        },
        "events_export": {
            name: [
                {
                    k: (x[k].isoformat() if isinstance(x[k], datetime) else x[k])
                    for k in (
                        "symbol",
                        "direction",
                        "entry",
                        "stop",
                        "tp1",
                        "score",
                        "strategy_id",
                        "confirmed_at",
                        "expires_at",
                        "filled_at",
                        "outcome",
                        "outcome_at",
                        "outcome_usd",
                        "outcome_r",
                        "net_risk_usd",
                        "status",
                        "status_at",
                        "regime",
                        "family",
                    )
                    if k in x
                }
                for x in pipes[name]["eligible"]
            ]
            for name in vcfg
        },
        "trades_4_of_5_or_better": [
            {**_row(e), "opportunity_id": e["opportunity_id"], "strategies": e["strategies"]}
            for e in live_events
            if e["score"] >= 4
        ],
        "notes": [
            "Development period only; the previously inspected holdout is excluded.",
            "Experimental variants (H1, H2) are pre-registered hypotheses evaluated on the same signals; they do "
            "not change the live analyzer. Several variants x thresholds were compared: treat any single "
            "favourable cell with extra suspicion (multiple comparisons).",
            "Calendar/data gates are not reconstructed for history; news exposure is a breakdown, not a filter.",
            "1 contract per trade; per-trade risk cap applied in the risk-eligible view only (violations reported).",
            "Do not change the live threshold or rules from this study alone.",
        ],
    }


def combine(results: Sequence[dict[str, Any]], variant: str = "live") -> dict[str, Any]:
    """Shared account across instruments: merge exported risk-eligible opportunities, one position at a
    time ACROSS ES/NQ/MNQ (correlated index futures share one account), then survival."""
    from datetime import datetime as dt

    lim = results[0].get("risk_limits") or {"per_trade": 1000.0, "daily_loss": 1000.0, "max_dd": 2000.0}
    evs = []
    for r in results:
        for x in (r.get("events_export") or {}).get(variant, []):
            y = dict(x)
            for k in ("confirmed_at", "expires_at", "filled_at", "outcome_at", "status_at"):
                if y.get(k):
                    y[k] = dt.fromisoformat(y[k])
            y.setdefault("opportunity_id", f"{y['symbol']}|{y['confirmed_at'].isoformat()}|{y['entry']}")
            evs.append(y)
    seq = signal_tracker.one_position_at_a_time(evs)
    out: dict[str, Any] = {
        "variant": variant,
        "instruments": [r["symbol"] for r in results],
        "opportunities": len(evs),
        "taken_one_at_a_time": len(seq),
        "tiers": {},
    }
    for k, v in {"3/5": 3, "4/5": 4, "5/5": 5}.items():
        sub = [e for e in seq if e["score"] >= v]
        cmp = signal_tracker.compare_thresholds(sub)["tiers"]["3/5 loose (research only)"]
        out["tiers"][k] = {
            "resolved": cmp["resolved"],
            "win_rate": cmp["win_rate"],
            "expectancy": cmp["expectancy"],
            "expectancy_ci": cmp["expectancy_ci"],
            "profit_factor": cmp["profit_factor"],
            "max_drawdown": cmp["max_drawdown"],
            "survival": account_survival(sub, lim["daily_loss"], lim["max_dd"]),
        }
    return out


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


def _diagnostics_md(r: dict[str, Any]) -> list[str]:
    out: list[str] = ["", "## Why candidates fail", ""]
    d = r.get("rr_diagnostics") or {}
    out += [
        f"- TP1 net R:R rejections: {d.get('rejected_for_rr')}; "
        f"median stop distance {d.get('median_risk_atr5')} x ATR(5m), "
        f"median TP1 distance {d.get('median_tp1_distance_atr5')} x ATR(5m).",
        f"- Gross R:R met the minimum but costs pushed net below it: {d.get('gross_rr_ok_but_net_fails_after_costs')}; "
        f"TP2 would have met the minimum: {d.get('tp2_would_meet_minimum')}.",
        f"- TP1 basis of rejected plans: {d.get('tp1_basis')}",
        f"- Stop basis of rejected plans: {d.get('stop_basis')}",
        f"- Higher-timeframe bias outcomes: {r.get('htf_bias_fail_reasons')}",
        f"- By strategy family: {r.get('by_family')}",
        "",
        "## Survival, dependence on large winners, cost sensitivity (risk-eligible, one at a time)",
        "",
    ]
    for k, t in (r.get("per_tier") or {}).items():
        sv, wd, cs = t["survival"], t["winner_dependence"], t["cost_sensitivity"]
        out.append(
            f"- {k}: account {'TERMINATED at ' + str(sv['terminated_at']) if sv['terminated'] else 'not terminated'} "
            f"({sv['trades_taken']} trades taken, {sv['trades_after_termination']} after termination not traded, "
            f"{sv['trades_skipped_by_daily_limit']} skipped by the daily limit, net ${sv['net_before_end_usd']}); "
            f"largest win share of gross profit {wd.get('top1_share_of_gross_profit')}, mean without largest win "
            f"${wd.get('mean_without_largest_win')}, without 3 largest ${wd.get('mean_without_3_largest_wins')}; "
            f"mean $/trade +1 tick each side ${cs.get('plus_1_tick_slippage_each_side')}, double commission "
            f"${cs.get('double_commission')}."
        )
    reg = r["views"]["risk_eligible_one_position"]["tiers"]["3/5 loose (research only)"].get("by_regime") or {}
    if reg:
        out.append(
            "- 3/5 by regime: " + ", ".join(f"{g} {v['trades']} trades ${v['expectancy']}" for g, v in reg.items())
        )
    out += [
        "",
        "## Experimental hypotheses (same signals, same outcome rules; NOT used live)",
        "",
        "| Variant | Tier | Signals | Resolved | Win rate (95% CI) | $/trade (95% CI) | PF | Max DD | Survival |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, ex in (r.get("experiments") or {}).items():
        for k, t in ex["risk_eligible_one_position"].items():
            tier = k.split(" ")[0]
            sv = ex["per_tier"].get(tier, {}).get("survival", {})
            out.append(
                f"| {name} | {k} | {t['signals']} | {t['resolved']} | {t['win_rate']} {t['win_rate_ci95']} | "
                f"{t['expectancy']} {t['expectancy_ci']} | {t['profit_factor']} | {t['max_drawdown']} | "
                f"{'terminated' if sv.get('terminated') else 'survived'} |"
            )
    return out


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
    lines += _diagnostics_md(r)
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
