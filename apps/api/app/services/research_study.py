"""Run, store and export research studies (see app.research.study). Read-only on market data."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.marketdata.instruments import INSTRUMENTS
from app.models.research import ResearchStudy
from app.research.scanner import load_bars
from app.research.study import run_full_study
from app.risk.profile import load_rules, to_ruleset

TRADE_COLUMNS = (
    "symbol",
    "contract",
    "strategy_id",
    "config",
    "period",
    "direction",
    "signal_ts",
    "entry_ts",
    "entry",
    "stop",
    "target",
    "exit_ts",
    "exit",
    "exit_reason",
    "quantity",
    "gross_pnl",
    "commission",
    "slippage_usd",
    "pnl",
    "risk_usd",
    "r",
    "mfe_usd",
    "mae_usd",
    "session",
    "hour_et",
    "weekday",
    "regime",
    "rvol",
    "stop_ticks",
    "planned_rr",
    "trading_day",
    "bars_held",
)
LEADER_COLUMNS = (
    "strategy_id",
    "config",
    "direction",
    "period",
    "trades",
    "wins",
    "losses",
    "breakeven",
    "win_rate",
    "win_rate_ci95_low",
    "win_rate_ci95_high",
    "expectancy",
    "expectancy_ci_low",
    "expectancy_ci_high",
    "median_pnl",
    "avg_win",
    "avg_loss",
    "avg_r",
    "profit_factor",
    "gross_profit",
    "gross_loss",
    "net_pnl",
    "max_drawdown",
    "max_consecutive_losses",
    "trades_per_session",
    "profitable_sessions_pct",
    "avg_daily_pnl",
    "costs_pct_of_gross_profit",
    "sharpe_daily",
    "evidence",
)


async def _provenance(session: AsyncSession, provider: str, symbol: str, a: datetime, b: datetime) -> dict[str, int]:
    rows = await session.execute(
        text(
            "SELECT quality, count(*) FROM candles WHERE provider = :p AND symbol = :s AND ts >= :a AND ts < :b "
            "GROUP BY quality"
        ),
        {"p": provider, "s": symbol, "a": a, "b": b},
    )
    return {f"{provider}/{r[0]}": int(r[1]) for r in rows}


async def run_and_store(
    sm: Any,
    symbol: str,
    days: int,
    strategies: Sequence[str] | None,
    workers: int,
    progress: Callable[[str], None] | None = None,
    contract_map: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    s = get_settings()
    now = datetime.now(UTC)
    start = now - timedelta(days=days)
    async with sm() as session:
        bars = await load_bars(session, s.market_data_provider, symbol, start, now)
        prov = await _provenance(session, s.market_data_provider, symbol, start, now)
        rule_rows = await load_rules(session)
        rules = to_ruleset(rule_rows)
        await session.commit()
    rules_provenance = [
        {
            "key": r.key,
            "label": r.label,
            "value": r.value,
            "required": r.required,
            "verified": r.verified,
            "source_url": r.source_url,
            "verified_at": r.verified_at.isoformat() if r.verified_at else None,
            "applies_to": r.applies_to,
            "version": r.version,
        }
        for r in rule_rows
    ]
    inst = INSTRUMENTS[symbol]
    commission = s.cost_commission_per_side_micro if inst.is_micro else s.cost_commission_per_side_standard
    quality = "simulated" if s.market_data_provider == "simulated" else "historical"
    res = run_full_study(
        symbol,
        bars,
        quality,
        rules,
        commission,
        s.cost_slippage_ticks_per_side,
        strategies,
        workers=workers,
        progress=progress,
        requested=(start, now),
        provenance=prov,
        rules_provenance=rules_provenance,
        contract_map=contract_map,
    )
    trades = res.pop("trade_log")
    params = {
        "days": days,
        "strategies": list(strategies or []),
        "workers": workers,
        "commission_per_side": commission,
        "slippage_ticks": s.cost_slippage_ticks_per_side,
        "min_net_reward_risk": s.min_net_reward_risk,
        "contract_map": bool(contract_map),
    }
    async with sm() as session:
        row = ResearchStudy(
            symbol=symbol,
            provider=s.market_data_provider,
            data_class=res["data_class"],
            params=params,
            result=res,
            trades=trades,
        )
        session.add(row)
        await session.commit()
        sid = row.id
    res["trade_log"] = trades
    return sid, res


def leaderboard_rows(res: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in res["rows"]:
        for period in ("research", "walk_forward", "holdout", "all_periods"):
            for d, st in r[period].items():
                wci, eci = st.get("win_rate_ci95") or [None, None], st.get("expectancy_ci") or [None, None]
                out.append(
                    {
                        **{k: st.get(k) for k in LEADER_COLUMNS if k in st},
                        "strategy_id": r["strategy_id"],
                        "config": r["config"],
                        "direction": d,
                        "period": period,
                        "win_rate_ci95_low": wci[0],
                        "win_rate_ci95_high": wci[1],
                        "expectancy_ci_low": eci[0],
                        "expectancy_ci_high": eci[1],
                        "evidence": r["evidence"],
                    }
                )
    return out


def to_csv(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def headline(res: dict[str, Any]) -> dict[str, Any]:
    """Statements only when backed by completed analysis on real data; otherwise say why not."""
    sym = res["symbol"]
    if res["data_class"] == "SIMULATED":
        return {"available": False, "reason": "Simulated data: no findings."}
    rk = res["rankings"]
    wr = [x for x in rk.get("win_rate_all", []) if not x["small_sample"]]
    ra = [x for x in rk.get("risk_adjusted_all", []) if not x["small_sample"]]
    out: dict[str, Any] = {"available": bool(wr or ra), "preliminary": res["plan"]["preliminary"]}
    if wr:
        w = wr[0]
        out["highest_win_rate"] = (
            f"Highest observed out-of-sample win rate - {sym}: {w['strategy_id']} "
            f"({w['config']}), {w['win_rate']:.1%} over {w['trades']} trades "
            f"(95% CI {w['win_rate_ci95'][0]:.1%}-{w['win_rate_ci95'][1]:.1%})"
        )
    if ra:
        e = ra[0]
        out["strongest_expectancy"] = (
            f"Strongest out-of-sample expectancy - {sym}: {e['strategy_id']} "
            f"({e['config']}), ${e['expectancy']} per trade over {e['trades']} trades "
            f"(CI {e['expectancy_ci']}) - evidence: {e['evidence']}"
        )
    if not out["available"]:
        out["reason"] = "No configuration has >= 30 out-of-sample trades."
    return out


def report_md(res: dict[str, Any]) -> str:
    d, p = res["data"], res["plan"]
    lines = [
        f"# Research study - {res['symbol']} ({res['data_class']})",
        "",
        f"Generated {res['generated_at']}. Bars {d.get('bars')}, {d.get('earliest_bar')} to {d.get('latest_bar')}; "
        f"usable sessions {d.get('usable_sessions')} (verdict {d.get('verdict')}).",
        f"Periods: {p['rule']}.",
        f"Roll exclusions: {len(res['rollover']['excluded_sessions'])} sessions; segments: "
        + ", ".join(f"{s['contract']} ({s['bars']} bars)" for s in res["rollover"]["segments"]),
        f"Costs: {res['costs']['commission_per_side']} USD/side commission, {res['costs']['slippage_ticks_per_side']} "
        f"tick slippage; {res['comparisons']} strategy x configuration comparisons.",
        "",
        "## Headline",
    ]
    h = headline(res)
    lines += [f"- {h[k]}" for k in ("highest_win_rate", "strongest_expectancy") if k in h] or [f"- {h.get('reason')}"]
    lines += [
        "",
        "## Out-of-sample (walk-forward) results, all directions",
        "",
        "| strategy | config | trades | win rate (95% CI) | $/trade (CI) | PF | max DD | 2-tick $/trade | "
        "Apex pass (resolved) | evidence |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(res["rows"], key=lambda r: -(r["walk_forward"]["all"]["trades"] or 0)):
        w = r["walk_forward"]["all"]
        ap = r["apex_replay"]
        lines.append(
            f"| {r['strategy_id']} | {r['config']} | {w['trades']} | {w['win_rate']} {w['win_rate_ci95']} | "
            f"{w['expectancy']} {w['expectancy_ci']} | {w['profit_factor']} | {w['max_drawdown']} | "
            f"{r['stress']['slip2']['expectancy']} | {ap['pass_rate']} ({ap['resolved']}) | {r['evidence']} |"
        )
    lines += ["", "## Definitions", ""] + [f"- **{k}**: {v}" for k, v in res["definitions"].items()]
    lines += [
        "",
        "Apex rules: " + (res["rows"][0]["apex_replay"]["rules"]["label"] if res["rows"] else "n/a"),
        "",
        "Not financial advice. Past results describe the tested period only.",
    ]
    return "\n".join(lines) + "\n"


def write_artifacts(res: dict[str, Any], out_dir: str) -> list[str]:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    sym = res["symbol"]
    files = {
        f"study_{sym}.json": json.dumps({k: v for k, v in res.items() if k != "trade_log"}, indent=1, default=str),
        f"leaderboard_{sym}.csv": to_csv(leaderboard_rows(res), LEADER_COLUMNS),
        f"trades_{sym}.csv": to_csv(res.get("trade_log", []), TRADE_COLUMNS),
        f"report_{sym}.md": report_md(res),
    }
    for name, content in files.items():
        (root / name).write_text(content)
    return [str(root / n) for n in files]


async def latest(session: AsyncSession, symbol: str | None = None) -> ResearchStudy | None:
    q = select(ResearchStudy).order_by(ResearchStudy.created_at.desc()).limit(1)
    if symbol:
        q = q.where(ResearchStudy.symbol == symbol)
    return await session.scalar(q)
