"""Market analyzer and news intelligence (read-only; analysis only, never orders)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, SessionDep
from app.core.config import get_settings
from app.services import analyzer, news_intel

router = APIRouter(tags=["analyzer"])


def _symbol(symbol: str) -> str:
    sym = symbol.upper()
    if sym not in get_settings().analyzer_symbols:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{symbol} is not an analyzer instrument")
    return sym


@router.get("/analyzer")
async def overview(_: CurrentUser, session: SessionDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    cards = [analyzer.card(await analyzer.snapshot(session, sym, now)) for sym in get_settings().analyzer_symbols]
    return {
        "as_of": now.isoformat(),
        "cards": cards,
        "note": "MNQ and NQ track the same Nasdaq-100 index: their agreement is not independent confirmation.",
        "disclaimer": analyzer.DISCLAIMER,
    }


@router.get("/analyzer/{symbol}")
async def detail(symbol: str, _: CurrentUser, session: SessionDep) -> dict[str, Any]:
    doc = await analyzer.snapshot(session, _symbol(symbol))
    doc.pop("_stamp", None)
    return doc


@router.get("/news/intel")
async def news(_: CurrentUser, session: SessionDep, symbol: str = "ES") -> dict[str, Any]:
    return await news_intel.evaluate(session, datetime.now(UTC), _symbol(symbol))
