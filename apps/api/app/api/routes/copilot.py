"""AI copilot, economic calendar and session briefing endpoints."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from anthropic import AsyncAnthropic
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.ai.copilot import CopilotUnavailable, ask, build_client
from app.ai.tools import ToolContext
from app.api.deps import AdminUser, CurrentUser, SessionDep, client_ip
from app.core.config import get_settings
from app.core.ratelimit import rate_limiter
from app.marketdata.calendar import ET, is_market_open
from app.marketdata.instruments import INSTRUMENTS
from app.models.calendar import EconomicEvent
from app.models.copilot import CopilotExchange
from app.models.signal import SignalRow
from app.research.indicators import is_rth
from app.services.account import current_state
from app.services.audit import record_audit
from app.services.calendar import (
    ParsedEvent,
    classify,
    event_to_dict,
    events_between,
    parse_ics,
    upsert_events,
)

router = APIRouter(tags=["copilot"])


# --- copilot ---------------------------------------------------------------------------------


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=20_000)


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=40)


def get_copilot_client() -> AsyncAnthropic:
    """Dependency so tests can substitute a client backed by a mock transport."""
    try:
        return build_client(get_settings())
    except CopilotUnavailable as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("/copilot/status")
async def copilot_status(_: CurrentUser) -> dict[str, Any]:
    s = get_settings()
    return {
        "configured": s.anthropic_api_key is not None,
        "model": s.copilot_model,
        "effort": s.copilot_effort,
        "can_place_orders": False,
        "data_sent": "Questions and the results of the read-only tools the model calls (market data, scanner "
        "output, rule values and entered account balances) are sent to Anthropic's API. "
        "Credentials and session tokens are never sent.",
    }


@router.post("/copilot/ask")
async def copilot_ask(
    body: AskIn,
    user: CurrentUser,
    request: Request,
    session: SessionDep,
    client: Annotated[AsyncAnthropic, Depends(get_copilot_client)],
) -> dict[str, Any]:
    s = get_settings()
    user_id = user.id  # read once: the ORM object must not be touched after a rollback
    if not await rate_limiter.hit("copilot", str(user_id), s.copilot_rate_limit_per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Copilot rate limit reached; wait a minute.")
    ctx = ToolContext(session=session, settings=s, now=datetime.now(UTC))
    exchange = CopilotExchange(user_id=user_id, question=body.question)
    try:
        result = await ask(client, s, ctx, body.question, [t.model_dump() for t in body.history])
    except CopilotUnavailable as exc:
        exchange.error = str(exc)[:500]
        session.add(exchange)
        await session.commit()
        raise HTTPException(exc.status, str(exc)) from exc
    exchange.answer = result.answer
    exchange.tool_calls = result.tool_calls
    exchange.model = result.model
    exchange.stop_reason = result.stop_reason
    exchange.input_tokens, exchange.output_tokens = result.input_tokens, result.output_tokens
    exchange.cache_read_tokens = result.cache_read_tokens
    session.add(exchange)
    await record_audit(
        session,
        action="copilot.ask",
        outcome="success",
        actor_id=user_id,
        ip=client_ip(request),
        details={"tools": [c["name"] for c in result.tool_calls], "model": result.model},
    )
    await session.commit()
    return {"id": exchange.id, **asdict(result)}


@router.get("/copilot/history")
async def copilot_history(
    user: CurrentUser, session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 30
) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(CopilotExchange)
            .where(CopilotExchange.user_id == user.id)
            .order_by(CopilotExchange.id.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat(),
            "question": r.question,
            "answer": r.answer,
            "tool_calls": r.tool_calls,
            "model": r.model,
            "stop_reason": r.stop_reason,
            "error": r.error,
        }
        for r in reversed(rows)
    ]


# --- economic calendar -----------------------------------------------------------------------


class IcsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ics: str = Field(min_length=1, max_length=2_000_000)
    source_name: str = Field(default="uploaded file", max_length=200)
    dry_run: bool = False


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ts: datetime
    title: str = Field(min_length=2, max_length=300)
    importance: str | None = Field(default=None, pattern="^(high|medium|low)$")
    affected: list[str] | None = Field(default=None, max_length=12)

    @field_validator("ts")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("ts must include a timezone")
        return v

    @field_validator("affected")
    @classmethod
    def _symbols(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        out = [s.upper() for s in v]
        bad = [s for s in out if s not in INSTRUMENTS]
        if bad:
            raise ValueError(f"unknown symbols: {', '.join(bad)}")
        return out


@router.get("/calendar")
async def calendar(
    _: CurrentUser,
    session: SessionDep,
    days: Annotated[int, Query(ge=0, le=60)] = 7,
    back_hours: Annotated[int, Query(ge=0, le=24 * 30)] = 24,
) -> dict[str, Any]:
    s = get_settings()
    now = datetime.now(UTC)
    events = await events_between(session, now - timedelta(hours=back_hours), now + timedelta(days=days or 1))
    total = len((await session.scalars(select(EconomicEvent.id).limit(1))).all())
    return {
        "as_of": now.isoformat(),
        "window": {
            "pre_minutes": s.calendar_pre_event_minutes,
            "post_minutes": s.calendar_post_event_minutes,
        },
        "apex_restrictions": "Unverified. See the 'News trading restrictions' rule on Apex Account Health.",
        "empty_calendar": total == 0,
        "events": [event_to_dict(e, now, s.calendar_pre_event_minutes, s.calendar_post_event_minutes) for e in events],
        "note": "Importance is a fixed classification of event types by typical relevance to these futures, "
        "not a forecast. PROJECT APEX does not attribute market moves to events.",
    }


@router.post("/calendar/import-ics")
async def import_ics(body: IcsIn, user: AdminUser, request: Request, session: SessionDep) -> dict[str, Any]:
    events, errors = parse_ics(body.ics)
    written = 0
    if not body.dry_run and events:
        written = await upsert_events(session, events, "ics_file", body.source_name)
        await record_audit(
            session,
            action="calendar.import",
            outcome="success",
            actor_id=user.id,
            ip=client_ip(request),
            details={"events": written, "source": body.source_name},
        )
        await session.commit()
    preview = []
    for e in events[:20]:
        category, importance, affected = classify(e.title)
        preview.append(
            {
                "ts": e.ts.isoformat(),
                "title": e.title,
                "category": category,
                "importance": importance,
                "affected": affected,
            }
        )
    return {
        "dry_run": body.dry_run,
        "parsed": len(events),
        "written": written,
        "errors": errors[:50],
        "preview": preview,
    }


@router.post("/calendar/events", status_code=status.HTTP_201_CREATED)
async def add_event(body: EventIn, user: AdminUser, request: Request, session: SessionDep) -> dict[str, Any]:
    ev = ParsedEvent(uid=f"manual-{uuid.uuid4().hex}", ts=body.ts.astimezone(UTC), title=body.title)
    await upsert_events(session, [ev], "manual", f"entered by {user.email}")
    row = await session.scalar(select(EconomicEvent).where(EconomicEvent.uid == ev.uid))
    assert row is not None
    if body.importance:
        row.importance = body.importance
    if body.affected is not None:
        row.affected = body.affected
    await record_audit(
        session,
        action="calendar.add",
        outcome="success",
        actor_id=user.id,
        ip=client_ip(request),
        details={"title": body.title, "ts": body.ts.isoformat()},
    )
    await session.commit()
    s = get_settings()
    return event_to_dict(row, datetime.now(UTC), s.calendar_pre_event_minutes, s.calendar_post_event_minutes)


@router.delete("/calendar/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(event_id: int, user: AdminUser, request: Request, session: SessionDep) -> Response:
    row = await session.get(EconomicEvent, event_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    await session.delete(row)
    await record_audit(
        session,
        action="calendar.delete",
        outcome="success",
        actor_id=user.id,
        ip=client_ip(request),
        details={"title": row.title},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- session briefing (deterministic, no AI) -------------------------------------------------


def session_phase(now: datetime) -> str:
    local = now.astimezone(ET)
    if not is_market_open(now):
        return "closed"
    if is_rth(now):
        return "live"
    return "premarket" if local.hour < 9 or (local.hour == 9 and local.minute < 30) else "postmarket"


@router.get("/briefing")
async def briefing(_: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Facts for the premarket / live / postmarket workflow, assembled without any AI."""
    import json

    from app.core.redis import get_redis
    from app.research.scanner import SCAN_KEY

    s = get_settings()
    now = datetime.now(UTC)
    rules, state = await current_state(session, now)
    raw = await get_redis().get(SCAN_KEY)
    scan = json.loads(raw) if raw else None
    local_midnight = now.astimezone(ET).replace(hour=0, minute=0, second=0, microsecond=0)
    events = await events_between(session, now - timedelta(hours=2), local_midnight.astimezone(UTC) + timedelta(days=1))
    active = (
        await session.scalars(
            select(SignalRow).where(SignalRow.status == "active", SignalRow.provider == s.market_data_provider)
        )
    ).all()

    checklist: list[dict[str, str]] = []

    def check(ok: bool, text_ok: str, text_bad: str, severity: str = "warning") -> None:
        checklist.append({"status": "ok" if ok else severity, "text": text_ok if ok else text_bad})

    quality = (scan or {}).get("quality", "unavailable")
    check(
        quality in ("live", "delayed"),
        f"Market data is {quality.upper()}.",
        f"Market data is {quality.upper()}; no real-market analysis is possible.",
        "critical",
    )
    check(
        rules.verified,
        "Apex rule profile verified.",
        "Apex rule profile UNVERIFIED; nothing can be called compliant.",
    )
    check(
        state.known and not state.stale,
        "Account snapshot is current.",
        "Account snapshot missing or stale; enter your balance before trading.",
    )
    high = [e for e in events if e.importance == "high" and e.ts >= now]
    check(
        not high,
        "No high-importance events remaining today.",
        f"{len(high)} high-importance event(s) today: "
        + ", ".join(f"{e.title} {e.ts.astimezone(ET):%H:%M} ET" for e in high[:4]),
        "warning",
    )

    symbols = []
    for sym in (scan or {}).get("symbols", []):
        lv = sym["levels"]
        symbols.append(
            {
                "symbol": sym["symbol"],
                "last": sym["last"],
                "regime": sym["regime"],
                "vs_vwap": sym["vs_vwap"],
                "prior_day_high": lv["prior_day_high"],
                "prior_day_low": lv["prior_day_low"],
                "prior_day_close": lv["prior_day_close"],
                "overnight_high": lv["overnight_high"],
                "overnight_low": lv["overnight_low"],
                "opening_range_high": lv["opening_range_high"],
                "opening_range_low": lv["opening_range_low"],
            }
        )
    return {
        "as_of": now.isoformat(),
        "phase": session_phase(now),
        "data": {
            "provider": (scan or {}).get("provider"),
            "quality": quality,
            "scan_as_of": (scan or {}).get("as_of"),
        },
        "checklist": checklist,
        "markets": symbols,
        "events_today": [
            event_to_dict(e, now, s.calendar_pre_event_minutes, s.calendar_post_event_minutes) for e in events
        ],
        "account": {
            "known": state.known,
            "stale": state.stale,
            "risk_budget": float(state.risk_budget) if state.risk_budget is not None else None,
            "drawdown_room": float(state.drawdown_room) if state.drawdown_room is not None else None,
            "daily_loss_remaining": float(state.daily_loss_remaining)
            if state.daily_loss_remaining is not None
            else None,
        },
        "active_candidates": len(active),
        "ranked_opportunities": 0,
        "note": "Assembled deterministically from stored data. Overnight = 18:00 ET to 09:30 ET.",
    }
