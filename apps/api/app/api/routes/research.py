"""Research studies (read-only): leaderboards, trade logs, exports and the strategy inventory."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi import Path as PathParam
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models.research import ResearchStudy
from app.research.inventory import inventory
from app.services.research_study import LEADER_COLUMNS, TRADE_COLUMNS, headline, leaderboard_rows, to_csv

router = APIRouter(prefix="/research", tags=["research"])
StudyId = Annotated[int, PathParam(ge=1, le=2**31 - 1)]


def _summary(r: ResearchStudy) -> dict[str, Any]:
    res = r.result
    counts: dict[str, int] = {}
    for row in res.get("rows", []):
        counts[row["evidence"]] = counts.get(row["evidence"], 0) + 1
    return {
        "id": r.id,
        "created_at": r.created_at.isoformat(),
        "symbol": r.symbol,
        "data_class": r.data_class,
        "usable_sessions": res.get("data", {}).get("usable_sessions"),
        "first_bar": res.get("data", {}).get("earliest_bar"),
        "last_bar": res.get("data", {}).get("latest_bar"),
        "preliminary": res.get("plan", {}).get("preliminary"),
        "evidence_counts": counts,
        "trades": len(r.trades or []),
    }


@router.get("/studies")
async def list_studies(_: CurrentUser, session: SessionDep, symbol: str | None = None) -> list[dict[str, Any]]:
    q = select(ResearchStudy).order_by(ResearchStudy.created_at.desc()).limit(50)
    if symbol:
        q = q.where(ResearchStudy.symbol == symbol.upper())
    return [_summary(r) for r in (await session.scalars(q)).all()]


async def _get(session: SessionDep, study_id: int) -> ResearchStudy:
    r = await session.get(ResearchStudy, study_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Study not found")
    return r


@router.get("/studies/{study_id}")
async def get_study(_: CurrentUser, session: SessionDep, study_id: StudyId) -> dict[str, Any]:
    r = await _get(session, study_id)
    return {**_summary(r), "params": r.params, "result": r.result, "headline": headline(r.result)}


@router.get("/studies/{study_id}/trades")
async def study_trades(
    _: CurrentUser,
    session: SessionDep,
    study_id: StudyId,
    strategy_id: str | None = None,
    config: str | None = None,
    period: Literal["research", "walk_forward", "holdout"] | None = None,
    direction: Literal["long", "short"] | None = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    r = await _get(session, study_id)
    rows = [
        t
        for t in r.trades or []
        if (strategy_id is None or t.get("strategy_id") == strategy_id)
        and (config is None or t.get("config") == config)
        and (period is None or t.get("period") == period)
        and (direction is None or t.get("direction") == direction)
    ]
    return {"total": len(rows), "trades": rows[offset : offset + limit]}


@router.get("/studies/{study_id}/export/{kind}.csv", response_class=PlainTextResponse)
async def export_csv(
    _: CurrentUser, session: SessionDep, study_id: StudyId, kind: Literal["leaderboard", "trades"]
) -> PlainTextResponse:
    r = await _get(session, study_id)
    body = (
        to_csv(leaderboard_rows(r.result), LEADER_COLUMNS)
        if kind == "leaderboard"
        else to_csv(r.trades or [], TRADE_COLUMNS)
    )
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{kind}_{r.symbol}_{r.id}.csv"'},
    )


@router.get("/inventory")
async def strategy_inventory(_: CurrentUser) -> list[dict[str, Any]]:
    return inventory()
