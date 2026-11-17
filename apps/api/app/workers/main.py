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

from app.core.config import get_settings
from app.core.logging import configure_logging, log_event
from app.core.redis import close_redis, get_redis
from app.services.health import WORKER_HEARTBEAT_KEY

log = logging.getLogger("apex.worker")
HEARTBEAT_INTERVAL_S = 5


async def heartbeat_once() -> None:
    payload = json.dumps({"ts": time.time(), "pid": os.getpid(), "version": get_settings().version})
    await get_redis().set(WORKER_HEARTBEAT_KEY, payload, ex=HEARTBEAT_INTERVAL_S * 12)


async def run(stop: asyncio.Event) -> None:
    log_event(log, "worker started")
    while not stop.is_set():
        try:
            await heartbeat_once()
        except (RedisError, OSError) as exc:
            log_event(log, "heartbeat failed", level=logging.WARNING, error=type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), HEARTBEAT_INTERVAL_S)
        except TimeoutError:
            pass
    await close_redis()
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
