"""Backtesting laboratory, paper trading and trading journal endpoints."""

from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
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
async def create_backtest(body: BacktestIn, user: CurrentUser, request: Request, session: SessionDep) -> dict[str, Any]:
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
    return [_run_out(r, False) for r in rows]


@router.get("/backtests/{run_id}")
async def get_backtest(run_id: int, _: CurrentUser, session: SessionDep) -> dict[str, Any]:
    r = await session.get(BacktestRun, run_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Backtest not found")
    return _run_out(r, True)


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
async def paper_order(body: OrderIn, _: CurrentUser, session: SessionDep) -> dict[str, Any]:
    if get_settings().market_data_provider == "none":
        raise HTTPException(status.HTTP_409_CONFLICT, "No market data provider configured; orders cannot fill.")
    return await _mutate(session, lambda b: b.submit(body.model_dump(), datetime.now(UTC)))


@router.post("/paper/orders/{order_id}/cancel")
async def paper_cancel(order_id: int, _: CurrentUser, session: SessionDep) -> dict[str, Any]:
    return await _mutate(session, lambda b: b.cancel(order_id, datetime.now(UTC)))


@router.post("/paper/flatten")
async def paper_flatten(_: CurrentUser, session: SessionDep) -> dict[str, Any]:
    return await _mutate(session, lambda b: b.flatten(datetime.now(UTC)))


@router.post("/paper/reset")
async def paper_reset(user: CurrentUser, request: Request, session: SessionDep) -> dict[str, Any]:
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


class TradeEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: str | None = None
    planned_stop: float | None = Field(default=None, gt=0)
    planned_target: float | None = Field(default=None, gt=0)
    followed_plan: bool | None = None
    mistakes: list[str] | None = Field(default=None, max_length=10)
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("mistakes")
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        bad = [m for m in v if m not in MISTAKE_TAGS]
        if bad:
            raise ValueError(f"unknown mistake tags: {', '.join(bad)}")
        return sorted(set(v))

    @field_validator("strategy_id")
    @classmethod
    def _strategy(cls, v: str | None) -> str | None:
        if v not in (None, "", *STRATEGY_BY_ID, "discretionary"):
            raise ValueError("unknown strategy")
        return v or None


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
    }


@router.post("/journal/import")
async def journal_import(body: CsvIn, user: CurrentUser, request: Request, session: SessionDep) -> dict[str, Any]:
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


@router.patch("/journal/trades/{trade_id}")
async def journal_edit(trade_id: int, body: TradeEdit, _: CurrentUser, session: SessionDep) -> dict[str, Any]:
    t = await session.get(JournalTrade, trade_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trade not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    await session.commit()
    return _trade_out(t)


@router.delete("/journal/trades/{trade_id}", status_code=status.HTTP_204_NO_CONTENT)
async def journal_delete(trade_id: int, user: AdminUser, request: Request, session: SessionDep) -> Response:
    t = await session.get(JournalTrade, trade_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trade not found")
    await session.delete(t)
    await record_audit(
        session,
        action="journal.delete",
        outcome="success",
        actor_id=user.id,
        ip=client_ip(request),
        details={"trade_id": trade_id},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/analytics")
async def analytics(
    _: CurrentUser,
    session: SessionDep,
    period: Literal["day", "week", "month"] = "day",
    days: Annotated[int, Query(ge=1, le=3650)] = 90,
    source: str | None = None,
) -> dict[str, Any]:
    since = datetime.now(UTC).date() - timedelta(days=days)
    q = select(JournalTrade).where(JournalTrade.trading_date >= since)
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
        "notes": [
            "Statistics describe past trades only. Small samples are flagged rather than interpreted.",
            "Rule checks use the configured rule values" + ("" if rules.verified else ", which are UNVERIFIED") + ".",
            "PROJECT APEX never changes strategy parameters automatically.",
        ],
    }
