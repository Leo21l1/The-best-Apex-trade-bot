"""Historical candle storage and timeframe aggregation (PostgreSQL)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.marketdata.types import BASE_TIMEFRAME, Candle, Timeframe
from app.models.candle import CandleRow

_CHUNK = 2000


async def upsert_candles(session: AsyncSession, candles: Sequence[Candle]) -> int:
    """Insert closed 1-minute bars. Re-delivered bars overwrite (corrections), never duplicate."""
    rows = [
        {
            "provider": c.provider,
            "symbol": c.symbol,
            "ts": c.ts,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
            "quality": c.quality.value,
        }
        for c in candles
        if c.is_closed and c.timeframe == BASE_TIMEFRAME
    ]
    for i in range(0, len(rows), _CHUNK):
        stmt = insert(CandleRow).values(rows[i : i + _CHUNK])
        stmt = stmt.on_conflict_do_update(
            index_elements=["provider", "symbol", "ts"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "quality": stmt.excluded.quality,
                "ingested_at": func.now(),
            },
        )
        await session.execute(stmt)
    return len(rows)


@dataclass(frozen=True, slots=True)
class Bar:
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    #: Number of 1-minute bars aggregated into this bar.
    count: int


# Bucket expressions. Intraday buckets are aligned to the 18:00 ET session open so 4h bars
# start with the session. Daily/weekly bars group by TRADING day (18:00 ET roll).
_LOCAL = "(ts AT TIME ZONE 'America/New_York')"
_BUCKETS: dict[Timeframe, str] = {
    tf: (
        f"date_bin(interval '{tf.minutes} minutes', {_LOCAL}, timestamp '2000-01-02 18:00')"
        " AT TIME ZONE 'America/New_York'"
    )
    for tf in (
        Timeframe.M1,
        Timeframe.M3,
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.M30,
        Timeframe.H1,
        Timeframe.H4,
    )
}
_TRADING_DAY = f"date_trunc('day', {_LOCAL} + interval '6 hours')"
_BUCKETS[Timeframe.D1] = f"{_TRADING_DAY} AT TIME ZONE 'UTC'"
_BUCKETS[Timeframe.W1] = f"date_trunc('week', {_TRADING_DAY}) AT TIME ZONE 'UTC'"


async def query_bars(
    session: AsyncSession,
    *,
    provider: str,
    symbol: str,
    timeframe: Timeframe,
    limit: int = 500,
    before: datetime | None = None,
) -> list[Bar]:
    """Most recent ``limit`` bars (oldest first), aggregated from stored 1-minute bars.

    Daily/weekly bar timestamps are midnight UTC of the trading date (week: its Monday).
    """
    bucket = _BUCKETS[timeframe]  # fixed whitelist of SQL fragments, never user input
    sql = text(
        f"""
        SELECT bucket,
               (array_agg(open ORDER BY ts ASC))[1]  AS open,
               max(high)                             AS high,
               min(low)                              AS low,
               (array_agg(close ORDER BY ts DESC))[1] AS close,
               sum(volume)                           AS volume,
               count(*)                              AS n
        FROM (
            SELECT ts, open, high, low, close, volume, {bucket} AS bucket
            FROM candles
            WHERE provider = :provider AND symbol = :symbol
              AND (CAST(:before AS timestamptz) IS NULL OR ts < CAST(:before AS timestamptz))
        ) s
        GROUP BY bucket
        ORDER BY bucket DESC
        LIMIT :limit
        """  # noqa: S608
    )
    res = await session.execute(sql, {"provider": provider, "symbol": symbol, "before": before, "limit": limit})
    bars = [
        Bar(
            ts=r.bucket,
            open=r.open,
            high=r.high,
            low=r.low,
            close=r.close,
            volume=int(r.volume),
            count=int(r.n),
        )
        for r in res
    ]
    bars.reverse()
    return bars


async def last_bar_times(session: AsyncSession, provider: str) -> dict[str, datetime]:
    res = await session.execute(
        select(CandleRow.symbol, func.max(CandleRow.ts))
        .where(CandleRow.provider == provider)
        .group_by(CandleRow.symbol)
    )
    return {sym: ts for sym, ts in res}


async def bar_times(
    session: AsyncSession, provider: str, symbol: str, start: datetime, end: datetime
) -> list[datetime]:
    res = await session.scalars(
        select(CandleRow.ts)
        .where(
            CandleRow.provider == provider,
            CandleRow.symbol == symbol,
            CandleRow.ts >= start,
            CandleRow.ts <= end,
        )
        .order_by(CandleRow.ts)
    )
    return list(res)


async def latest_closes(session: AsyncSession, provider: str) -> dict[str, CandleRow]:
    """Latest stored 1-minute bar per symbol."""
    res = await session.scalars(
        select(CandleRow)
        .where(CandleRow.provider == provider)
        .distinct(CandleRow.symbol)
        .order_by(CandleRow.symbol, CandleRow.ts.desc())
    )
    return {row.symbol: row for row in res}
