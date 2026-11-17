"""Ingestion service: backfill, streaming, reconnection, validation, dedupe, status.

Runs inside the worker process. Closed bars are persisted to PostgreSQL; every accepted bar
update is published on Redis for the API's WebSocket fan-out. The feed status document in Redis
is what the API and dashboard use for freshness monitoring.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.marketdata.base import MarketDataProvider, ProviderAuthError, ProviderError
from app.core.logging import log_event
from app.marketdata.store import last_bar_times, upsert_candles
from app.marketdata.types import Candle
from app.marketdata.validation import DuplicateFilter, sort_dedupe, validate_candle

log = logging.getLogger("apex.feed")

STATUS_KEY = "apex:md:status"
CANDLE_CHANNEL = "apex:md:candles"
STATUS_TTL_S = 120
BACKFILL_CHUNK = timedelta(hours=24)


def candle_to_wire(c: Candle) -> dict[str, Any]:
    return {
        "symbol": c.symbol,
        "timeframe": c.timeframe.value,
        "t": int(c.ts.timestamp()),
        "o": float(c.open),
        "h": float(c.high),
        "l": float(c.low),
        "c": float(c.close),
        "v": c.volume,
        "closed": c.is_closed,
        "provider": c.provider,
        "quality": c.quality.value,
    }


class IngestionService:
    def __init__(
        self,
        provider: MarketDataProvider,
        symbols: Sequence[str],
        sessionmaker: async_sessionmaker[AsyncSession],
        redis: Redis,
        *,
        backfill_hours: int = 72,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        backoff_base_s: float = 1.0,
        backoff_max_s: float = 60.0,
    ) -> None:
        self.provider = provider
        self.symbols = list(symbols)
        self._sessionmaker = sessionmaker
        self._redis = redis
        self._backfill_hours = backfill_hours
        self._clock = clock
        self._backoff_base = backoff_base_s
        self._backoff_max = backoff_max_s
        self._dupes = DuplicateFilter()
        self._last_status_write = 0.0
        self.status: dict[str, Any] = {
            "provider": provider.info.name,
            "quality": provider.info.stream_quality.value,
            "verified": provider.info.verified,
            "description": provider.info.description,
            "state": "starting",
            "symbols": self.symbols,
            "reconnects": 0,
            "duplicates_dropped": 0,
            "invalid_dropped": 0,
            "bars_stored": 0,
            "last_error": None,
            "last_event": {},
            "updated_at": time.time(),
        }

    # -- status ---------------------------------------------------------------------------
    async def _write_status(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_status_write < 1.0:
            return
        self._last_status_write = now
        self.status["updated_at"] = time.time()
        self.status["duplicates_dropped"] = self._dupes.dropped
        try:
            await self._redis.set(STATUS_KEY, json.dumps(self.status), ex=STATUS_TTL_S)
        except (RedisError, OSError):
            log_event(log, "status write failed", level=logging.WARNING)

    async def _set_state(self, state: str, error: str | None = None) -> None:
        self.status["state"] = state
        if error is not None:
            self.status["last_error"] = error
        await self._write_status(force=True)

    # -- pipeline -------------------------------------------------------------------------
    def _accept(self, candles: Sequence[Candle]) -> list[Candle]:
        now = self._clock()
        good: list[Candle] = []
        for c in candles:
            problems = validate_candle(c, now)
            if problems:
                self.status["invalid_dropped"] += 1
                log_event(
                    log,
                    "invalid bar dropped",
                    level=logging.WARNING,
                    symbol=c.symbol,
                    ts=c.ts.isoformat(),
                    problems=problems,
                )
                continue
            good.append(c)
        return good

    async def backfill(self) -> int:
        """Fetch history from the later of (last stored bar, now - backfill window)."""
        await self._set_state("backfilling")
        now = self._clock()
        floor = now - timedelta(hours=self._backfill_hours)
        async with self._sessionmaker() as s:
            last = await last_bar_times(s, self.provider.info.name)
        total = 0
        for symbol in self.symbols:
            start = max(floor, last[symbol] + timedelta(minutes=1)) if symbol in last else floor
            while start < now:
                end = min(start + BACKFILL_CHUNK, now)
                fetched = await self.provider.fetch_candles(symbol, start, end)
                good = sort_dedupe(self._accept(fetched))
                if good:
                    async with self._sessionmaker() as s:
                        total += await upsert_candles(s, good)
                        await s.commit()
                    self.status["last_event"][symbol] = good[-1].ts.isoformat()
                start = end
        self.status["bars_stored"] += total
        await self._write_status(force=True)
        log_event(log, "backfill complete", bars=total)
        return total

    async def handle(self, candle: Candle) -> bool:
        """Process one streamed bar update. Returns True if it was accepted."""
        if self._dupes.is_duplicate(candle):
            return False
        if not self._accept([candle]):
            return False
        if candle.is_closed:
            async with self._sessionmaker() as s:
                self.status["bars_stored"] += await upsert_candles(s, [candle])
                await s.commit()
        self.status["last_event"][candle.symbol] = candle.ts.isoformat()
        try:
            await self._redis.publish(CANDLE_CHANNEL, json.dumps(candle_to_wire(candle)))
        except (RedisError, OSError):
            log_event(log, "publish failed", level=logging.WARNING)
        await self._write_status()
        return True

    async def run(self, stop: asyncio.Event) -> None:
        """Backfill then stream until ``stop`` is set, reconnecting with capped backoff."""
        attempt = 0
        while not stop.is_set():
            try:
                await self.backfill()
                await self._set_state("streaming")
                async for candle in self.provider.stream_candles(self.symbols):
                    attempt = 0
                    await self.handle(candle)
                    if stop.is_set():
                        break
            except ProviderAuthError as exc:
                # Credentials will not fix themselves; retry slowly and say why.
                await self._set_state("auth_error", str(exc))
                log_event(log, "provider auth error", level=logging.ERROR, error=str(exc))
                await self._wait(stop, self._backoff_max)
                continue
            except (ProviderError, OSError, RedisError) as exc:
                attempt += 1
                self.status["reconnects"] += 1
                await self._set_state("reconnecting", f"{type(exc).__name__}: {exc}")
                delay = min(self._backoff_max, self._backoff_base * 2 ** (attempt - 1))
                log_event(
                    log,
                    "feed disconnected",
                    level=logging.WARNING,
                    attempt=attempt,
                    retry_in_s=delay,
                    error=type(exc).__name__,
                )
                await self._wait(stop, delay)
        await self._set_state("stopped")
        await self.provider.aclose()

    @staticmethod
    async def _wait(stop: asyncio.Event, seconds: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), seconds)
        except TimeoutError:
            pass
