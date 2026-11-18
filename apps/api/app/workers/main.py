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
from datetime import UTC, datetime
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


async def forexfactory_sync(stop: asyncio.Event, tick_s: float = 60) -> None:
    """Check every minute whether the ForexFactory export is due (hourly; backoff on failure).

    The schedule and backoff are stored in PostgreSQL and guarded by a Redis lock, so restarts or
    several workers never download more often than the source allows.
    """
    import httpx

    from app.services.calendar import guarded_sync_forexfactory

    s = get_settings()
    if not s.calendar_forexfactory:
        return
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, headers={"User-Agent": "project-apex-calendar/1.0"}
    ) as client:
        while not stop.is_set():
            try:
                async with get_sessionmaker()() as session:
                    out = await guarded_sync_forexfactory(session, get_redis(), client, s, datetime.now(UTC))
                if "skipped" not in out:
                    log_event(
                        log,
                        "forexfactory calendar sync",
                        synced=out.get("synced"),
                        events=out.get("events"),
                        error=out.get("error"),
                    )
            except Exception as exc:
                log_event(log, "forexfactory sync failed", level=logging.WARNING, error=type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), tick_s)
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


failed_task: list[str] = []


async def autopilot_loop(stop: asyncio.Event, interval_s: float = 15.0) -> None:
    """Autopilot cycles (no-op in APEX_MANUAL). Errors are logged and retried; never fatal."""
    from app.services import autopilot

    s = get_settings()
    log_event(log, "autopilot mode", mode=s.automation_mode)
    if s.automation_mode == "APEX_MANUAL":
        await stop.wait()
        return
    sm = get_sessionmaker()
    while not stop.is_set():
        try:
            async with sm() as session:
                autopilot.log_cycle(await autopilot.cycle(session))
                await session.commit()
        except Exception:
            log.exception("autopilot cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), interval_s)
        except TimeoutError:
            pass


NEWS_INTEL_KEY = "apex:news:intel"


async def news_intel_loop(stop: asyncio.Event, interval_s: float = 60) -> None:
    """Every 60 s: re-evaluate the cached calendar against the clock and alert once per event/status.

    Downloads nothing (see forexfactory_sync). Alerts are de-duplicated per event and status.
    """
    from app.services import news_intel
    from app.services.alerts import emit_alert

    while not stop.is_set():
        try:
            async with get_sessionmaker()() as session:
                doc = await news_intel.evaluate(session, datetime.now(UTC), "ES")
                await get_redis().set(NEWS_INTEL_KEY, json.dumps(doc), ex=300)
                ev = doc.get("next_high_impact") if doc["status"] == news_intel.UPCOMING else None
                if doc["status"] in (news_intel.ACTIVE, news_intel.UPCOMING) and (ev or doc["today"]):
                    target = ev or min(doc["today"], key=lambda e: abs(e["minutes_until"]))
                    await emit_alert(
                        session,
                        get_redis(),
                        kind="economic_event",
                        severity="warning",
                        title=f"{doc['status']}: {target['title']}",
                        body=f"{target['time_et']} (high impact). {news_intel.DIRECTION_NOTE}",
                        dedupe_key=f"news:{target['id']}:{doc['status']}",
                        cooldown_s=86400,
                        data={"event_id": target["id"], "status": doc["status"]},
                    )
                    await session.commit()
        except Exception:
            log.exception("news intelligence cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), interval_s)
        except TimeoutError:
            pass


async def analyzer_loop(stop: asyncio.Event, interval_s: float = 20) -> None:
    """Warm the analyzer cache when new bars close, so the dashboard answers instantly."""
    from app.services import analyzer

    s = get_settings()
    if s.market_data_provider == "none":
        return
    while not stop.is_set():
        try:
            async with get_sessionmaker()() as session:
                for sym in s.analyzer_symbols:
                    await analyzer.snapshot(session, sym)
        except Exception:
            log.exception("analyzer cycle failed")
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
    # Analysis-only build: the simulated autopilot is not started unless explicitly configured.
    auto = asyncio.create_task(autopilot_loop(stop)) if get_settings().automation_mode != "APEX_MANUAL" else None
    intel = asyncio.create_task(news_intel_loop(stop))
    analysis = asyncio.create_task(analyzer_loop(stop))
    scanner = asyncio.create_task(ScannerService(get_settings(), get_sessionmaker(), get_redis()).run(stop))
    named = {
        "ingestion": ingestion,
        "scanner": scanner,
        "calendar": calendar,
        "forexfactory": ff,
        "lab": lab,
        "news": news,
        "autopilot": auto,
        "news_intel": intel,
        "analyzer": analysis,
    }
    while not stop.is_set():
        # Supervision: a long-running task that ends while the worker is running means something broke.
        # Exit non-zero so Docker (restart: unless-stopped) restarts the worker, instead of reporting a
        # healthy heartbeat with a dead live feed. Tasks that end immediately because they are not
        # configured (no calendar URLs, provider "none") return None or finish cleanly and are ignored.
        for name, t in named.items():
            if t is not None and t.done() and not t.cancelled() and t.exception() is not None:
                log_event(
                    log,
                    "worker task crashed; exiting so the container restarts",
                    level=logging.ERROR,
                    task=name,
                    error=type(t.exception()).__name__,
                )
                stop.set()
                failed_task.append(name)
        if stop.is_set():
            break
        try:
            await heartbeat_once()
        except (RedisError, OSError) as exc:
            log_event(log, "heartbeat failed", level=logging.WARNING, error=type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), HEARTBEAT_INTERVAL_S)
        except TimeoutError:
            pass
    tasks = {t for t in (ingestion, scanner, calendar, ff, lab, news, auto, intel, analysis) if t is not None}
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
    if failed_task:
        raise SystemExit(1)  # non-zero: Docker restarts the worker


if __name__ == "__main__":
    main()
