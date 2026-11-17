"""Trading terminal: chart overlays, BUY/SHORT/WAIT label, trade plans, execution status, trade reviews."""

from __future__ import annotations

import bisect
import copy
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

import anthropic
from anthropic import AsyncAnthropic
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, SessionDep, client_ip
from app.api.routes.copilot import get_copilot_client
from app.core.config import get_settings
from app.core.redis import get_redis
from app.marketdata.instruments import INSTRUMENTS, get_instrument
from app.marketdata.store import query_bars
from app.marketdata.types import Timeframe
from app.models.calendar import EconomicEvent
from app.models.research import JournalTrade, TradePlan
from app.models.signal import SignalRow
from app.paper.engine import PaperBook
from app.research.bars import Bar
from app.research.indicators import ema, session_vwap
from app.research.scanner import SCAN_KEY
from app.risk.engine import check_trade
from app.services.account import current_state
from app.services.audit import record_audit
from app.services.calendar import news_exposure
from app.services.paper import get_account, process
from app.services.trade_review import replay_data, review_trade
from app.strategies.library import STRATEGY_BY_ID

router = APIRouter(tags=["terminal"])

LIVE_TRADING = {
    "live_trading_enabled": False,
    "reason": "Live order submission to Apex / Tradovate is not enabled. Third-party automated order entry has "
    "not been verified as authorised by Apex and Tradovate for this account, and PROJECT APEX contains no "
    "live order endpoint. Paper trading is fully available.",
    "manual_workflow": [
        "Write a trade plan here (entry, stop, target, size, thesis) and check it in the Risk Guardian.",
        "Place the order yourself in your Tradovate window using the plan's values.",
        "Mark the plan as executed manually.",
        "After the trade closes, import your Tradovate Performance CSV (or enter the trade manually) on the "
        "Trading Journal. The trade is linked, reviewed and journaled automatically.",
    ],
}


# --- label and overlays ----------------------------------------------------------------------


async def trade_label(session: SessionDep, symbol: str) -> dict[str, Any]:
    """BUY / SHORT / WAIT / NO TRADE for ``symbol`` with the reasons, from current stored state."""
    s = get_settings()
    now = datetime.now(UTC)
    raw = await get_redis().get(SCAN_KEY)
    scan = json.loads(raw) if raw else None
    quality = (scan or {}).get("quality", "unavailable")
    reasons: list[str] = []
    if s.market_data_provider == "none" or quality == "unavailable":
        return {"label": "NO TRADE", "reasons": ["No current market data."], "quality": quality, "signal": None}
    news = await news_exposure(session, symbol, now, s.calendar_pre_event_minutes, s.calendar_post_event_minutes)
    sig = await session.scalar(
        select(SignalRow)
        .where(
            SignalRow.symbol == symbol,
            SignalRow.status == "active",
            SignalRow.provider == s.market_data_provider,
            SignalRow.created_at >= now - timedelta(minutes=30),
        )
        .order_by(SignalRow.created_at.desc())
        .limit(1)
    )
    if news["status"] == "inside_window":
        return {"label": "NO TRADE", "reasons": [news["summary"]], "quality": quality, "signal": None}
    if sig is not None:
        label = "BUY" if sig.direction == "long" else "SHORT"
        reasons.append(f"{sig.strategy_id} conditions met at the {sig.bar_ts.astimezone().strftime('%H:%M')} UTC bar.")
        reasons.append("Strategy is NOT validated by backtests on real data; treat as a research candidate.")
        if quality == "simulated":
            reasons.append("Prices are SIMULATED.")
        return {
            "label": label,
            "reasons": reasons,
            "quality": quality,
            "signal": {
                "id": sig.id,
                "strategy_id": sig.strategy_id,
                "direction": sig.direction,
                "entry": sig.entry,
                "stop": sig.stop,
                "targets": sig.targets,
                "score": sig.score,
                "bar_ts": sig.bar_ts.isoformat(),
            },
        }
    sym = next((x for x in (scan or {}).get("symbols", []) if x["symbol"] == symbol), None)
    reasons.append("No strategy's conditions are met on the latest closed 5-minute bar.")
    if sym:
        reasons.append(f"Regime: {sym['regime'].replace('_', ' ')} ({sym['regime_definition']})")
    return {"label": "WAIT", "reasons": reasons, "quality": quality, "signal": None}


def _series(times: list[int], values: list[float | None]) -> list[dict[str, float]]:
    return [{"t": t, "v": round(v, 4)} for t, v in zip(times, values, strict=True) if v is not None]


