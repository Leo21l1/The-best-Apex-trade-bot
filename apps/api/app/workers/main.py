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
from datetime import datetime
from typing import Any

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


async def forexfactory_sync(stop: asyncio.Event, interval_s: float = 3600) -> None:
    """Fetch the ForexFactory weekly calendar export hourly (its publisher rate-limits the feed)."""
    import httpx

    from app.services.calendar import sync_forexfactory

    s = get_settings()
    if not s.calendar_forexfactory:
        return
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, headers={"User-Agent": "project-apex-calendar/1.0"}
    ) as client:
        while not stop.is_set():
            try:
                async with get_sessionmaker()() as session:
                    out = await sync_forexfactory(
                        session, client, s.calendar_forexfactory_url, s.calendar_forexfactory_currencies
                    )
                log_event(log, "forexfactory calendar synced", events=out["events"], errors=len(out["errors"]))
            except Exception as exc:
                log_event(log, "forexfactory sync failed", level=logging.WARNING, error=type(exc).__name__)
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


REVIEW_SKIP_KEY = "apex:review:skip:"
_BACKFILL_PROVIDER: Any = None


async def lab_cycle(sm: Any, now: datetime | None = None) -> str:
    """One pass of the lab job loop. Every unit of work is isolated: an exception in one backtest,
    paper account or trade review is logged and recorded, and never stops the others.

    Root cause of the stuck-backtest failure: the loop used to catch only SQLAlchemyError/OSError, so
    any other exception (for example the trade-review KeyError on plan-linked imported trades) ended
    the whole task silently; queued backtests then stayed 'queued' forever and paper fills and
    journal reviews stopped as well.
    """
    from datetime import UTC
    from datetime import datetime as dt

    from sqlalchemy import select

    from app.marketdata.status import current_quality
    from app.models.research import JournalTrade, PaperAccount
    from app.services.paper import process
    from app.services.research import claim_next, execute_run
    from app.services.trade_assistant import assist
    from app.services.trade_review import review_trade

    now = now or dt.now(UTC)
    try:
        from app.services.health import LAB_HEARTBEAT_KEY

        await get_redis().set(LAB_HEARTBEAT_KEY, str(time.time()), ex=600)
    except (RedisError, OSError):
        pass
    async with sm() as session:
        run = await claim_next(session)
    if run is not None:
        quality, _ = await current_quality()
        # Stored bars keep their class: simulated stays simulated even when the feed is down.
        if get_settings().market_data_provider == "simulated":
            quality = "simulated"
        elif quality in ("unavailable", "live", "delayed"):
            quality = "historical"
        await execute_run(sm, run.id, quality)  # never raises: failures are stored on the run
        return "backtest"
    # Approved historical backfill: a few sessions per cycle, so the loop stays responsive.
    try:
        from app.services.history import get_job, run_job_step

        job = await get_job()
        if job and job["status"] in ("queued", "running"):
            global _BACKFILL_PROVIDER
            if _BACKFILL_PROVIDER is None:
                from app.marketdata.factory import build_provider

                _BACKFILL_PROVIDER = build_provider(get_settings())
            async with sm() as session:
                if await run_job_step(session, _BACKFILL_PROVIDER):
                    return "backfill"
    except Exception:
        log.exception("backfill step failed")
    async with sm() as session:
        for acct in (await session.scalars(select(PaperAccount))).all():
            try:
                await process(session, acct, now)
                await assist(session, get_redis(), acct, get_settings(), now)
                await session.commit()
            except Exception:
                await session.rollback()
                log.exception("paper account job failed", extra={"account": acct.id})
    async with sm() as session:
        pending = (
            await session.scalars(
                select(JournalTrade.id).where(JournalTrade.review.is_(None)).order_by(JournalTrade.id).limit(25)
            )
        ).all()
        done = 0
        for tid in pending:
            if done >= 10:
                break
            try:
                if await get_redis().get(f"{REVIEW_SKIP_KEY}{tid}"):
                    continue  # failed recently: retry after the skip expires, not every 2 seconds
            except (RedisError, OSError):
                pass
            try:
                t = await session.get(JournalTrade, tid)
                if t is not None:
                    await review_trade(session, t, get_settings())
                    await session.commit()
                    done += 1
            except Exception:
                await session.rollback()
                log.exception("trade review failed", extra={"trade_id": tid})
                try:
                    await get_redis().set(f"{REVIEW_SKIP_KEY}{tid}", "1", ex=3600)
                except (RedisError, OSError):
                    pass
    return "idle"


async def lab_jobs(stop: asyncio.Event, interval_s: float = 2.0) -> None:
    """Run queued backtests, advance paper accounts and journal new trades, forever."""
    from sqlalchemy.exc import SQLAlchemyError

    from app.services.research import recover_stuck

    sm = get_sessionmaker()
    try:
        async with sm() as session:
            await recover_stuck(session)
    except SQLAlchemyError:
        log.exception("could not recover stuck backtests")
    while not stop.is_set():
        try:
            if await lab_cycle(sm) in ("backtest", "backfill"):
                continue
        except Exception:  # the loop itself must survive anything (database restarts included)
            log.exception("lab job cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), interval_s)
        except TimeoutError:
            pass


async def run(stop: asyncio.Event) -> None:
    log_event(log, "worker started")
    ingestion = start_ingestion(stop)
    calendar = asyncio.create_task(calendar_sync(stop))
    ff = asyncio.create_task(forexfactory_sync(stop))
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
    tasks = {t for t in (ingestion, scanner, calendar, ff, lab, news) if t is not None}
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
