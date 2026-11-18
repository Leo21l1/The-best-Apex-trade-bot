"""Backtesting laboratory, paper trading and trading journal endpoints."""

from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, SessionDep, client_ip
from app.core.config import get_settings
from app.marketdata.instruments import INSTRUMENTS
from app.models.calendar import EconomicEvent
from app.models.research import BacktestRun, JournalTrade
from app.paper.engine import OrderRejected, PaperBook, new_state
from app.research.backtest import BacktestParams
from app.risk.profile import load_rules, to_ruleset
from app.services.audit import record_audit
from app.services.journal import (
    MISTAKE_TAGS,
    grouped,
    mistake_analysis,
    parse_trades_csv,
    planned_vs_actual,
    r_multiple,
    report,
    rule_violations,
    stats,
    to_row,
)
from app.services.paper import config_from_rules, get_account, journal_closed, process
from app.strategies.library import STRATEGY_BY_ID

router = APIRouter(tags=["lab"])


# --- backtests -------------------------------------------------------------------------------


class BacktestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: str
    symbol: str
    start: datetime
    end: datetime
    quantity: int = Field(default=1, ge=1, le=20)
    commission_per_side: float | None = Field(default=None, ge=0, le=50)
    slippage_ticks: float | None = Field(default=None, ge=0, le=20)
    target_r: float | None = Field(default=None, gt=0.2, le=10)
    stop_atr: float | None = Field(default=None, gt=0.1, le=10)
    #: Skip signals whose net R:R to the exit target is below this (confirmation cost model).
    min_net_rr: float | None = Field(default=None, ge=0.5, le=5)
    exit_target: Literal[1, 2] = 1

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("must include a timezone")
        return v


def _run_out(r: BacktestRun, full: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": r.id,
        "created_at": r.created_at.isoformat(),
        "status": r.status,
        "strategy_id": r.strategy_id,
        "symbol": r.symbol,
        "provider": r.provider,
        "start": r.start.isoformat(),
        "end": r.end.isoformat(),
        "params": r.params,
        "validated": r.validated,
        "error": r.error,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }
    if r.result:
        res = r.result
        out["summary"] = {
            k: res["metrics"][k]
            for k in ("trades", "win_rate", "profit_factor", "expectancy_r", "net_profit", "max_drawdown")
        }
        out["dataset_quality"] = res["dataset"]["quality"]
        if full:
            out["result"] = res
    return out


@router.post("/backtests", status_code=status.HTTP_202_ACCEPTED)
async def create_backtest(body: BacktestIn, user: AdminUser, request: Request, session: SessionDep) -> dict[str, Any]:
    s = get_settings()
    if s.market_data_provider == "none":
        raise HTTPException(status.HTTP_409_CONFLICT, "No market data provider configured; nothing to test on.")
    strat = STRATEGY_BY_ID.get(body.strategy_id)
    if strat is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown strategy")
    sym = body.symbol.upper()
    if sym not in INSTRUMENTS or sym not in strat.spec.eligible_instruments:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{sym} is not eligible for {body.strategy_id}")
    if body.end <= body.start or body.end - body.start > timedelta(days=400):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Range must be positive and at most 400 days")
    from app.research.scanner import load_bars

    n_bars = len(await load_bars(session, s.market_data_provider, sym, body.start, body.end))
    if n_bars < 1000:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only {n_bars} stored 1-minute {s.market_data_provider} bars for {sym} in this range (need >= 1000). "
            "Check coverage with GET /api/backtests/coverage or backfill history first.",
        )
    inst = INSTRUMENTS[sym]
    params = BacktestParams(
        strategy_id=body.strategy_id,
        symbol=sym,
        quantity=body.quantity,
        commission_per_side=body.commission_per_side
        if body.commission_per_side is not None
        else (s.cost_commission_per_side_micro if inst.is_micro else s.cost_commission_per_side_standard),
        slippage_ticks=body.slippage_ticks if body.slippage_ticks is not None else s.cost_slippage_ticks_per_side,
        target_r=(body.target_r, body.target_r + 1) if body.target_r else None,
        stop_atr=body.stop_atr,
        min_net_rr=body.min_net_rr,
        exit_target=body.exit_target,
    )
    run = BacktestRun(
        status="queued",
        strategy_id=body.strategy_id,
        symbol=sym,
        provider=s.market_data_provider,
        start=body.start,
        end=body.end,
        params=asdict(params),
        created_by=user.id,
    )
    session.add(run)
    await session.flush()
    await record_audit(
        session,
        action="backtest.create",
        outcome="success",
        actor_id=user.id,
        ip=client_ip(request),
        details={"run_id": run.id, "strategy": body.strategy_id, "symbol": sym},
    )
    await session.commit()
    await session.refresh(run)
    return _run_out(run, False)


