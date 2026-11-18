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


def run(
    symbol: str,
    m1: Sequence[Bar],
    d1: Sequence[Bar],
    *,
    holdout_sessions: int = 50,
    strategies: Sequence[str] | None = None,
    events: Sequence[tuple[datetime, str]] = (),
    cfg: setups.SetupConfig | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    cfg = cfg or setups.SetupConfig()
    sessions = rth_sessions(m1)
    dev = sessions[:-holdout_sessions] if holdout_sessions and len(sessions) > holdout_sessions else []
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
    funnel: dict[str, int] = {"signals": 0, "no levels / R:R": 0, "mandatory failed": 0, "recorded": 0}
    for sid in ids:
        _, evs = generate_signals(BacktestParams(strategy_id=sid, symbol=symbol), dev_bars)
        for ev in evs:
            funnel["signals"] += 1
            t = ev.close_ts
            lo = bisect.bisect_left(ts_list, t - timedelta(days=analyzer.LOOKBACK_DAYS))
            hi = bisect.bisect_left(ts_list, t)
            window = dev_bars[lo:hi]
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
            cand = next((c for c in doc["signal"]["candidates"]), None)
            if cand is None:
                continue
            reqs = {r["key"]: r["ok"] for r in cand["requirements"]}
            if not (reqs["stop"] and reqs["target"] and reqs["net R:R"] and reqs["entry zone"]):
                funnel["no levels / R:R"] += 1
                continue
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
    cmp = signal_tracker.compare_thresholds(items, counts)
    return {
        "symbol": symbol,
        "generated_at": datetime.now(UTC).isoformat(),
        "engine_version": setups.ENGINE_VERSION,
        "period": {
            "first": dev[0],
            "last": last_dev,
            "sessions": len(dev),
            "excluded_holdout_sessions": min(holdout_sessions, len(sessions)),
            "holdout_starts": sessions[len(dev)] if len(sessions) > len(dev) else None,
        },
        "funnel": funnel,
        "score_counts": counts,
        "status_counts": {
            k: sum(1 for x in items if x["status"] == k) for k in ("CONFIRMED", "EXPIRED", "MISSED", "INVALIDATED")
        },
        "comparison": cmp,
        "notes": [
            "Development period only; the final holdout is excluded.",
            "Calendar/data gates are not reconstructed for history; news exposure is a breakdown, not a filter.",
            "Do not change the live threshold from this study alone.",
        ],
    }


def to_markdown(r: dict[str, Any]) -> str:
    if "error" in r:
        return f"# Confluence threshold study - {r['symbol']}\n\n{r['error']}\n"
    lines = [
        f"# Confluence threshold study - {r['symbol']}",
        "",
        f"Development sessions {r['period']['first']} .. {r['period']['last']} ({r['period']['sessions']}); "
        f"final holdout excluded ({r['period']['excluded_holdout_sessions']} sessions).",
        "",
        f"Funnel: {r['funnel']}",
        f"Scores: {r['score_counts']}",
        "",
        "| Threshold | Resolved | Win rate (95% CI) | Avg win | Avg loss | $/trade (95% CI) | PF | Max DD "
        "| Max loss streak | Avg R |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, t in r["comparison"]["tiers"].items():
        lines.append(
            f"| {name} | {t['resolved']} | {t['win_rate']} {t['win_rate_ci95']} | {t['avg_win']} | {t['avg_loss']} | "
            f"{t['expectancy']} {t['expectancy_ci']} | {t['profit_factor']} | {t['max_drawdown']} | "
            f"{t['max_consecutive_losses']} | {t['avg_r']} |"
        )
    lines += [
        "",
        f"**Verdict:** {r['comparison']['verdict']}",
        "",
        r["comparison"]["method"],
        "",
        r["comparison"]["caveat"],
    ]
    return "\n".join(lines) + "\n"
