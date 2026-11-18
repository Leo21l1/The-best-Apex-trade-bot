"""Autopilot: autonomous paper trading on live data, and the AUTHORIZED_BROKER gate.

Modes (``APEX_AUTOMATION_MODE``):
* APEX_MANUAL (default) - the scanner and alerts run; this module never creates any order.
* AUTONOMOUS_PAPER - trades a SEPARATE simulated account named "Autonomous paper" (the manual paper
  account and the journal are untouched). Every order, fill and exit is simulated and labelled.
* AUTHORIZED_BROKER - real execution on a non-Apex brokerage account. Refused unless every gate in
  ``app.execution.broker.broker_gate`` passes; no broker adapter exists, so it is always refused.

Each cycle (run by the worker): halts first (kill switch, live feed not streaming, stale data,
account halted), then lifecycle notifications, then new signals. A signal is traded only if its
(instrument, strategy) is pre-registered in ``APEX_AUTOPILOT_STRATEGIES`` with a research
configuration, and it passes: freshness, news window, no position or working entry in the same
correlation group, the configuration's own net R:R gate, and per-trade / daily risk limits. Size is
fixed (``APEX_AUTOPILOT_CONTRACTS``): never increased after losses. Each signal is evaluated once.
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import log_event
from app.core.redis import get_redis
from app.marketdata.instruments import INSTRUMENTS
from app.marketdata.status import read_feed_status
from app.models.research import PaperAccount
from app.models.signal import SignalRow
from app.paper.engine import PaperBook, new_state
from app.research.backtest import BacktestParams, net_rr
from app.research.rr_study import CONFIGS
from app.risk.engine import correlation_group
from app.services.alerts import emit_alert
from app.services.session_intel import market_status

log = logging.getLogger("apex.autopilot")
AUTO_ACCOUNT = "Autonomous paper"
KILL_KEY = "apex:autopilot:kill"
SEEN_KEY = "apex:autopilot:seen"
STATUS_KEY = "apex:autopilot:status"
STALE_S = 180
CFG = {c.key: c for c in CONFIGS}


def allowlist(entries: list[str]) -> dict[tuple[str, str], Any]:
    """'ES:mtf_trend:t1_all' -> {('ES', 'mtf_trend'): RRConfig}. Unknown entries are ignored (and reported)."""
    from app.strategies.library import STRATEGY_BY_ID

    out = {}
    for e in entries:
        parts = [x.strip() for x in e.split(":")]
        if len(parts) == 3 and parts[0] in INSTRUMENTS and parts[1] in STRATEGY_BY_ID and parts[2] in CFG:
            out[(parts[0], parts[1])] = CFG[parts[2]]
    return out


async def auto_account(session: AsyncSession, settings: Settings) -> PaperAccount:
    from app.services.paper import config_from_rules

    acct = await session.scalar(
        select(PaperAccount).where(
            PaperAccount.name == AUTO_ACCOUNT, PaperAccount.provider == settings.market_data_provider
        )
    )
    if acct is None:
        acct = PaperAccount(
            name=AUTO_ACCOUNT,
            provider=settings.market_data_provider,
            state=new_state(await config_from_rules(session, settings)),
            cursor={},
        )
        session.add(acct)
        await session.flush()
    return acct


async def kill(on: bool, actor: str) -> None:
    r = get_redis()
    if on:
        await r.set(KILL_KEY, json.dumps({"at": datetime.now(UTC).isoformat(), "by": actor}))
    else:
        await r.delete(KILL_KEY)


async def killed() -> dict[str, Any] | None:
    try:
        raw = await get_redis().get(KILL_KEY)
    except (RedisError, OSError):
        return {"at": None, "by": "redis unavailable - treated as halted"}
    return json.loads(raw) if raw else None


async def _alert(
    session: AsyncSession,
    kind: str,
    severity: str,
    title: str,
    body: str,
    key: str,
    cooldown: int = 0,
    symbol: str | None = None,
) -> None:
    await emit_alert(
        session,
        get_redis(),
        kind=kind,
        severity=severity,
        title=title,
        body=body,
        dedupe_key=key,
        cooldown_s=cooldown,
        symbol=symbol,
    )


def _feed_halts(
    settings: Settings, feed: dict[str, Any], latest: dict[str, datetime | None], now: datetime, symbols: set[str]
) -> dict[str, str]:
    """symbol -> reason no NEW entry may be opened ('*' = all)."""
    h: dict[str, str] = {}
    if settings.market_data_provider == "databento" and (
        feed.get("state") != "streaming" or (feed.get("quality") or "").lower() != "live"
    ):
        h["*"] = f"live feed not streaming (state {feed.get('state', 'not reporting')})"
    for sym in symbols:
        if not market_status(sym, now)["open"]:
            h[sym] = "market closed"
            continue
        ts = latest.get(sym)
        if ts is None or (now - (ts + timedelta(minutes=1))).total_seconds() > STALE_S:
            h[sym] = "market data stale or missing"
    return h


async def _lifecycle(session: AsyncSession, acct: PaperAccount, book: PaperBook) -> None:
    """Notify new simulated fills and closed trades since the last cycle (labelled SIMULATED)."""
    seen = book.s.setdefault("auto_seen", {"fills": 0, "closed": 0})
    for f in book.s["fills"][seen["fills"] :]:
        await _alert(
            session,
            "auto_trade",
            "info",
            f"[SIMULATED] Fill {f.get('side', '')} {f.get('qty', '')} {f.get('symbol', '')} @ {f.get('price', '')}",
            json.dumps(f, default=str)[:900],
            f"auto:fill:{acct.id}:{f.get('id', id(f))}",
            symbol=f.get("symbol"),
        )
    for c in book.s["closed"][seen["closed"] :]:
        net = round(c["gross_pnl"] - c["fees"], 2)
        await _alert(
            session,
            "auto_trade",
            "info",
            f"[SIMULATED] Closed {c['direction']} {c['qty']} {c['symbol']}: {'+' if net >= 0 else ''}${net}",
            f"entry {c['entry_price']} exit {c['exit_price']} ({c.get('exit_reason', c.get('reason', 'exit'))})",
            f"auto:closed:{acct.id}:{c['id']}",
            symbol=c["symbol"],
        )
    seen["fills"], seen["closed"] = len(book.s["fills"]), len(book.s["closed"])


async def cycle(session: AsyncSession, now: datetime | None = None) -> dict[str, Any]:
    from app.services.calendar import news_exposure

    s = get_settings()
    now = now or datetime.now(UTC)
    status: dict[str, Any] = {"mode": s.automation_mode, "at": now.isoformat(), "orders": [], "rejected": []}
    if s.automation_mode == "APEX_MANUAL":
        status["note"] = "manual mode: scanning and alerts only, no orders"
        return status
    if s.automation_mode == "AUTHORIZED_BROKER":
        from app.execution.broker import broker_gate
        from app.risk.profile import load_rules, to_ruleset

        rules = to_ruleset(await load_rules(session))
        reasons = broker_gate(s, rules.profile, await forward_pnls(session, s))
        status["broker_gate"] = reasons
        if reasons:
            await _alert(
                session,
                "broker_gate",
                "critical",
                "Real-money automation REFUSED",
                "; ".join(reasons)[:900],
                "auto:broker_gate",
                cooldown=6 * 3600,
            )
            status["note"] = "AUTHORIZED_BROKER refused: no orders of any kind"
            return status
    acct = await auto_account(session, s)
    book = PaperBook(copy.deepcopy(acct.state))
    allowed = allowlist(s.autopilot_strategies)
    symbols = {sym for sym, _ in allowed}
    k = await killed()
    if k:
        from app.paper.engine import OrderRejected

        working = [o for o in book.s["orders"] if o["status"] in ("working", "pending", "partially_filled")]
        for o in working:
            if o["status"] in ("working", "pending", "partially_filled"):  # an entry's cancel also cancels its children
                try:
                    book.cancel(o["id"], now)
                except OrderRejected:
                    pass
        if book.s["positions"]:
            book.flatten(now)
        if working or book.s.get("kill_ack") != k.get("at"):
            await _alert(
                session,
                "kill_switch",
                "critical",
                "[SIMULATED] Autopilot halted by kill switch",
                f"Cancelled {len(working)} orders and flattened simulated positions. Set by {k.get('by')}.",
                f"auto:kill:{k.get('at')}",
            )
            book.s["kill_ack"] = k.get("at")
        status["halted"] = {"*": "kill switch"}
    else:
        feed = await read_feed_status() or {}
        latest: dict[str, datetime | None] = {}
        for sym in symbols:
            ts = (
                await session.execute(
                    text("SELECT max(ts) FROM candles WHERE provider = :p AND symbol = :s"),
                    {"p": s.market_data_provider, "s": sym},
                )
            ).scalar()
            latest[sym] = ts
        halts = _feed_halts(s, feed, latest, now, symbols)
        if book.s["status"] != "active":
            halts["*"] = f"account {book.s['status']} (daily loss or drawdown limit)"
        status["halted"] = halts
        if "*" in halts and halts["*"] != "market closed":
            await _alert(
                session,
                "auto_halt",
                "warning",
                "[SIMULATED] Autopilot paused: no new entries",
                halts["*"],
                f"auto:halt:{halts['*'][:40]}",
                cooldown=1800,
            )
        cutoff = now - timedelta(minutes=s.autopilot_signal_max_age_minutes)
        rows = (
            await session.scalars(
                select(SignalRow)
                .where(
                    SignalRow.provider == s.market_data_provider,
                    SignalRow.status == "active",
                    SignalRow.created_at >= cutoff,
                    SignalRow.created_at <= now,
                )
                .order_by(SignalRow.created_at)
            )
        ).all()
        r = get_redis()
        for sig in rows:
            key = (sig.symbol, sig.strategy_id)
            if key not in allowed or not await r.sadd(SEEN_KEY, str(sig.id)):
                continue  # not pre-registered, or already evaluated once (no duplicate orders)
            cfg = allowed[key]
            label = f"{sig.symbol} {sig.direction} {sig.strategy_id} ({cfg.key})"
            reason = _reject_reason(
                s,
                book,
                sig,
                cfg,
                halts,
                await news_exposure(
                    session, sig.symbol, now, s.calendar_pre_event_minutes, s.calendar_post_event_minutes
                ),
            )
            if reason:
                status["rejected"].append({"signal": sig.id, "setup": label, "reason": reason})
                await _alert(
                    session,
                    "auto_trade",
                    "info",
                    f"[SIMULATED] Signal skipped: {label}",
                    reason,
                    f"auto:skip:{sig.id}",
                    symbol=sig.symbol,
                )
                continue
            target = sig.targets[1] if cfg.exit_target == 2 and len(sig.targets) > 1 else sig.targets[0]
            order = book.submit(
                {
                    "symbol": sig.symbol,
                    "side": "buy" if sig.direction == "long" else "sell",
                    "type": "market",
                    "qty": s.autopilot_contracts,
                    "stop_loss": sig.stop,
                    "take_profit": target,
                    "plan_id": f"auto:{sig.id}",
                },
                now,
            )
            status["orders"].append({"signal": sig.id, "setup": label, "order": order["id"], "status": order["status"]})
            book.s.setdefault("auto_log", []).append(
                {
                    "at": now.isoformat(),
                    "signal": sig.id,
                    "strategy": sig.strategy_id,
                    "config": cfg.key,
                    "symbol": sig.symbol,
                    "order": order["id"],
                    "status": order["status"],
                    "reason": order.get("reason"),
                }
            )
            await _alert(
                session,
                "auto_trade",
                "info" if order["status"] != "rejected" else "warning",
                f"[SIMULATED] Order {order['status']}: {label}",
                f"market {s.autopilot_contracts} contract(s), stop {sig.stop}, target {target}"
                + (f" - {order['reason']}" if order.get("reason") else ""),
                f"auto:order:{sig.id}",
                symbol=sig.symbol,
            )
    await _lifecycle(session, acct, book)
    acct.state = book.s
    status["account"] = book.summary()
    status["positions"] = book.s["positions"]
    try:
        await get_redis().set(STATUS_KEY, json.dumps(status, default=str), ex=3600)
    except (RedisError, OSError):
        pass
    return status


def _reject_reason(
    s: Settings, book: PaperBook, sig: SignalRow, cfg: Any, halts: dict[str, str], news: dict[str, Any]
) -> str | None:
    if "*" in halts:
        return f"halted: {halts['*']}"
    if sig.symbol in halts:
        return f"halted for {sig.symbol}: {halts[sig.symbol]}"
    if news.get("status") == "inside_window":
        return f"news window: {news.get('summary')}"
    if news.get("status") == "unknown":
        return "news status unknown (no calendar imported)"
    group = correlation_group(sig.symbol)
    busy = {sym for sym, p in book.s["positions"].items() if p.get("qty")}
    busy |= {
        o["symbol"] for o in book.s["orders"] if o["role"] == "entry" and o["status"] in ("working", "partially_filled")
    }
    if any(correlation_group(x) == group or x == sig.symbol for x in busy):
        return f"position or working entry already open in the {group or sig.symbol} group"
    target = sig.targets[1] if cfg.exit_target == 2 and len(sig.targets) > 1 else sig.targets[0]
    inst = INSTRUMENTS[sig.symbol]
    comm = s.cost_commission_per_side_micro if inst.is_micro else s.cost_commission_per_side_standard
    p = BacktestParams(
        strategy_id=sig.strategy_id,
        symbol=sig.symbol,
        commission_per_side=comm,
        slippage_ticks=s.cost_slippage_ticks_per_side,
    )
    rr = net_rr(p, sig.symbol, sig.direction, sig.entry, sig.stop, target)
    if rr is None or rr <= 0:
        return "invalid levels"
    if cfg.min_net_rr is not None and rr < cfg.min_net_rr:
        return f"net R:R {rr} below the configuration's {cfg.min_net_rr}"
    pv, tick = float(inst.point_value), float(inst.tick_size)
    loss = (
        abs(sig.entry - sig.stop) * pv + 2 * comm + 2 * s.cost_slippage_ticks_per_side * tick * pv
    ) * s.autopilot_contracts
    if loss > s.autopilot_max_risk_per_trade:
        return f"loss at stop ${loss:,.2f} exceeds the per-trade limit ${s.autopilot_max_risk_per_trade:,.0f}"
    remaining = book.summary()["daily_loss_remaining"]
    if loss > remaining:
        return f"loss at stop ${loss:,.2f} exceeds the remaining daily loss allowance ${remaining:,.2f}"
    return None


async def forward_pnls(session: AsyncSession, s: Settings) -> list[float]:
    acct = await session.scalar(
        select(PaperAccount).where(PaperAccount.name == AUTO_ACCOUNT, PaperAccount.provider == s.market_data_provider)
    )
    if acct is None:
        return []
    return [round(c["gross_pnl"] - c["fees"], 2) for c in acct.state.get("closed", [])]


async def report(session: AsyncSession) -> dict[str, Any]:
    """Forward (prospective) simulated performance, per strategy - the evidence the broker gate needs."""
    from app.execution.broker import forward_evidence_ok
    from app.research.stats import bootstrap_mean_ci, wilson

    s = get_settings()
    acct = await session.scalar(
        select(PaperAccount).where(PaperAccount.name == AUTO_ACCOUNT, PaperAccount.provider == s.market_data_provider)
    )
    closed = acct.state.get("closed", []) if acct else []
    log_map = {f"auto:{x['signal']}": x for x in (acct.state.get("auto_log", []) if acct else [])}
    by: dict[str, list[float]] = {}
    for c in closed:
        meta = log_map.get(str(c.get("plan_id")))
        k = f"{meta['symbol']} {meta['strategy']} {meta['config']}" if meta else f"{c['symbol']} (unattributed)"
        by.setdefault(k, []).append(round(c["gross_pnl"] - c["fees"], 2))
    rows = []
    for k, pnls in sorted(by.items()):
        wins = sum(1 for x in pnls if x > 0)
        rows.append(
            {
                "setup": k,
                "trades": len(pnls),
                "win_rate": round(wins / len(pnls), 4),
                "win_rate_ci95": wilson(wins, len(pnls)),
                "net": round(sum(pnls), 2),
                "expectancy": round(sum(pnls) / len(pnls), 2),
                "expectancy_ci": bootstrap_mean_ci(pnls),
            }
        )
    allp = [x for v in by.values() for x in v]
    ok, why = forward_evidence_ok(allp)
    st = None
    try:
        raw = await get_redis().get(STATUS_KEY)
        st = json.loads(raw) if raw else None
    except (RedisError, OSError):
        pass
    return {
        "mode": s.automation_mode,
        "kill_switch": await killed(),
        "strategies": s.autopilot_strategies,
        "account": PaperBook(copy.deepcopy(acct.state)).summary() if acct else None,
        "positions": acct.state.get("positions", {}) if acct else {},
        "forward": rows,
        "forward_total": {"trades": len(allp), "net": round(sum(allp), 2)},
        "prospective_evidence": {"sufficient": ok, "detail": why},
        "last_cycle": st,
        "label": "SIMULATED - autonomous paper account, no broker connection",
    }


def log_cycle(status: dict[str, Any]) -> None:
    if status.get("orders") or status.get("rejected"):
        log_event(log, "autopilot cycle", orders=len(status["orders"]), rejected=len(status["rejected"]))