@router.get("/market/overlays")
async def overlays(
    _: CurrentUser,
    session: SessionDep,
    symbol: str,
    timeframe: Timeframe = Timeframe.M5,
    limit: Annotated[int, Query(ge=10, le=2000)] = 600,
) -> dict[str, Any]:
    inst = get_instrument(symbol)
    if inst is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown symbol")
    sym = inst.symbol
    s = get_settings()
    out: dict[str, Any] = {
        "symbol": sym,
        "timeframe": timeframe.value,
        "ema20": [],
        "ema50": [],
        "vwap": [],
        "levels": {},
        "signals": [],
        "events": [],
        "label": await trade_label(session, sym),
    }
    if s.market_data_provider == "none":
        return out
    bars = await query_bars(session, provider=s.market_data_provider, symbol=sym, timeframe=timeframe, limit=limit)
    if not bars:
        return out
    times = [int(b.ts.timestamp()) for b in bars]
    closes = [float(b.close) for b in bars]
    out["ema20"], out["ema50"] = _series(times, ema(closes, 20)), _series(times, ema(closes, 50))
    if timeframe.minutes <= 60:
        rb = [Bar(b.ts, float(b.open), float(b.high), float(b.low), float(b.close), b.volume) for b in bars]
        out["vwap"] = _series(times, session_vwap(rb)[0])
    raw = await get_redis().get(SCAN_KEY)
    scan = json.loads(raw) if raw else None
    radar = next((x for x in (scan or {}).get("symbols", []) if x["symbol"] == sym), None)
    if radar:
        out["levels"] = {
            k: v for k, v in radar["levels"].items() if isinstance(v, int | float) and not isinstance(v, bool)
        }
    start, end = bars[0].ts, bars[-1].ts + timeframe.delta
    sigs = (
        await session.scalars(
            select(SignalRow)
            .where(
                SignalRow.symbol == sym,
                SignalRow.provider == s.market_data_provider,
                SignalRow.bar_ts >= start,
                SignalRow.bar_ts <= end,
            )
            .order_by(SignalRow.bar_ts)
        )
    ).all()

    def bucket(ts: datetime) -> int:
        """Open time of the displayed bar containing ``ts`` (markers must sit on a bar)."""
        i = bisect.bisect_right(times, int(ts.timestamp())) - 1
        return times[max(i, 0)]

    out["signals"] = [
        {
            "t": bucket(x.bar_ts),
            "direction": x.direction,
            "label": "BUY" if x.direction == "long" else "SHORT",
            "strategy_id": x.strategy_id,
            "status": x.status,
            "entry": x.entry,
            "stop": x.stop,
            "targets": x.targets,
            "score": x.score,
            "explanation": "; ".join(c["name"] for c in x.detail.get("conditions", [])),
        }
        for x in sigs
    ]
    evs = (
        await session.scalars(
            select(EconomicEvent).where(EconomicEvent.ts >= start, EconomicEvent.ts <= end).order_by(EconomicEvent.ts)
        )
    ).all()
    out["events"] = [
        {
            "id": e.id,
            "t": bucket(e.ts),
            "ts": e.ts.isoformat(),
            "title": e.title,
            "importance": e.importance,
            "category": e.category,
            "affected": e.affected,
            "source": e.source,
            "source_ref": e.source_ref,
            "detected_at": e.created_at.isoformat() if e.created_at else None,
            "bias": "None. Scheduled calendar event with no directional forecast recorded.",
            "explanation": "Importance reflects the typical relevance of this event type to these futures; it is not a "
            "prediction of direction or size.",
            "affects_symbol": sym in (e.affected or []),
        }
        for e in evs
    ]
    # Published announcements (official feeds) relevant to this instrument.
    from app.models.calendar import NewsItem
    from app.services.news import bias as news_bias

    imp = {"CRITICAL": "high", "HIGH": "high", "MODERATE": "medium"}
    news = (
        await session.scalars(
            select(NewsItem)
            .where(NewsItem.published_at >= start, NewsItem.published_at <= end)
            .order_by(NewsItem.published_at)
        )
    ).all()
    now = datetime.now(UTC)
    for n in news:
        if sym not in (n.instruments or []):
            continue
        b = news_bias(n, now, sym)
        out["events"].append(
            {
                "id": -n.id,
                "kind": "news",
                "t": bucket(n.published_at),
                "ts": n.published_at.isoformat(),
                "title": n.title,
                "importance": imp.get(n.impact, "low"),
                "impact": n.impact,
                "category": n.impact_reason,
                "affected": n.instruments,
                "source": n.source_name,
                "source_ref": n.link,
                "detected_at": n.detected_at.isoformat(),
                "bias": f"{b['bias']}. {b['why']}",
                "explanation": f"Impact {n.impact} by rule: {n.impact_reason}. A relevance classification, not a "
                "forecast.",
                "affects_symbol": True,
            }
        )
    out["events"].sort(key=lambda e: e["t"])
    return out


