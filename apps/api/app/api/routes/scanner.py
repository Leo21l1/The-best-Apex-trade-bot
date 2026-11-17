"""Scanner, opportunity, strategy and alert endpoints (read-only analysis)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from redis.exceptions import RedisError
from sqlalchemy import func, select, update

from app.api.deps import CurrentUser, SessionDep
from app.core.config import get_settings
from app.core.redis import get_redis
from app.models.signal import AlertRow, SignalRow
from app.research.scanner import SCAN_KEY
from app.research.scoring import SCORE_NOTE
from app.research.validation import STANDARDS, strategy_validation
from app.services.alerts import alert_to_wire
from app.strategies.library import STRATEGIES

router = APIRouter(tags=["scanner"])


async def _latest_scan() -> dict[str, Any] | None:
    try:
        raw = await get_redis().get(SCAN_KEY)
    except (RedisError, OSError):
        return None
    return json.loads(raw) if raw else None


def _empty(note: str) -> dict[str, Any]:
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "provider": None,
        "quality": "unavailable",
        "market_open": None,
        "symbols": [],
        "note": note,
    }


@router.get("/scanner/radar")
async def radar(_: CurrentUser) -> dict[str, Any]:
    """Latest scan: per-symbol regime, levels, per-timeframe state and strategy checks."""
    doc = await _latest_scan()
    if doc is None:
        return _empty("No scan available. The scanner runs in the worker process.")
    doc.pop("opportunities", None)
    return doc


def _signal_out(row: SignalRow) -> dict[str, Any]:
    return {
        **row.detail,
        "id": row.id,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "status_at": row.status_at.isoformat() if row.status_at else None,
    }


@router.get("/opportunities")
async def opportunities(
    _: CurrentUser,
    session: SessionDep,
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
    symbol: str | None = None,
    strategy_id: str | None = None,
) -> dict[str, Any]:
    """Ranked opportunities plus unranked research candidates.

    ``ranked`` only contains setups from validated strategies on live/delayed data that passed
    risk checks. It is expected to be empty until those conditions are met.
    """
    provider = get_settings().market_data_provider
    q = select(SignalRow).where(
        SignalRow.provider == provider,
        SignalRow.created_at >= datetime.now(UTC) - timedelta(hours=hours),
    )
    if symbol:
        q = q.where(SignalRow.symbol == symbol.upper())
    if strategy_id:
        q = q.where(SignalRow.strategy_id == strategy_id)
    rows = (await session.scalars(q.order_by(SignalRow.created_at.desc()).limit(300))).all()
    items = [_signal_out(r) for r in rows]
    ranked = sorted(
        (i for i in items if i.get("rank_eligible") and i["status"] == "active"),
        key=lambda i: i["score"],
        reverse=True,
    )
    scan = await _latest_scan()
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "provider": None if provider == "none" else provider,
        "quality": (scan or {}).get("quality", "unavailable"),
        "ranked": ranked,
        "candidates": [i for i in items if not i.get("rank_eligible")],
        "score_note": SCORE_NOTE,
        "ranking_rule": "Ranked = validated strategy AND live/delayed data AND risk-eligible, "
        "ordered by setup score. Zero ranked opportunities is a valid result.",
    }


@router.get("/strategies")
async def strategies(_: CurrentUser) -> dict[str, Any]:
    out = []
    for strat in STRATEGIES:
        spec = asdict(strat.spec)
        spec["eligible_instruments"] = list(strat.spec.eligible_instruments)
        spec["validation"] = asdict(strategy_validation(strat.spec.id))
        out.append(spec)
    return {"strategies": out, "validation_standards": asdict(STANDARDS)}


@router.get("/alerts")
async def alerts(
    _: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    unread_only: bool = False,
) -> dict[str, Any]:
    q = select(AlertRow)
    if unread_only:
        q = q.where(AlertRow.read_at.is_(None))
    rows = (await session.scalars(q.order_by(AlertRow.id.desc()).limit(limit))).all()
    unread = await session.scalar(
        select(func.count()).select_from(AlertRow).where(AlertRow.read_at.is_(None))
    )
    return {"unread": int(unread or 0), "alerts": [alert_to_wire(r) for r in rows]}


@router.post("/alerts/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def read_all(_: CurrentUser, session: SessionDep) -> Response:
    await session.execute(
        update(AlertRow).where(AlertRow.read_at.is_(None)).values(read_at=datetime.now(UTC))
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/alerts/{alert_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def read_one(alert_id: int, _: CurrentUser, session: SessionDep) -> Response:
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    if row.read_at is None:
        row.read_at = datetime.now(UTC)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
