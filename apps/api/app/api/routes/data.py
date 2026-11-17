"""Historical data readiness and approved, cost-estimated backfill (no automatic paid downloads)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.core.config import get_settings
from app.marketdata.instruments import INSTRUMENTS
from app.marketdata.status import read_feed_status
from app.services import history

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/readiness")
async def readiness(_: CurrentUser, session: SessionDep) -> dict[str, Any]:
    s = get_settings()
    feed = await read_feed_status() or {}
    symbols = list(feed.get("symbols") or s.market_data_symbols)
    rows = [await history.coverage(session, sym) for sym in symbols if sym in INSTRUMENTS]
    quality = feed.get("quality")
    return {
        "live_provider": s.market_data_provider if s.market_data_provider != "none" else None,
        "historical_provider": s.market_data_provider if s.market_data_provider != "none" else None,
        "live_feed": {
            "state": feed.get("state", "not reporting"),
            "class": (quality or "unavailable").upper(),
            "last_error": feed.get("last_error"),
            "provider_last_error": feed.get("provider_last_error"),
            "reconnects": feed.get("reconnects"),
            "last_event": feed.get("last_event", {}),
            "contracts": feed.get("contracts", {}),
        },
        "symbols": rows,
        "backfill_job": await history.get_job(),
        "labels": "Stored history is HISTORICAL; only bars arriving on the live stream are LIVE. Simulated data is "
        "always labelled SIMULATED.",
    }


class BackfillIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: list[str] = Field(min_length=1, max_length=12)
    target_sessions: int = Field(default=history.TARGET_SESSIONS, ge=5, le=60)
    approved_cost_usd: float | None = Field(default=None, ge=0)
    acknowledge_unknown_cost: bool = False


def _symbols(v: list[str]) -> list[str]:
    out = sorted({x.upper() for x in v})
    bad = [x for x in out if x not in INSTRUMENTS]
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown symbols: {', '.join(bad)}")
    return out


@router.post("/backfill/estimate")
async def estimate(body: BackfillIn, _: AdminUser, session: SessionDep) -> dict[str, Any]:
    """Missing sessions and the provider's cost estimate. Nothing is downloaded."""
    return await history.estimate(session, _symbols(body.symbols), body.target_sessions)


@router.post("/backfill", status_code=status.HTTP_202_ACCEPTED)
async def start(body: BackfillIn, user: AdminUser, session: SessionDep) -> dict[str, Any]:
    """Start an approved backfill. Requires an approved cost >= the current estimate, or an explicit
    acknowledgement when the provider cannot estimate the cost."""
    if get_settings().market_data_provider == "none":
        raise HTTPException(status.HTTP_409_CONFLICT, "No market-data provider configured.")
    job = await history.get_job()
    if job and job["status"] in ("queued", "running"):
        raise HTTPException(status.HTTP_409_CONFLICT, "A backfill is already running.")
    syms = _symbols(body.symbols)
    est = await history.estimate(session, syms, body.target_sessions)
    cost = est["estimated_cost_usd"]
    if cost is None and not body.acknowledge_unknown_cost:
        raise HTTPException(
            status.HTTP_412_PRECONDITION_FAILED,
            "The provider could not estimate the cost; set acknowledge_unknown_cost to proceed.",
        )
    if cost is not None and cost > 0 and (body.approved_cost_usd is None or body.approved_cost_usd + 1e-9 < cost):
        raise HTTPException(
            status.HTTP_412_PRECONDITION_FAILED,
            f"Estimated cost is ${cost:.4f}; approve at least that amount to start.",
        )
    out = await history.start_job(syms, body.target_sessions, cost, user.email)
    out["estimate"] = est
    return out


@router.get("/backfill")
async def job(_: CurrentUser) -> dict[str, Any] | None:
    return await history.get_job()


@router.get("/strategy-statistics")
async def strategy_statistics(
    _: CurrentUser,
    session: SessionDep,
    symbol: str | None = None,
    strategy_id: str | None = None,
    include_simulated: bool = False,
) -> dict[str, Any]:
    rows = await history.strategy_statistics(
        session, symbol.upper() if symbol else None, strategy_id, include_simulated
    )
    return {
        "rows": rows,
        "note": "Latest completed backtest per strategy and instrument on stored data, split in-sample / "
        "out-of-sample. Segments with few trades are descriptive only; nothing here is a forecast.",
    }