@router.get("/market/label")
async def label(_: CurrentUser, session: SessionDep, symbol: str) -> dict[str, Any]:
    inst = get_instrument(symbol)
    if inst is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown symbol")
    return await trade_label(session, inst.symbol)


@router.get("/execution/live-status")
async def live_status(_: CurrentUser) -> dict[str, Any]:
    return LIVE_TRADING


# --- trade plans ------------------------------------------------------------------------------


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    direction: Literal["long", "short"]
    entry: float | None = Field(default=None, gt=0, description="Blank = market order")
    stop: float = Field(gt=0)
    target: float | None = Field(default=None, gt=0)
    quantity: int = Field(ge=1, le=100)
    strategy_id: str | None = None
    thesis: str | None = Field(default=None, max_length=4000)

    @field_validator("symbol")
    @classmethod
    def _sym(cls, v: str) -> str:
        inst = get_instrument(v)
        if inst is None:
            raise ValueError("unknown symbol")
        return inst.symbol

    @field_validator("strategy_id")
    @classmethod
    def _strategy(cls, v: str | None) -> str | None:
        if v not in (None, "", *STRATEGY_BY_ID, "discretionary"):
            raise ValueError("unknown strategy")
        return v or None


def _plan_out(p: TradePlan) -> dict[str, Any]:
    return {
        "id": p.id,
        "created_at": p.created_at.isoformat(),
        "symbol": p.symbol,
        "direction": p.direction,
        "entry": p.entry,
        "stop": p.stop,
        "target": p.target,
        "quantity": p.quantity,
        "strategy_id": p.strategy_id,
        "thesis": p.thesis,
        "signal_snapshot": p.signal_snapshot,
        "risk_snapshot": p.risk_snapshot,
        "status": p.status,
        "execution": p.execution,
    }


