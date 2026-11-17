"""Background worker process.

Phase 1: publishes a heartbeat so the System Health page can detect a dead worker.
Later phases register market-data ingestion, scanning and alert jobs here.
Run with: ``python -m app.workers.main``
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time

from redis.exceptions import RedisError

from app.adapters.marketdata.base import ProviderError
from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.core.logging import configure_logging, log_event
from app.core.redis import close_redis, get_redis
from app.marketdata.factory import build_provider
from app.marketdata.feed import IngestionService
from app.services.health import WORKER_HEARTBEAT_KEY

log = logging.getLogger("apex.worker")
HEARTBEAT_INTERVAL_S = 5


async def heartbeat_once() -> None:
    payload = json.dumps({"ts": time.time(), "pid": os.getpid(), "version": get_settings().version})
    await get_redis().set(WORKER_HEARTBEAT_KEY, payload, ex=HEARTBEAT_INTERVAL_S * 12)


def start_ingestion(stop: asyncio.Event) -> asyncio.Task[None] | None:
    """Start market-data ingestion when a provider is configured."""
    settings = get_settings()
    try:
        provider = build_provider(settings)
    except ProviderError as exc:
        log_event(log, "market data provider unavailable", level=logging.ERROR, error=str(exc))
        return None
    if provider is None:
        log_event(log, "market data disabled (APEX_MARKET_DATA_PROVIDER=none)")
        return None
    service = IngestionService(
        provider,
        settings.market_data_symbols,
        get_sessionmaker(),
        get_redis(),
        backfill_hours=settings.market_data_backfill_hours,
    )
    log_event(log, "ingestion starting", provider=provider.info.name)
    return asyncio.create_task(service.run(stop))


async def run(stop: asyncio.Event) -> None:
    log_event(log, "worker started")
    ingestion = start_ingestion(stop)
    while not stop.is_set():
        try:
            await heartbeat_once()
        except (RedisError, OSError) as exc:
            log_event(log, "heartbeat failed", level=logging.WARNING, error=type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), HEARTBEAT_INTERVAL_S)
        except TimeoutError:
            pass
    if ingestion is not None:
        await asyncio.wait({ingestion}, timeout=10)
        if not ingestion.done():
            ingestion.cancel()
    await close_redis()
    await dispose_engine()
    log_event(log, "worker stopped")


def main() -> None:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    stop = asyncio.Event()

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await run(stop)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
