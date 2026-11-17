"""Read-only market-data endpoints. Every price-bearing response carries provenance."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, SessionDep
from app.core.config import get_settings
from app.marketdata.calendar import expected_minutes, is_market_open
from app.marketdata.instruments import INSTRUMENTS, get_instrument
from app.marketdata.status import read_feed_status, symbol_freshness
from app.marketdata.store import bar_times, last_bar_times, latest_closes, query_bars
from app.marketdata.types import DataQuality, Timeframe
from app.marketdata.validation import find_gaps
from app.schemas.market import (
    BarOut,
    CandlesOut,
    FeedStatusOut,
    GapOut,
    GapReport,
    InstrumentOut,
    Provenance,
    SnapshotItem,
    SnapshotOut,
    SymbolFreshness,
)

router = APIRouter(prefix="/market", tags=["market"])

_NO_PROVIDER = "No market data provider configured."
_GAP_NOTE = (
    "Missing bars are candidates: thin markets may print no trade in a minute, and exchange "
    "holidays are not modelled yet."
)


def _symbol(symbol: str) -> str:
    inst = get_instrument(symbol)
    if inst is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown symbol")
    return inst.symbol


def _unavailable(now: datetime, note: str = _NO_PROVIDER) -> Provenance:
    return Provenance(provider=None, quality=DataQuality.UNAVAILABLE, as_of=now, note=note)


async def _active() -> tuple[str | None, DataQuality, dict | None]:
    """(provider name, quality class, live feed status document)."""
    s = get_settings()
    if s.market_data_provider == "none":
        return None, DataQuality.UNAVAILABLE, None
    doc = await read_feed_status()
    reporting = doc is not None and time.time() - float(doc.get("updated_at", 0)) <= 60
    if reporting and doc is not None:
        return s.market_data_provider, DataQuality(doc["quality"]), doc
    # Worker not reporting: stored bars may still be shown, but only as history.
    quality = DataQuality.SIMULATED if s.market_data_provider == "simulated" else DataQuality.HISTORICAL
    return s.market_data_provider, quality, None


@router.get("/instruments", response_model=list[InstrumentOut])
async def instruments(_: CurrentUser) -> list[InstrumentOut]:
    return [
        InstrumentOut(
            symbol=i.symbol,
            name=i.name,
            exchange=i.exchange,
            asset_class=i.asset_class,
            tick_size=float(i.tick_size),
            tick_value=float(i.tick_value),
            point_value=float(i.point_value),
            is_micro=i.is_micro,
            sibling=i.sibling,
        )
        for i in INSTRUMENTS.values()
    ]


@router.get("/candles", response_model=CandlesOut)
async def candles(
    _: CurrentUser,
    session: SessionDep,
    symbol: str,
    timeframe: Timeframe = Timeframe.M1,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    before: datetime | None = None,
) -> CandlesOut:
    sym = _symbol(symbol)
    now = datetime.now(UTC)
    provider, quality, doc = await _active()
    if provider is None:
        return CandlesOut(symbol=sym, timeframe=timeframe.value, provenance=_unavailable(now), bars=[])
    if before is not None and before.tzinfo is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "before must include a timezone")
    bars = await query_bars(
        session, provider=provider, symbol=sym, timeframe=timeframe, limit=limit, before=before
    )
    last = (await last_bar_times(session, provider)).get(sym)
    s = get_settings()
    fresh = symbol_freshness(last.isoformat() if last else None, now, s.market_data_stale_seconds)
    note = None
    if doc is None:
        note = "Ingestion worker is not reporting; showing stored bars only."
    elif not bars:
        note = "No bars stored for this symbol yet."
    return CandlesOut(
        symbol=sym,
        timeframe=timeframe.value,
        provenance=Provenance(
            provider=provider,
            quality=quality,
            as_of=now,
            last_bar=last,
            stale=bool(fresh["stale"]) or doc is None,
            note=note,
        ),
        bars=[
            BarOut(
                t=int(b.ts.timestamp()),
                o=float(b.open),
                h=float(b.high),
                l=float(b.low),
                c=float(b.close),
                v=b.volume,
            )
            for b in bars
        ],
    )


@router.get("/snapshot", response_model=SnapshotOut)
async def snapshot(_: CurrentUser, session: SessionDep) -> SnapshotOut:
    """Latest stored 1-minute close per configured symbol."""
    now = datetime.now(UTC)
    s = get_settings()
    provider, quality, doc = await _active()
    if provider is None:
        return SnapshotOut(
            provenance=_unavailable(now),
            items=[
                SnapshotItem(symbol=sym, last=None, last_bar=None, age_seconds=None, stale=True)
                for sym in s.market_data_symbols
            ],
        )
    rows = await latest_closes(session, provider)
    items = []
    for sym in s.market_data_symbols:
        row = rows.get(sym)
        f = symbol_freshness(row.ts.isoformat() if row else None, now, s.market_data_stale_seconds)
        items.append(
            SnapshotItem(
                symbol=sym,
                last=float(row.close) if row else None,
                last_bar=row.ts if row else None,
                age_seconds=f["age_seconds"],
                stale=bool(f["stale"]) or doc is None,
            )
        )
    note = None if doc else "Ingestion worker is not reporting; values are last stored bars."
    return SnapshotOut(
        provenance=Provenance(provider=provider, quality=quality, as_of=now, stale=doc is None, note=note),
        items=items,
    )


@router.get("/status", response_model=FeedStatusOut)
async def feed_status(_: CurrentUser, session: SessionDep) -> FeedStatusOut:
    now = datetime.now(UTC)
    s = get_settings()
    provider, quality, doc = await _active()
    last = await last_bar_times(session, provider) if provider else {}
    symbols = []
    for sym in s.market_data_symbols:
        # Prefer the live feed's view; fall back to what is stored.
        iso = (doc or {}).get("last_event", {}).get(sym) or (last[sym].isoformat() if sym in last else None)
        f = symbol_freshness(iso, now, s.market_data_stale_seconds)
        not_reporting = provider is not None and not doc
        symbols.append(
            SymbolFreshness(
                symbol=sym,
                last_bar=f["last_bar"],
                age_seconds=f["age_seconds"],
                stale=bool(f["stale"]) or not_reporting,
            )
        )
    d = doc or {}
    return FeedStatusOut(
        configured_provider=s.market_data_provider,
        reporting=doc is not None,
        state=d.get("state", "disabled" if provider is None else "not_reporting"),
        quality=quality,
        verified=d.get("verified"),
        description=d.get("description"),
        market_open=is_market_open(now),
        reconnects=int(d.get("reconnects", 0)),
        duplicates_dropped=int(d.get("duplicates_dropped", 0)),
        invalid_dropped=int(d.get("invalid_dropped", 0)),
        bars_stored=int(d.get("bars_stored", 0)),
        last_error=d.get("last_error"),
        stale_after_seconds=s.market_data_stale_seconds,
        symbols=symbols,
        as_of=now,
    )


@router.get("/gaps", response_model=GapReport)
async def gaps(
    _: CurrentUser,
    session: SessionDep,
    symbol: str,
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
) -> GapReport:
    """Missing 1-minute bars within the last ``hours`` (ending at the newest stored bar)."""
    sym = _symbol(symbol)
    now = datetime.now(UTC)
    provider, _q, _doc = await _active()
    start = now - timedelta(hours=hours)
    if provider is None:
        return GapReport(
            symbol=sym,
            provider=None,
            window_start=start,
            window_end=now,
            expected_bars=0,
            present_bars=0,
            missing_bars=0,
            gaps=[],
            note=_NO_PROVIDER,
        )
    last = (await last_bar_times(session, provider)).get(sym)
    end = min(now, last) if last else start
    present = await bar_times(session, provider, sym, start, end)
    expected = sum(1 for _ in expected_minutes(start, end)) if last else 0
    found = find_gaps(present, start, end) if last else []
    return GapReport(
        symbol=sym,
        provider=provider,
        window_start=start,
        window_end=end,
        expected_bars=expected,
        present_bars=len(present),
        missing_bars=sum(g.missing_bars for g in found),
        gaps=[GapOut(start=g.start, end=g.end, missing_bars=g.missing_bars) for g in found[:200]],
        note=_GAP_NOTE,
    )
