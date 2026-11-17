"""Trading Session Intelligence and the readiness checklist (read-only)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, SessionDep
from app.marketdata.instruments import get_instrument
from app.services.readiness import readiness
from app.services.session_intel import session_context

router = APIRouter(prefix="/session", tags=["session"])
Style = Literal["intraday", "scalping", "momentum", "orb", "afternoon"]


def _sym(symbol: str) -> str:
    inst = get_instrument(symbol)
    if inst is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown symbol")
    return inst.symbol


@router.get("/context")
async def context(_: CurrentUser, symbol: str = "ES") -> dict[str, Any]:
    """Clock and exchange context only (New York time, window, market open, next transition)."""
    return session_context(_sym(symbol), datetime.now(UTC))


@router.get("/readiness")
async def get_readiness(
    _: CurrentUser, session: SessionDep, symbol: str = "ES", style: Style = "intraday"
) -> dict[str, Any]:
    """Session context, data status, news, risk and the eight-condition checklist (cached 15 s)."""
    return await readiness(session, _sym(symbol), style)
