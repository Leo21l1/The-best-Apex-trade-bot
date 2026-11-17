"""Controlled, read-only tools the AI copilot may call.

Every tool reads application data through the same services the dashboard uses and returns JSON
that labels what kind of information it is:

* ``verified_app_data``  - values stored or computed deterministically by PROJECT APEX
* ``statistical_estimate`` - derived metrics (scores, indicators); not facts about the future
* ``unavailable``        - the data does not exist; the model must say so rather than fill gaps

No tool can place, modify or cancel orders, change settings, or read credentials. Account data is
limited to balances and rule figures needed to answer risk questions.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.redis import get_redis
from app.marketdata.instruments import INSTRUMENTS, get_instrument
from app.marketdata.status import read_feed_status
from app.marketdata.store import latest_closes, query_bars
from app.marketdata.types import Timeframe
from app.models.calendar import EconomicEvent
from app.models.signal import AlertRow, SignalRow
from app.research.scanner import SCAN_KEY
from app.research.validation import STANDARDS, strategy_validation
from app.risk.engine import check_trade
from app.risk.profile import load_rules
from app.services.account import current_state
from app.services.calendar import event_to_dict, events_between
from app.strategies.library import STRATEGIES, STRATEGY_BY_ID


@dataclass
class ToolContext:
    session: AsyncSession
    settings: Settings
    now: datetime


def _num(x: Any) -> Any:
    if isinstance(x, Decimal):
        return float(x)
    if isinstance(x, datetime):
        return x.isoformat()
    if isinstance(x, dict):
        return {k: _num(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_num(v) for v in x]
    return x


def _symbol(raw: Any) -> str:
    inst = get_instrument(str(raw or ""))
    if inst is None:
        raise ValueError(f"unknown symbol {raw!r}; supported: {', '.join(INSTRUMENTS)}")
    return inst.symbol


async def _feed(ctx: ToolContext) -> tuple[str | None, str]:
    """(provider, quality) of the data currently being served."""
    if ctx.settings.market_data_provider == "none":
        return None, "unavailable"
    doc = await read_feed_status()
    if not doc:
        return ctx.settings.market_data_provider, "unavailable"
    return ctx.settings.market_data_provider, str(doc.get("quality", "unavailable"))


# --- tool implementations -------------------------------------------------------------------


async def get_system_status(ctx: ToolContext, _: dict[str, Any]) -> dict[str, Any]:
    from app.services.health import system_status

    st = await system_status()
    return {
        "data_class": "verified_app_data",
        "execution_mode": st.execution_mode,
        "live_order_routing": st.live_order_routing,
        "market_data_provider": st.market_data_provider,
        "market_data_quality": st.market_data_quality,
        "components": [{"name": c.name, "state": c.state.value, "detail": c.detail} for c in st.components],
        "server_time": st.server_time.isoformat(),
    }


async def get_market_snapshot(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    provider, quality = await _feed(ctx)
    if provider is None:
        return {"data_class": "unavailable", "reason": "No market data provider configured. No prices exist."}
    symbols = [_symbol(s) for s in args.get("symbols") or ctx.settings.market_data_symbols]
    rows = await latest_closes(ctx.session, provider)
    items = []
    for s in symbols:
        r = rows.get(s)
        items.append(
            {
                "symbol": s,
                "last_close": float(r.close) if r else None,
                "bar_open_time": r.ts.isoformat() if r else None,
                "age_seconds": round((ctx.now - r.ts).total_seconds()) if r else None,
            }
        )
    return {
        "data_class": "verified_app_data",
        "provider": provider,
        "quality": quality,
        "note": "Latest stored 1-minute close per symbol."
        + (" SIMULATED prices: synthetic, not real market data." if quality == "simulated" else ""),
        "items": items,
    }


async def get_recent_bars(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    symbol = _symbol(args.get("symbol"))
    try:
        tf = Timeframe(str(args.get("timeframe", "5m")))
    except ValueError as exc:
        raise ValueError("timeframe must be one of " + ", ".join(t.value for t in Timeframe)) from exc
    limit = max(1, min(int(args.get("limit", 24)), 60))
    provider, quality = await _feed(ctx)
    if provider is None:
        return {"data_class": "unavailable", "reason": "No market data provider configured."}
    bars = await query_bars(ctx.session, provider=provider, symbol=symbol, timeframe=tf, limit=limit)
    return {
        "data_class": "verified_app_data",
        "provider": provider,
        "quality": quality,
        "symbol": symbol,
        "timeframe": tf.value,
        "times": "bar open time, UTC",
        "bars": [
            {
                "t": b.ts.isoformat(),
                "o": float(b.open),
                "h": float(b.high),
                "l": float(b.low),
                "c": float(b.close),
                "v": b.volume,
            }
            for b in bars
        ],
    }


async def _scan() -> dict[str, Any] | None:
    raw = await get_redis().get(SCAN_KEY)
    return json.loads(raw) if raw else None


async def get_market_radar(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    scan = await _scan()
    if not scan or not scan.get("symbols"):
        return {"data_class": "unavailable", "reason": (scan or {}).get("note") or "No scan available."}
    wanted = {_symbol(s) for s in args.get("symbols") or []}
    out = []
    for s in scan["symbols"]:
        if wanted and s["symbol"] not in wanted:
            continue
        out.append(
            {
                "symbol": s["symbol"],
                "last": s["last"],
                "regime": s["regime"],
                "regime_definition": s["regime_definition"],
                "session": s["session"],
                "vwap": s["vwap"],
                "vs_vwap": s["vs_vwap"],
                "levels": s["levels"],
                "timeframes": [
                    {k: r[k] for k in ("timeframe", "trend", "rsi", "atr_pct", "rel_volume", "note")}
                    for r in s["timeframes"]
                ],
                "strategy_checks": [
                    {
                        "strategy_id": e["strategy_id"],
                        "triggered": e["triggered"],
                        "side": e["side"],
                        "passed": e["passed"],
                        "total": e["total"],
                        "note": e["note"],
                        "failed_conditions": [
                            c["name"] + ": " + c["detail"] for c in e["conditions"] if not c["passed"]
                        ],
                    }
                    for e in s["evaluations"]
                ],
            }
        )
    return {
        "data_class": "statistical_estimate",
        "as_of": scan["as_of"],
        "provider": scan["provider"],
        "quality": scan["quality"],
        "note": "Indicator readings on closed bars. Regimes and trends describe the past, not the future.",
        "symbols": out,
    }


async def get_opportunities(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    hours = max(1, min(int(args.get("hours", 24)), 168))
    q = select(SignalRow).where(
        SignalRow.provider == ctx.settings.market_data_provider,
        SignalRow.created_at >= ctx.now - timedelta(hours=hours),
    )
    if args.get("symbol"):
        q = q.where(SignalRow.symbol == _symbol(args["symbol"]))
    if args.get("status"):
        q = q.where(SignalRow.status == str(args["status"]))
    rows = (await ctx.session.scalars(q.order_by(SignalRow.created_at.desc()).limit(25))).all()
    items = []
    for r in rows:
        d = r.detail
        items.append(
            {
                "id": r.id,
                "status": r.status,
                "symbol": r.symbol,
                "strategy_id": r.strategy_id,
                "direction": r.direction,
                "triggered_bar": d["bar_ts"],
                "quality": d["quality"],
                "entry": d["entry"],
                "stop": d["stop"],
                "targets": d["targets"],
                "reward_risk": d["reward_risk"],
                "loss_at_stop_after_costs_per_contract": d["loss_at_stop_after_costs"],
                "setup_score": d["score"],
                "score_breakdown": d["score_breakdown"],
                "conditions": d["conditions"],
                "rank_eligible": d["rank_eligible"],
                "rank_blockers": d["rank_blockers"],
                "validation": d["validation"],
            }
        )
    return {
        "data_class": "statistical_estimate",
        "note": "Research candidates as stored at detection time. Setup score is a checklist, NOT a probability. "
        "Ranking needs a validated strategy, live/delayed data, a verified rule profile and a passing risk check "
        "(see the Opportunity Leaderboard for current ranking).",
        "candidates": items,
    }


async def get_strategy_specs(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    sid = args.get("strategy_id")
    strategies = [STRATEGY_BY_ID[sid]] if sid in STRATEGY_BY_ID else list(STRATEGIES)
    if sid and sid not in STRATEGY_BY_ID:
        raise ValueError(f"unknown strategy_id; valid: {', '.join(STRATEGY_BY_ID)}")
    from app.services.research import load_validations

    validations = await load_validations(ctx.session)
    return {
        "data_class": "verified_app_data",
        "validation_standards": asdict(STANDARDS),
        "strategies": [
            {
                **asdict(s.spec),
                "eligible_instruments": list(s.spec.eligible_instruments),
                "validation": asdict(strategy_validation(s.spec.id, validations)),
            }
            for s in strategies
        ],
        "note": "Validation comes from the latest backtest on real historical data; simulated data never validates.",
    }


async def get_account_risk(ctx: ToolContext, _: dict[str, Any]) -> dict[str, Any]:
    rules, state = await current_state(ctx.session, ctx.now)
    if not state.known:
        return {
            "data_class": "unavailable",
            "reason": "No account snapshot entered. Account risk is UNKNOWN.",
            "rules_verified": rules.verified,
        }
    s = asdict(state)
    keep = (
        "snapshot_at",
        "snapshot_age_seconds",
        "stale",
        "source",
        "balance",
        "unrealized_pnl",
        "equity",
        "highest_eod_balance",
        "drawdown_threshold",
        "drawdown_room",
        "start_of_day_balance",
        "day_pnl",
        "daily_loss_remaining",
        "risk_budget",
        "profit_to_target",
        "target_reached",
        "drawdown_breached",
        "daily_loss_hit",
        "contracts_in_use",
        "warnings",
    )
    return {
        "data_class": "verified_app_data",
        "provenance": "Computed deterministically from balances the trader entered manually or imported; "
        "not confirmed by the broker.",
        "rules": _num({k: v for k, v in asdict(rules).items() if k != "profile"}),
        "account": _num({k: s[k] for k in keep}),
        "positions": _num([asdict(p) for p in state.positions]),
    }


async def check_trade_risk(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from app.api.routes.account import cost_model_for

    inst = INSTRUMENTS[_symbol(args.get("symbol"))]
    try:
        entry, stop = Decimal(str(args["entry"])), Decimal(str(args["stop"]))
        target = Decimal(str(args["target"])) if args.get("target") is not None else None
        qty = int(args.get("quantity", 1))
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise ValueError("entry and stop are required numbers; quantity an integer") from exc
    rules, state = await current_state(ctx.session, ctx.now)
    c = check_trade(
        rules,
        state,
        inst,
        str(args.get("direction")),
        entry,
        stop,
        target,
        qty,
        cost_model_for(inst.symbol),
        INSTRUMENTS,
        Decimal(str(ctx.settings.risk_max_trade_fraction)),
    )
    return {
        "data_class": "verified_app_data",
        "provenance": "Deterministic Risk Guardian calculation. Costs are configured assumptions.",
        "check": _num(asdict(c)),
    }


async def get_rule_profile(ctx: ToolContext, _: dict[str, Any]) -> dict[str, Any]:
    rows = await load_rules(ctx.session)
    return {
        "data_class": "verified_app_data",
        "note": "A rule is 'verified' only if a person recorded the official Apex source it was checked against.",
        "rules": [
            {
                "key": r.key,
                "label": r.label,
                "value": r.value,
                "verified": r.verified,
                "source_url": r.source_url,
                "version": r.version,
            }
            for r in rows
        ],
    }


async def get_economic_calendar(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    days = max(0, min(int(args.get("days_ahead", 1)), 14))
    back = max(0, min(int(args.get("hours_back", 6)), 72))
    events = await events_between(ctx.session, ctx.now - timedelta(hours=back), ctx.now + timedelta(days=days or 1))
    if not events:
        total = await ctx.session.scalar(select(func.count()).select_from(EconomicEvent))
        return {
            "data_class": "unavailable",
            "reason": "No calendar events in this window."
            + (" The calendar is empty: no official calendar has been imported." if not total else ""),
        }
    s = ctx.settings
    return {
        "data_class": "verified_app_data",
        "note": "Events from imported official calendars or manual entry. Importance is a fixed classification "
        "by event type, not a forecast. Apex news-trading restrictions are unverified.",
        "events": [
            event_to_dict(e, ctx.now, s.calendar_pre_event_minutes, s.calendar_post_event_minutes) for e in events
        ],
    }


async def get_recent_alerts(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    limit = max(1, min(int(args.get("limit", 15)), 50))
    rows = (await ctx.session.scalars(select(AlertRow).order_by(AlertRow.id.desc()).limit(limit))).all()
    return {
        "data_class": "verified_app_data",
        "alerts": [
            {
                "created_at": a.created_at.isoformat(),
                "kind": a.kind,
                "severity": a.severity,
                "title": a.title,
                "body": a.body,
            }
            for a in rows
        ],
    }


async def get_session_activity(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    hours = max(1, min(int(args.get("hours", 12)), 72))
    since = ctx.now - timedelta(hours=hours)
    res = await ctx.session.execute(
        select(SignalRow.strategy_id, SignalRow.status, func.count())
        .where(SignalRow.created_at >= since)
        .group_by(SignalRow.strategy_id, SignalRow.status)
    )
    by: dict[str, dict[str, int]] = {}
    for sid, status, n in res:
        by.setdefault(sid, {})[status] = int(n)
    alerts = await ctx.session.execute(
        select(AlertRow.kind, func.count()).where(AlertRow.created_at >= since).group_by(AlertRow.kind)
    )
    return {
        "data_class": "verified_app_data",
        "window_hours": hours,
        "signals_by_strategy_and_status": by,
        "alerts_by_kind": {k: int(n) for k, n in alerts},
        "executed_trades": None,
        "note": "Executed trades are unavailable: no trade import exists yet, so performance and behaviour "
        "cannot be assessed. Signal outcomes are hypothetical (stop or first target touched).",
    }


# --- registry -------------------------------------------------------------------------------

_SYMBOL = {"type": "string", "description": "Futures symbol, e.g. ES, MNQ, CL"}


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


TOOLS: dict[str, tuple[Any, dict[str, Any]]] = {
    "get_system_status": (
        get_system_status,
        {
            "description": "Execution mode, market-data provider and data class, and health of every component.",
            "input_schema": _schema({}),
        },
    ),
    "get_market_snapshot": (
        get_market_snapshot,
        {
            "description": "Latest stored 1-minute close for symbols, with provider, data class and bar time.",
            "input_schema": _schema({"symbols": {"type": "array", "items": _SYMBOL}}),
        },
    ),
    "get_recent_bars": (
        get_recent_bars,
        {
            "description": "Recent OHLCV bars (max 60) for one symbol and timeframe (1m,3m,5m,15m,30m,1h,4h,1d,1w).",
            "input_schema": _schema(
                {"symbol": _SYMBOL, "timeframe": {"type": "string"}, "limit": {"type": "integer"}}, ["symbol"]
            ),
        },
    ),
    "get_market_radar": (
        get_market_radar,
        {
            "description": "Latest scanner output: regime, VWAP, prior-day/overnight/opening-range levels, trend/RSI "
            "per timeframe, and every strategy's condition checks (including failed conditions).",
            "input_schema": _schema({"symbols": {"type": "array", "items": _SYMBOL}}),
        },
    ),
    "get_opportunities": (
        get_opportunities,
        {
            "description": "Recent strategy signals (research candidates) with entry/stop/targets, setup score "
            "breakdown, triggering conditions, status and why they are not ranked.",
            "input_schema": _schema(
                {
                    "hours": {"type": "integer"},
                    "symbol": _SYMBOL,
                    "status": {
                        "type": "string",
                        "enum": ["active", "invalidated", "target_reached", "expired"],
                    },
                }
            ),
        },
    ),
    "get_strategy_specs": (
        get_strategy_specs,
        {
            "description": "Rule specifications and validation status of strategies A-H (or one strategy_id).",
            "input_schema": _schema({"strategy_id": {"type": "string"}}),
        },
    ),
    "get_account_risk": (
        get_account_risk,
        {
            "description": "Apex account figures: balance, equity, EOD drawdown threshold and room, daily loss "
            "remaining, risk budget, contracts in use, staleness and warnings.",
            "input_schema": _schema({}),
        },
    ),
    "check_trade_risk": (
        check_trade_risk,
        {
            "description": "Deterministic Risk Guardian check of a proposed manual trade: dollar risk, max size, "
            "reward:risk, loss after costs, budget consumed, rule violations. Use for ANY risk number.",
            "input_schema": _schema(
                {
                    "symbol": _SYMBOL,
                    "direction": {"type": "string", "enum": ["long", "short"]},
                    "entry": {"type": "number"},
                    "stop": {"type": "number"},
                    "target": {"type": "number"},
                    "quantity": {"type": "integer"},
                },
                ["symbol", "direction", "entry", "stop"],
            ),
        },
    ),
    "get_rule_profile": (
        get_rule_profile,
        {
            "description": "Configured Apex rule values and whether each has been verified against an official source.",
            "input_schema": _schema({}),
        },
    ),
    "get_economic_calendar": (
        get_economic_calendar,
        {
            "description": "Imported economic events (CPI, FOMC, NFP, ...) near now, with affected markets and "
            "whether now is inside the configured alert window.",
            "input_schema": _schema({"days_ahead": {"type": "integer"}, "hours_back": {"type": "integer"}}),
        },
    ),
    "get_recent_alerts": (
        get_recent_alerts,
        {
            "description": "Most recent alerts (opportunities, invalidations, regime changes, feed, account, risk).",
            "input_schema": _schema({"limit": {"type": "integer"}}),
        },
    ),
    "get_session_activity": (
        get_session_activity,
        {
            "description": "Signal counts by strategy and outcome and alert counts over recent hours. Executed trades "
            "are not available yet.",
            "input_schema": _schema({"hours": {"type": "integer"}}),
        },
    ),
}


def tool_definitions() -> list[dict[str, Any]]:
    """Tool list in a fixed order (stable for prompt caching)."""
    return [{"name": name, **spec} for name, (_, spec) in TOOLS.items()]


async def run_tool(ctx: ToolContext, name: str, args: Any) -> tuple[dict[str, Any], bool]:
    """Execute a tool. Returns (result, is_error). Unknown tools and bad input become error results."""
    entry = TOOLS.get(name)
    if entry is None:
        return {"error": f"unknown tool {name!r}"}, True
    if not isinstance(args, dict):
        return {"error": "tool input must be an object"}, True
    try:
        return await entry[0](ctx, args), False
    except (ValueError, TypeError, KeyError) as exc:
        return {"error": str(exc)}, True


def now_utc() -> datetime:
    return datetime.now(UTC)
