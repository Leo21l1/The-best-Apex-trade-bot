"""Evaluation-account replay: how an Apex-style evaluation would have gone trading these trades.

Rules come from the app's rule profile (Apex Account Health). They are reported with their
verification state; an unverified profile is labelled UNVERIFIED in every result.

Model (1 contract per trade, trades in time order):
* End-of-day trailing drawdown: threshold = highest end-of-day balance - max drawdown (capped at the
  configured stop level, if any) - the Risk Guardian's own ``drawdown_threshold``.
* The threshold is checked INTRADAY against equity: each trade's maximum adverse excursion (from
  1-minute highs/lows) is applied before its result, which is conservative.
* Daily loss limit: treated as a DAY STOP (no further trades that day), not an account failure,
  unless ``daily_loss_is_breach`` is set. Whether the provider enforces it is part of the rule
  profile's verification, not assumed here.
* An attempt PASSES when the balance reaches start + profit target, FAILS on a drawdown breach, and
  is UNRESOLVED when the data ends first. Attempts are consecutive and non-overlapping (a new
  account starts the session after the previous attempt resolved), so attempts are independent of
  each other's paths and the pass rate's Wilson interval is honest about sample size.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from app.research.stats import wilson
from app.risk.engine import RuleSet, drawdown_threshold


def replay(
    trades: Sequence[Any],
    rules: RuleSet,
    sessions: Sequence[str],
    daily_loss_is_breach: bool = False,
    max_contracts_ok: bool = True,
) -> dict[str, Any]:
    by_day: dict[str, list[Any]] = {}
    for t in trades:
        by_day.setdefault(t.trading_day, []).append(t)
    start = float(rules.starting_balance)
    target = start + float(rules.profit_target)
    dll = float(rules.daily_loss_limit) if rules.daily_loss_limit else None
    attempts: list[dict[str, Any]] = []
    i = 0
    days = list(sessions)
    while i < len(days):
        bal = start
        eod: list[Decimal] = []
        result, reason, j = "unresolved", "data ended", i
        worst_day = 0.0
        day_stops = 0
        for j in range(i, len(days)):
            d = days[j]
            thr = float(drawdown_threshold(rules, eod))
            day_pnl = 0.0
            for t in by_day.get(d, []):
                if dll is not None and day_pnl <= -dll:
                    day_stops += 1
                    break
                if bal - t.mae_usd <= thr:  # intraday equity touched the threshold
                    result, reason = "fail", f"trailing drawdown touched intraday on {d}"
                    break
                bal += t.pnl
                day_pnl += t.pnl
                if dll is not None and day_pnl <= -dll and daily_loss_is_breach:
                    result, reason = "fail", f"daily loss limit on {d}"
                    break
                if bal >= target:
                    result, reason = "pass", f"profit target reached on {d}"
                    break
            worst_day = min(worst_day, day_pnl)
            if result != "unresolved":
                break
            eod.append(Decimal(str(round(bal, 2))))
            if bal <= float(drawdown_threshold(rules, eod)):
                result, reason = "fail", f"end-of-day balance at the threshold on {d}"
                break
        attempts.append(
            {
                "start": days[i],
                "end": days[j],
                "trading_days": j - i + 1,
                "result": result,
                "reason": reason,
                "final_balance": round(bal, 2),
                "worst_day": round(worst_day, 2),
                "day_stops": day_stops,
            }
        )
        if result == "unresolved":
            break
        i = j + 1
    resolved = [a for a in attempts if a["result"] != "unresolved"]
    passes = sum(1 for a in resolved if a["result"] == "pass")
    ci = wilson(passes, len(resolved))
    days_to_pass = sorted(a["trading_days"] for a in resolved if a["result"] == "pass")
    return {
        "rules": {
            "profile": rules.profile,
            "starting_balance": start,
            "profit_target": float(rules.profit_target),
            "max_drawdown": float(rules.max_drawdown),
            "daily_loss_limit": dll,
            "daily_loss_treated_as": "account failure" if daily_loss_is_breach else "day stop",
            "max_contracts": float(rules.max_contracts),
            "verified": rules.verified,
            "label": "VERIFIED" if rules.verified else "UNVERIFIED rule profile",
        },
        "attempts": len(attempts),
        "resolved": len(resolved),
        "passes": passes,
        "fails": len(resolved) - passes,
        "pass_rate": round(passes / len(resolved), 4) if resolved else None,
        "pass_rate_ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
        "median_days_to_pass": days_to_pass[len(days_to_pass) // 2] if days_to_pass else None,
        "contracts_per_trade": 1,
        "contract_limit_respected": max_contracts_ok,
        "detail": attempts[:200],
        "note": "Consecutive, non-overlapping attempts on out-of-sample trades; few resolved attempts means "
        "the pass rate is very uncertain (see the interval). Not a guarantee of any future evaluation.",
    }
