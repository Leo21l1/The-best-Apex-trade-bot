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
from app.api.routes.account import cost_model_for
from app.core.config import get_settings
from app.core.redis import get_redis
from app.marketdata.instruments import INSTRUMENTS
from app.models.signal import AlertRow, SignalRow
from app.research.scanner import SCAN_KEY
from app.research.scoring import SCORE_NOTE
from app.research.validation import STANDARDS, strategy_validation
from app.risk.engine import check_trade
from app.services.account import current_state
from app.services.alerts import alert_to_wire
from app.services.calendar import news_exposure
from app.services.research import load_validations
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
    rules, state = await current_state(session)
    validations = await load_validations(session)
    await session.commit()
    s = get_settings()
    now = datetime.now(UTC)
    news_cache: dict[str, dict[str, Any]] = {}
    for item in items:
        sym = item["symbol"]
        if sym not in news_cache:
            news_cache[sym] = await news_exposure(
                session, sym, now, s.calendar_pre_event_minutes, s.calendar_post_event_minutes
            )
        item["news"] = news_cache[sym]
        item["news_exposure"] = news_cache[sym]["summary"]
        item["risk"] = _risk(rules, state, item)
        item["risk_eligibility"] = item["risk"]["verdict"]
        val = strategy_validation(item["strategy_id"], validations)
        item["validation"] = {
            "validated": val.validated,
            "reason": val.reason,
            "sample_size": val.sample_size,
            "expectancy_r": val.expectancy_r,
        }
        blockers = []
        if not val.validated:
            blockers.append(f"Strategy not validated: {val.reason}")
        if item["quality"] not in ("live", "delayed"):
            blockers.append(f"Data class is {item['quality'].upper()}; ranking requires live or delayed data.")
        if not rules.verified:
            blockers.append("Apex rule profile is unverified.")
        if item["news"]["status"] == "inside_window":
            blockers.append("Inside an economic event window.")
        risk_ok = item["risk"]["verdict"] == "no_violation_found" and (item["risk"]["max_size"] or 0) >= 1
        if not risk_ok:
            blockers.append(_risk_blocker(item["risk"]))
        item["rank_blockers"] = blockers
        item["rank_eligible"] = not blockers
    # Rank by validated out-of-sample expectancy (R), then by setup score.
    ranked = sorted(
        (i for i in items if i["rank_eligible"] and i["status"] == "active"),
        key=lambda i: (i["validation"]["expectancy_r"] or 0.0, i["score"]),
        reverse=True,
    )
    scan = await _latest_scan()
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "provider": None if provider == "none" else provider,
        "quality": (scan or {}).get("quality", "unavailable"),
        "ranked": ranked,
        "candidates": [i for i in items if not i["rank_eligible"]],
        "score_note": SCORE_NOTE,
        "ranking_rule": "Ranked = validated strategy AND live/delayed data AND verified rule profile AND "
        "risk-eligible AND outside event windows, ordered by validated expectancy then setup score. "
        "Zero ranked opportunities is a valid result.",
    }


def _risk(rules: Any, state: Any, item: dict[str, Any]) -> dict[str, Any]:
    """Deterministic one-contract check of a candidate against the current account."""
    from decimal import Decimal

    inst = INSTRUMENTS[item["symbol"]]
    try:
        c = check_trade(
            rules,
            state,
            inst,
            item["direction"],
            Decimal(str(item["entry"])),
            Decimal(str(item["stop"])),
            Decimal(str(item["targets"][0])),
            1,
            cost_model_for(inst.symbol),
            INSTRUMENTS,
            Decimal(str(get_settings().risk_max_trade_fraction)),
        )
    except ValueError as exc:
        return {
            "verdict": "invalid",
            "max_size": None,
            "violations": [str(exc)],
            "warnings": [],
            "summary": str(exc),
            "budget_used_pct": None,
        }
    return {
        "verdict": c.verdict,
        "max_size": c.max_size,
        "violations": list(c.violations),
        "warnings": list(c.warnings),
        "summary": c.summary,
        "budget_used_pct": float(c.budget_used_pct) if c.budget_used_pct is not None else None,
    }


def _risk_blocker(risk: dict[str, Any]) -> str:
    if risk["verdict"] == "violation":
        return "Risk check: " + "; ".join(risk["violations"])
    if risk["verdict"] == "cannot_confirm":
        return "Risk check: account data missing or stale, so eligibility cannot be confirmed."
    if risk["verdict"] == "invalid":
        return "Risk check failed: " + risk["summary"]
    if risk["max_size"] == 0:
        return "Risk check: the remaining risk budget does not cover one contract."
    return "Risk check passed on configured values, but the rule profile must be verified to rank."


@router.get("/strategies")
async def strategies(_: CurrentUser, session: SessionDep) -> dict[str, Any]:
    validations = await load_validations(session)
    out = []
    for strat in STRATEGIES:
        spec = asdict(strat.spec)
        spec["eligible_instruments"] = list(strat.spec.eligible_instruments)
        spec["validation"] = asdict(strategy_validation(strat.spec.id, validations))
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
    unread = await session.scalar(select(func.count()).select_from(AlertRow).where(AlertRow.read_at.is_(None)))
    return {"unread": int(unread or 0), "alerts": [alert_to_wire(r) for r in rows]}


@router.post("/alerts/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def read_all(_: CurrentUser, session: SessionDep) -> Response:
    await session.execute(update(AlertRow).where(AlertRow.read_at.is_(None)).values(read_at=datetime.now(UTC)))
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