@router.get("/backtests")
async def list_backtests(
    _: CurrentUser, session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[dict[str, Any]]:
    rows = (await session.scalars(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(limit))).all()
    return await _with_status(list(rows), False)


async def _with_status(rows: list[BacktestRun], full: bool) -> list[dict[str, Any]]:
    """Attach progress (phase written by the worker) and an honest worker-health note."""
    import json as _json

    from app.core.redis import get_redis
    from app.services.health import worker_alive
    from app.services.research import PROGRESS_KEY

    alive = await worker_alive()
    now = datetime.now(UTC)
    out = []
    for r in rows:
        d = _run_out(r, full)
        try:
            raw = await get_redis().get(f"{PROGRESS_KEY}{r.id}")
            d["progress"] = _json.loads(raw) if raw else None
        except Exception:
            d["progress"] = None
        d["started_at"] = r.started_at.isoformat() if r.started_at else None
        if r.status in ("queued", "running"):
            since = r.started_at or r.created_at
            d["elapsed_seconds"] = int((now - since).total_seconds())
            if not alive:
                d["worker_note"] = "The background worker is not reporting; queued runs will not start until it is."
        out.append(d)
    return out


@router.get("/backtests/coverage")
async def backtest_coverage(
    _: CurrentUser, session: SessionDep, symbol: str, start: datetime, end: datetime
) -> dict[str, Any]:
    """Stored-data coverage for a proposed backtest range (no download is triggered)."""
    from app.marketdata.calendar import trading_day
    from app.research.scanner import load_bars

    s = get_settings()
    sym = symbol.upper()
    if sym not in INSTRUMENTS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown symbol")
    bars = await load_bars(session, s.market_data_provider, sym, start, end) if s.market_data_provider != "none" else []
    sessions = sorted({trading_day(b.ts).isoformat() for b in bars})
    return {
        "symbol": sym,
        "provider": s.market_data_provider,
        "bars": len(bars),
        "sessions": len(sessions),
        "first": bars[0].ts.isoformat() if bars else None,
        "last": bars[-1].ts.isoformat() if bars else None,
        "enough": len(bars) >= 1000,
        "note": None if len(bars) >= 1000 else "At least 1000 stored 1-minute bars are required to run.",
    }


@router.get("/backtests/compare")
async def compare_backtests(_: CurrentUser, session: SessionDep, ids: str) -> list[dict[str, Any]]:
    try:
        wanted = [int(x) for x in ids.split(",") if x.strip()][:4]
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ids must be comma-separated integers") from exc
    rows = [r for r in (await session.scalars(select(BacktestRun).where(BacktestRun.id.in_(wanted)))).all()]
    out = []
    for r in sorted(rows, key=lambda x: wanted.index(x.id)):
        res = r.result or {}
        out.append(
            {
                "id": r.id,
                "strategy_id": r.strategy_id,
                "symbol": r.symbol,
                "status": r.status,
                "start": r.start.isoformat(),
                "end": r.end.isoformat(),
                "params": r.params,
                "metrics": res.get("metrics"),
                "in_sample": res.get("in_sample"),
                "out_of_sample": res.get("out_of_sample"),
                "validation": res.get("validation"),
                "dataset": res.get("dataset"),
            }
        )
    return out


@router.get("/backtests/{run_id}")
async def get_backtest(
    run_id: Annotated[int, PathParam(ge=1, le=2_147_483_647)], _: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    r = await session.get(BacktestRun, run_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Backtest not found")
    return (await _with_status([r], True))[0]


# --- paper trading ---------------------------------------------------------------------------


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    side: Literal["buy", "sell"]
    type: Literal["market", "limit", "stop"]
    qty: int = Field(ge=1, le=100)
    price: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)


def _paper_out(acct: Any) -> dict[str, Any]:
    book = PaperBook(acct.state)
    st = acct.state
    return {
        "id": acct.id,
        "provider": acct.provider,
        "isolated": True,
        "notice": "Simulated account. It has no connection to Apex, Tradovate or any broker.",
        "summary": book.summary(),
        "positions": [{"symbol": k, **v, "last": st["last_prices"].get(k)} for k, v in st["positions"].items()],
        "orders": list(reversed(st["orders"][-100:])),
        "fills": list(reversed(st["fills"][-100:])),
        "closed": list(reversed(st["closed"][-100:])),
        "events": list(reversed(st["events"][-50:])),
        "config": st["config"],
    }


@router.get("/paper")
async def paper_state(_: CurrentUser, session: SessionDep) -> dict[str, Any]:
    s = get_settings()
    acct = await get_account(session, s)
    await process(session, acct, datetime.now(UTC))
    await session.commit()
    return _paper_out(acct)


async def _mutate(session: SessionDep, fn: Any) -> dict[str, Any]:
    s = get_settings()
    acct = await get_account(session, s)
    await process(session, acct, datetime.now(UTC))
    book = PaperBook(copy.deepcopy(acct.state))
    try:
        result = fn(book)
    except OrderRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    acct.state = book.s
    await journal_closed(session, acct)  # e.g. a flatten closes positions without new bars
    await session.commit()
    return {"result": result, "account": _paper_out(acct)}


@router.post("/paper/orders")
async def paper_order(body: OrderIn, _: AdminUser, session: SessionDep) -> dict[str, Any]:
    if get_settings().market_data_provider == "none":
        raise HTTPException(status.HTTP_409_CONFLICT, "No market data provider configured; orders cannot fill.")
    return await _mutate(session, lambda b: b.submit(body.model_dump(), datetime.now(UTC)))


@router.post("/paper/orders/{order_id}/cancel")
async def paper_cancel(order_id: int, _: AdminUser, session: SessionDep) -> dict[str, Any]:
    return await _mutate(session, lambda b: b.cancel(order_id, datetime.now(UTC)))


@router.post("/paper/flatten")
async def paper_flatten(_: AdminUser, session: SessionDep) -> dict[str, Any]:
    return await _mutate(session, lambda b: b.flatten(datetime.now(UTC)))


@router.post("/paper/reset")
async def paper_reset(user: AdminUser, request: Request, session: SessionDep) -> dict[str, Any]:
    s = get_settings()
    acct = await get_account(session, s)
    acct.state = new_state(await config_from_rules(session, s))
    acct.cursor = {}
    await record_audit(session, action="paper.reset", outcome="success", actor_id=user.id, ip=client_ip(request))
    await session.commit()
    return _paper_out(acct)


# --- journal ---------------------------------------------------------------------------------


class CsvIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csv: str = Field(min_length=1, max_length=2_000_000)
    timezone: str = Field(default="America/New_York", max_length=64)
    dry_run: bool = False


def _trade_out(t: JournalTrade) -> dict[str, Any]:
    return {
        "id": t.id,
        "source": t.source,
        "trading_date": t.trading_date.isoformat(),
        "symbol": t.symbol,
        "contract": t.contract,
        "direction": t.direction,
        "quantity": t.quantity,
        "entry_time": t.entry_time.isoformat(),
        "exit_time": t.exit_time.isoformat(),
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "gross_pnl": t.gross_pnl,
        "fees": t.fees,
        "net_pnl": t.net_pnl,
        "strategy_id": t.strategy_id,
        "planned_stop": t.planned_stop,
        "planned_target": t.planned_target,
        "followed_plan": t.followed_plan,
        "mistakes": t.mistakes or [],
        "notes": t.notes,
        "slippage": t.slippage,
        "mfe": t.mfe,
        "mae": t.mae,
        "plan_id": t.plan_id,
        "duration_seconds": (t.exit_time - t.entry_time).total_seconds(),
        "simulated": t.source == "paper",
        "broker_record": t.source == "csv",
        "reviewed": t.review is not None,
        "findings": [f["code"] for f in ((t.review or {}).get("coaching") or {}).get("supported_mistakes", [])],
        "setup": t.setup,
        "thesis": t.thesis,
        "psychology": t.psychology,
        "emotion_tags": t.emotion_tags or [],
        "lessons": t.lessons,
        "tags": t.tags or [],
        "chart_links": t.chart_links or [],
        "fills": t.fills or [],
        "record": "MANUALLY ENTERED" if t.source == "manual" else "BROKER IMPORT" if t.source == "csv" else "SIMULATED",
        "r_multiple": r_multiple(t),
        "corrected_fields": sorted((t.corrections or {}).keys()),
        "corrections": t.corrections or {},
        "original": t.original,
        "corrected_at": t.corrected_at.isoformat() if t.corrected_at else None,
        "record_provenance": (
            (
                "broker record (imported)"
                if t.source == "csv"
                else "simulator record"
                if t.source == "paper"
                else "manual entry"
            )
            + (" with manual corrections" if t.corrections else "")
        ),
    }


@router.post("/journal/import")
async def journal_import(body: CsvIn, user: AdminUser, request: Request, session: SessionDep) -> dict[str, Any]:
    from zoneinfo import ZoneInfoNotFoundError

    s = get_settings()
    try:
        res = parse_trades_csv(
            body.csv,
            body.timezone,
            {"standard": s.cost_commission_per_side_standard, "micro": s.cost_commission_per_side_micro},
        )
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown time zone") from exc
    existing = (
        set(
            (
                await session.scalars(
                    select(JournalTrade.source_key).where(
                        JournalTrade.source_key.in_([t.source_key for t in res.trades])
                    )
                )
            ).all()
        )
        if res.trades
        else set()
    )
    new = [t for t in res.trades if t.source_key not in existing]
    imported = 0
    if not body.dry_run and new and not res.errors:
        for t in new:
            session.add(to_row(t, "csv"))
        imported = len(new)
        await record_audit(
            session,
            action="journal.import",
            outcome="success",
            actor_id=user.id,
            ip=client_ip(request),
            details={"trades": imported, "layout": res.layout},
        )
        await session.commit()
    return {
        "dry_run": body.dry_run,
        "layout": res.layout,
        "parsed": len(res.trades),
        "duplicates": len(existing),
        "imported": imported,
        "errors": res.errors[:50],
        "preview": [
            {
                "symbol": t.symbol,
                "contract": t.contract,
                "direction": t.direction,
                "qty": t.quantity,
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "net_pnl": round(t.gross_pnl - t.fees, 2),
            }
            for t in new[:10]
        ],
    }


@router.get("/journal/trades")
async def journal_trades(
    _: CurrentUser,
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    symbol: str | None = None,
    source: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> dict[str, Any]:
    q = select(JournalTrade)
    if start:
        q = q.where(JournalTrade.trading_date >= start)
    if end:
        q = q.where(JournalTrade.trading_date <= end)
    if symbol:
        q = q.where(JournalTrade.symbol == symbol.upper())
    if source:
        q = q.where(JournalTrade.source == source)
    rows = (await session.scalars(q.order_by(JournalTrade.exit_time.desc()).limit(limit))).all()
    return {"trades": [_trade_out(t) for t in rows], "stats": stats(rows), "mistake_tags": list(MISTAKE_TAGS)}


@router.get("/analytics")
async def analytics(
    _: CurrentUser,
    session: SessionDep,
    period: Literal["day", "week", "month"] = "day",
    days: Annotated[int, Query(ge=1, le=3650)] = 90,
    source: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    since = start or (datetime.now(UTC).date() - timedelta(days=days))
    q = select(JournalTrade).where(JournalTrade.trading_date >= since)
    if end:
        q = q.where(JournalTrade.trading_date <= end)
    if source:
        q = q.where(JournalTrade.source == source)
    trades = (await session.scalars(q.order_by(JournalTrade.exit_time))).all()
    rules = to_ruleset(await load_rules(session))
    s = get_settings()
    events = []
    if trades:
        events = (
            await session.scalars(
                select(EconomicEvent).where(
                    EconomicEvent.ts >= trades[0].entry_time - timedelta(hours=1),
                    EconomicEvent.ts <= trades[-1].exit_time + timedelta(hours=1),
                )
            )
        ).all()
    from app.marketdata.calendar import ET

    return {
        "window_days": days,
        "period": period,
        "overall": stats(trades),
        "by_period": report(trades, period),
        "by_strategy": grouped(trades, lambda t: t.strategy_id or "untagged"),
        "by_symbol": grouped(trades, lambda t: t.symbol),
        "by_hour_et": grouped(trades, lambda t: f"{t.entry_time.astimezone(ET).hour:02d}:00"),
        "by_direction": grouped(trades, lambda t: t.direction),
        "mistakes": mistake_analysis(trades),
        "planned_vs_actual": planned_vs_actual(trades),
        "rule_violations": rule_violations(
            trades,
            float(rules.max_contracts),
            float(rules.daily_loss_limit),
            events,
            s.calendar_pre_event_minutes,
            s.calendar_post_event_minutes,
        ),
        "rules_verified": rules.verified,
        "by_news": grouped(trades, _news_bucket),
        "equity_curve": _equity(trades),
        "daily": _daily(trades),
        "average_planned_rr": _avg_rr(trades),
        "rule_adherence": _adherence(trades),
        "common_findings": _common_findings(trades),
        "sample_size_note": None
        if len(trades) >= 30
        else f"Only {len(trades)} trades: treat every percentage here as highly uncertain.",
        "notes": [
            "Statistics describe past trades only. Small samples are flagged rather than interpreted.",
            "Rule checks use the configured rule values" + ("" if rules.verified else ", which are UNVERIFIED") + ".",
            "PROJECT APEX never changes strategy parameters automatically.",
        ],
    }


def _equity(trades: list[JournalTrade]) -> list[dict[str, Any]]:
    """Cumulative net P&L after each closed trade and the drawdown from its running peak.

    Journal P&L only: it is not the broker account balance and excludes intraday open-trade swings.
    """
    out, cum, peak = [], 0.0, 0.0
    for t in trades:
        cum = round(cum + t.net_pnl, 2)
        peak = max(peak, cum)
        out.append({"t": t.exit_time.isoformat(), "trade_id": t.id, "equity": cum, "drawdown": round(cum - peak, 2)})
    return out


def _daily(trades: list[JournalTrade]) -> list[dict[str, Any]]:
    days: dict[str, dict[str, Any]] = {}
    for t in trades:
        d = days.setdefault(t.trading_date.isoformat(), {"date": t.trading_date.isoformat(), "net": 0.0, "trades": 0})
        d["net"] = round(d["net"] + t.net_pnl, 2)
        d["trades"] += 1
    return sorted(days.values(), key=lambda x: x["date"])


def _news_bucket(t: JournalTrade) -> str:
    news = (t.context or {}).get("news") or {}
    if news.get("high_impact_near_entry"):
        return "high-impact event within 1h of entry"
    if any(e["phase"] == "while_open" for e in news.get("events", [])):
        return "event while open"
    return "no recorded event" if t.context else "not yet reviewed"


def _avg_rr(trades: list[JournalTrade]) -> dict[str, Any]:
    rrs = [
        abs(t.planned_target - t.entry_price) / abs(t.entry_price - t.planned_stop)
        for t in trades
        if t.planned_stop and t.planned_target and t.planned_stop != t.entry_price
    ]
    return {"value": round(sum(rrs) / len(rrs), 2) if rrs else None, "trades_with_plan": len(rrs)}


def _adherence(trades: list[JournalTrade]) -> dict[str, Any]:
    with_stop = [t for t in trades if t.planned_stop]
    beyond = [
        t
        for t in with_stop
        if "beyond_stop" in [f["code"] for f in ((t.review or {}).get("coaching") or {}).get("supported_mistakes", [])]
    ]
    marked = [t for t in trades if t.followed_plan is not None]
    return {
        "trades_with_stop": len(with_stop),
        "exits_beyond_stop": len(beyond),
        "stop_respected_rate": round(1 - len(beyond) / len(with_stop), 3) if with_stop else None,
        "self_reported_followed_rate": round(sum(1 for t in marked if t.followed_plan) / len(marked), 3)
        if marked
        else None,
    }


def _common_findings(trades: list[JournalTrade]) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for t in trades:
        for f in ((t.review or {}).get("coaching") or {}).get("supported_mistakes", []):
            c = counts.setdefault(f["code"], {"code": f["code"], "title": f["title"], "count": 0, "net_pnl": 0.0})
            c["count"] += 1
            c["net_pnl"] = round(c["net_pnl"] + t.net_pnl, 2)
    return sorted(counts.values(), key=lambda c: -c["count"])
