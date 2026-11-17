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
from app.research.scanner import ScannerService
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


async def calendar_sync(stop: asyncio.Event, interval_s: float = 6 * 3600) -> None:
    """Fetch configured official ICS calendar URLs periodically (none by default)."""
    import httpx

    from app.services.calendar import parse_ics, upsert_events

    urls = get_settings().calendar_ics_urls
    if not urls:
        return
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, headers={"User-Agent": "project-apex-calendar/1.0"}
    ) as client:
        while not stop.is_set():
            for url in urls:
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                    events, errors = parse_ics(r.text)
                    async with get_sessionmaker()() as session:
                        n = await upsert_events(session, events, "ics_url", url)
                        await session.commit()
                    log_event(log, "calendar synced", url=url, events=n, errors=len(errors))
                except (httpx.HTTPError, OSError) as exc:
                    log_event(log, "calendar sync failed", level=logging.WARNING, url=url, error=type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), interval_s)
            except TimeoutError:
                pass


async def news_poll(stop: asyncio.Event) -> None:
    """Poll the configured official news feeds (Federal Reserve, EIA) and measure reactions."""
    from datetime import UTC, datetime

    import httpx
    from sqlalchemy.exc import SQLAlchemyError

    from app.services.news import ingest

    s = get_settings()
    if not s.news_sources:
        return
    async with httpx.AsyncClient(follow_redirects=True) as client:
        while not stop.is_set():
            try:
                async with get_sessionmaker()() as session:
                    res = await ingest(session, get_redis(), client, datetime.now(UTC), s.news_sources)
                    await session.commit()
                log_event(log, "news polled", new=res["new"])
            except (SQLAlchemyError, OSError, RedisError):
                log.exception("news poll failed")
            try:
                await asyncio.wait_for(stop.wait(), s.news_poll_seconds)
            except TimeoutError:
                pass


async def lab_jobs(stop: asyncio.Event, interval_s: float = 2.0) -> None:
    """Run queued backtests and advance paper accounts from newly stored bars."""
    from datetime import UTC, datetime

    from sqlalchemy.exc import SQLAlchemyError

    from app.marketdata.status import current_quality
    from app.models.research import PaperAccount
    from app.services.paper import process
    from app.services.research import claim_next, execute_run, recover_stuck

    sm = get_sessionmaker()
    try:
        async with sm() as session:
            await recover_stuck(session)
    except SQLAlchemyError:
        log.exception("could not recover stuck backtests")
    while not stop.is_set():
        try:
            async with sm() as session:
                run = await claim_next(session)
            if run is not None:
                quality, _ = await current_quality()
                # Stored bars keep their class: simulated stays simulated even when the feed is down.
                if get_settings().market_data_provider == "simulated":
                    quality = "simulated"
                elif quality in ("unavailable", "live", "delayed"):
                    quality = "historical"
                await execute_run(sm, run.id, quality)
                continue
            async with sm() as session:
                from sqlalchemy import select

                from app.models.research import JournalTrade
                from app.services.trade_assistant import assist
                from app.services.trade_review import review_trade

                for acct in (await session.scalars(select(PaperAccount))).all():
                    await process(session, acct, datetime.now(UTC))
                    await assist(session, get_redis(), acct, get_settings(), datetime.now(UTC))
                await session.commit()
                # Journal every newly closed or imported trade automatically.
                pending = (
                    await session.scalars(
                        select(JournalTrade).where(JournalTrade.review.is_(None)).order_by(JournalTrade.id).limit(10)
                    )
                ).all()
                for t in pending:
                    await review_trade(session, t, get_settings())
                await session.commit()
        except (SQLAlchemyError, OSError):
            log.exception("lab job failed")
        try:
            await asyncio.wait_for(stop.wait(), interval_s)
        except TimeoutError:
            pass


async def run(stop: asyncio.Event) -> None:
    log_event(log, "worker started")
    ingestion = start_ingestion(stop)
    calendar = asyncio.create_task(calendar_sync(stop))
    lab = asyncio.create_task(lab_jobs(stop))
    news = asyncio.create_task(news_poll(stop))
    scanner = asyncio.create_task(ScannerService(get_settings(), get_sessionmaker(), get_redis()).run(stop))
    while not stop.is_set():
        try:
            await heartbeat_once()
        except (RedisError, OSError) as exc:
            log_event(log, "heartbeat failed", level=logging.WARNING, error=type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), HEARTBEAT_INTERVAL_S)
        except TimeoutError:
            pass
    tasks = {t for t in (ingestion, scanner, calendar, lab, news) if t is not None}
    await asyncio.wait(tasks, timeout=10)
    for t in tasks:
        if not t.done():
            t.cancel()
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