@router.post("/plans", status_code=status.HTTP_201_CREATED)
async def create_plan(body: PlanIn, user: AdminUser, request: Request, session: SessionDep) -> dict[str, Any]:
    from app.api.routes.account import cost_model_for

    inst = INSTRUMENTS[body.symbol]
    rules, state = await current_state(session)
    paper = await get_account(session, get_settings())
    last = paper.state["last_prices"].get(body.symbol)
    ref = body.entry or last
    risk: dict[str, Any] | None = None
    if ref is not None:
        tick = float(inst.tick_size)
        ref = round(round(ref / tick) * tick, 6)
        try:
            c = check_trade(
                rules,
                state,
                inst,
                body.direction,
                Decimal(str(ref)),
                Decimal(str(body.stop)),
                Decimal(str(body.target)) if body.target else None,
                body.quantity,
                cost_model_for(body.symbol),
                INSTRUMENTS,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        risk = {k: (float(v) if isinstance(v, Decimal) else v) for k, v in asdict(c).items()}
        risk["reference_price"] = ref
    plan = TradePlan(
        symbol=body.symbol,
        direction=body.direction,
        entry=body.entry,
        stop=body.stop,
        target=body.target,
        quantity=body.quantity,
        strategy_id=body.strategy_id,
        thesis=body.thesis,
        signal_snapshot=await trade_label(session, body.symbol),
        risk_snapshot=risk,
        status="draft",
        created_by=user.id,
    )
    session.add(plan)
    await session.flush()
    await record_audit(
        session,
        action="plan.create",
        outcome="success",
        actor_id=user.id,
        ip=client_ip(request),
        details={"plan_id": plan.id},
    )
    await session.commit()
    await session.refresh(plan)
    return _plan_out(plan)


@router.get("/plans")
async def list_plans(_: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    rows = (await session.scalars(select(TradePlan).order_by(TradePlan.id.desc()).limit(100))).all()
    return [_plan_out(p) for p in rows]


async def _plan(session: SessionDep, plan_id: int) -> TradePlan:
    p = await session.get(TradePlan, plan_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    return p


@router.post("/plans/{plan_id}/execute-paper")
async def execute_paper(plan_id: int, user: AdminUser, request: Request, session: SessionDep) -> dict[str, Any]:
    p = await _plan(session, plan_id)
    if p.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Plan is {p.status}")
    s = get_settings()
    if s.market_data_provider == "none":
        raise HTTPException(status.HTTP_409_CONFLICT, "No market data provider configured; orders cannot fill.")
    acct = await get_account(session, s)
    await process(session, acct, datetime.now(UTC))
    book = PaperBook(copy.deepcopy(acct.state))
    order = book.submit(
        {
            "symbol": p.symbol,
            "side": "buy" if p.direction == "long" else "sell",
            "type": "market" if p.entry is None else "limit",
            "qty": p.quantity,
            "price": p.entry,
            "stop_loss": p.stop,
            "take_profit": p.target,
            "plan_id": p.id,
        },
        datetime.now(UTC),
    )
    if order["status"] == "rejected":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Paper order rejected: {order['reason']}")
    acct.state = book.s
    p.status, p.execution = "executed_paper", "paper"
    await record_audit(
        session,
        action="plan.execute_paper",
        outcome="success",
        actor_id=user.id,
        ip=client_ip(request),
        details={"plan_id": p.id, "order_id": order["id"]},
    )
    await session.commit()
    return {"plan": _plan_out(p), "order": order}


@router.post("/plans/{plan_id}/mark-manual")
async def mark_manual(plan_id: int, user: AdminUser, session: SessionDep) -> dict[str, Any]:
    p = await _plan(session, plan_id)
    if p.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Plan is {p.status}")
    p.status, p.execution = "executed_manual", "manual_tradovate"
    await session.commit()
    return {"plan": _plan_out(p), "next_steps": LIVE_TRADING["manual_workflow"][2:]}


@router.post("/plans/{plan_id}/cancel")
async def cancel_plan(plan_id: int, _: AdminUser, session: SessionDep) -> dict[str, Any]:
    p = await _plan(session, plan_id)
    if p.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Plan is {p.status}")
    p.status = "cancelled"
    await session.commit()
    return _plan_out(p)


# --- trades: manual entry, detail, review, replay, AI narrative --------------------------------


class ManualTradeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    direction: Literal["long", "short"]
    quantity: int = Field(ge=1, le=100)
    entry_time: datetime
    exit_time: datetime
    entry_price: float = Field(gt=0)
    exit_price: float = Field(gt=0)
    fees: float | None = Field(default=None, ge=0)
    planned_stop: float | None = Field(default=None, gt=0)
    planned_target: float | None = Field(default=None, gt=0)
    plan_id: int | None = None
    strategy_id: str | None = None
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("entry_time", "exit_time")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("must include a timezone")
        return v


def trade_detail(t: JournalTrade) -> dict[str, Any]:
    from app.api.routes.lab import _trade_out

    return {
        **_trade_out(t),
        "context": t.context,
        "review": t.review,
        "reviewed_at": t.reviewed_at.isoformat() if t.reviewed_at else None,
    }


@router.post("/journal/trades", status_code=status.HTTP_201_CREATED)
async def manual_trade(body: ManualTradeIn, user: AdminUser, request: Request, session: SessionDep) -> dict[str, Any]:
    from app.marketdata.calendar import trading_day

    s = get_settings()
    inst = INSTRUMENTS[get_instrument(body.symbol).symbol] if get_instrument(body.symbol) else None
    if inst is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown symbol")
    if body.exit_time < body.entry_time:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Exit is before entry")
    for px in (body.entry_price, body.exit_price):
        if abs(round(px / float(inst.tick_size)) * float(inst.tick_size) - px) > 1e-9:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"Prices must be on the {inst.tick_size} tick grid"
            )
    if body.plan_id is not None:
        plan = await _plan(session, body.plan_id)
        if plan.symbol != inst.symbol or plan.direction != body.direction:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Trade does not match the plan's symbol/direction"
            )
    sgn = 1 if body.direction == "long" else -1
    gross = round((body.exit_price - body.entry_price) * sgn * float(inst.point_value) * body.quantity, 2)
    per = s.cost_commission_per_side_micro if inst.is_micro else s.cost_commission_per_side_standard
    fees = body.fees if body.fees is not None else round(2 * per * body.quantity, 2)
    key = (
        f"manual:{inst.symbol}|{body.entry_time.isoformat()}|{body.exit_time.isoformat()}|{body.quantity}|"
        f"{body.entry_price}|{body.exit_price}"
    )
    if await session.scalar(select(JournalTrade).where(JournalTrade.source_key == key)):
        raise HTTPException(status.HTTP_409_CONFLICT, "This trade is already recorded")
    t = JournalTrade(
        source_key=key,
        source="manual",
        trading_date=trading_day(body.exit_time),
        symbol=inst.symbol,
        contract=inst.symbol,
        direction=body.direction,
        quantity=body.quantity,
        entry_time=body.entry_time.astimezone(UTC),
        exit_time=body.exit_time.astimezone(UTC),
        entry_price=body.entry_price,
        exit_price=body.exit_price,
        gross_pnl=gross,
        fees=fees,
        net_pnl=round(gross - fees, 2),
        planned_stop=body.planned_stop,
        planned_target=body.planned_target,
        plan_id=body.plan_id,
        strategy_id=body.strategy_id,
        notes=body.notes,
        mistakes=[],
        slippage=0.0,
    )
    session.add(t)
    await session.flush()
    await review_trade(session, t, s)
    await record_audit(
        session,
        action="journal.manual",
        outcome="success",
        actor_id=user.id,
        ip=client_ip(request),
        details={"trade_id": t.id},
    )
    await session.commit()
    return trade_detail(t)


async def _trade(session: SessionDep, trade_id: int) -> JournalTrade:
    t = await session.get(JournalTrade, trade_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trade not found")
    return t


@router.get("/journal/trades/{trade_id}")
async def get_trade(trade_id: int, _: CurrentUser, session: SessionDep) -> dict[str, Any]:
    t = await _trade(session, trade_id)
    if t.review is None:
        await review_trade(session, t, get_settings())
        await session.commit()
    return trade_detail(t)


@router.post("/journal/trades/{trade_id}/review")
async def regenerate_review(trade_id: int, _: AdminUser, session: SessionDep) -> dict[str, Any]:
    t = await _trade(session, trade_id)
    await review_trade(session, t, get_settings())
    await session.commit()
    return trade_detail(t)


@router.get("/journal/trades/{trade_id}/replay")
async def replay(trade_id: int, _: CurrentUser, session: SessionDep) -> dict[str, Any]:
    t = await _trade(session, trade_id)
    if t.review is None:
        await review_trade(session, t, get_settings())
        await session.commit()
    return await replay_data(session, t, get_settings())


COACH_PROMPT = """You are a trading performance coach reviewing ONE completed futures trade for a trader on an \
Apex Trader Funding evaluation. You receive the stored trade record, the computed market context, news timing, \
rule-based findings and hypothetical scenarios as JSON. Use only that data. Do not invent prices, events or \
account values. Do not diagnose emotions or intent. Do not say news caused the outcome; describe timing as \
correlation. Do not judge the decision by its outcome. Label hypothetical results as hypothetical.

Write 6-10 short bullets under these headings: **What went well**, **What to improve** (each tied to evidence in \
the data), **What was known at entry**, **Next learning objective**. Be direct and specific."""


@router.post("/journal/trades/{trade_id}/ai-review")
async def ai_review(
    trade_id: int, user: AdminUser, session: SessionDep, c: Annotated[AsyncAnthropic, Depends(get_copilot_client)]
) -> dict[str, Any]:
    """Narrative coaching from Claude, grounded only in the stored record (optional; needs an API key)."""
    t = await _trade(session, trade_id)
    if t.review is None:
        await review_trade(session, t, get_settings())
    s = get_settings()
    record = {
        "trade": {k: v for k, v in trade_detail(t).items() if k not in ("review", "context")},
        "context": t.context,
        "findings": (t.review or {}).get("coaching"),
        "scenarios": (t.review or {}).get("scenarios"),
    }
    try:
        resp = await c.messages.create(
            model=s.copilot_model,
            max_tokens=4000,
            system=[{"type": "text", "text": COACH_PROMPT}],
            messages=[{"role": "user", "content": json.dumps(record, default=str)}],
            output_config={"effort": "low"},
        )
    except anthropic.APIStatusError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"AI provider error ({exc.status_code})") from exc
    except anthropic.APIConnectionError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Could not reach the AI provider") from exc
    if resp.stop_reason == "refusal":
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The AI model declined this request")
    text = "\n".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    review = dict(t.review or {})
    review["ai"] = {
        "text": text,
        "model": resp.model,
        "generated_at": datetime.now(UTC).isoformat(),
        "grounding": "Generated only from the stored record, context, findings and scenarios shown.",
    }
    t.review = review
    await record_audit(
        session,
        action="journal.ai_review",
        outcome="success",
        actor_id=user.id,
        details={"trade_id": t.id, "model": resp.model},
    )
    await session.commit()
    return trade_detail(t)
