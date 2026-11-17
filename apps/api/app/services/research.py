"""Backtest experiments (queued, run by the worker) and strategy validation from their results."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import fields
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import log_event
from app.models.research import BacktestRun
from app.research.backtest import BacktestParams, run_backtest
from app.research.scanner import load_bars
from app.research.validation import ValidationStatus

log = logging.getLogger("apex.research")


def params_from(d: dict[str, Any]) -> BacktestParams:
    names = {f.name for f in fields(BacktestParams)}
    clean = {k: v for k, v in d.items() if k in names}
    if clean.get("target_r") is not None:
        clean["target_r"] = tuple(clean["target_r"])
    return BacktestParams(**clean)


async def claim_next(session: AsyncSession) -> BacktestRun | None:
    """Atomically move the oldest queued run to 'running' (safe with several workers)."""
    row = await session.scalar(
        select(BacktestRun)
        .where(BacktestRun.status == "queued")
        .order_by(BacktestRun.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if row is None:
        return None
    row.status, row.started_at = "running", datetime.now(UTC)
    await session.commit()
    return row


PROGRESS_KEY = "apex:backtest:progress:"
_POOL: Any = None


def _pool() -> Any:
    """One worker process for backtests: CPU-heavy simulation must not hold the GIL of the process
    that runs the live market-data stream."""
    global _POOL
    if _POOL is None:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor

        _POOL = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    return _POOL


async def _progress(run_id: int, phase: str, **extra: Any) -> None:
    import json

    from redis.exceptions import RedisError

    from app.core.redis import get_redis

    try:
        await get_redis().set(
            f"{PROGRESS_KEY}{run_id}",
            json.dumps({"phase": phase, "at": datetime.now(UTC).isoformat(), **extra}),
            ex=86400,
        )
    except (RedisError, OSError):
        pass


async def _simulate(params: BacktestParams, bars: list[Any], quality: str, prior: int, use_process: bool) -> Any:
    loop = asyncio.get_running_loop()
    if use_process:
        try:
            return await loop.run_in_executor(_pool(), run_backtest, params, bars, quality, prior)
        except (OSError, RuntimeError, NotImplementedError) as exc:  # e.g. no process support: use a thread
            log_event(log, "backtest process pool unavailable", level=logging.WARNING, error=type(exc).__name__)
    return await asyncio.to_thread(run_backtest, params, bars, quality, prior)


async def execute_run(
    sessionmaker: async_sessionmaker[AsyncSession], run_id: int, quality: str, use_process: bool = True
) -> None:
    """Execute one run. Never raises: every failure is stored on the run with its reason."""
    async with sessionmaker() as session:
        run = await session.get(BacktestRun, run_id)
        if run is None:
            return
        try:
            params = params_from(run.params)
            await _progress(run_id, "loading historical bars")
            bars = await load_bars(session, run.provider, run.symbol, run.start, run.end)
            if len(bars) < 1000:
                raise ValueError(
                    f"only {len(bars)} stored 1-minute {run.provider} bars for {run.symbol} between "
                    f"{run.start:%Y-%m-%d} and {run.end:%Y-%m-%d}; need at least 1000. Backfill history "
                    "(System Health > Data readiness) or choose a range with stored data."
                )
            prior = int(
                await session.scalar(
                    select(
                        func.coalesce(
                            func.sum(func.coalesce(func.jsonb_array_length(BacktestRun.result["sensitivity"]), 0) + 1),
                            0,
                        )
                    ).where(BacktestRun.strategy_id == run.strategy_id, BacktestRun.status == "done")
                )
                or 0
            )
            await _progress(
                run_id,
                "simulating (base run, in/out-of-sample, walk-forward, sensitivity)",
                bars=len(bars),
                first=bars[0].ts.isoformat(),
                last=bars[-1].ts.isoformat(),
            )
            result = await _simulate(params, bars, quality, prior, use_process)
            run.result, run.validated, run.status = result, result["validation"]["passed"], "done"
            run.error = None
        except (ValueError, KeyError) as exc:
            run.status, run.error = "failed", str(exc)[:500]
        except Exception as exc:  # unexpected: record it instead of leaving the run 'running' forever
            log.exception("backtest crashed", extra={"run_id": run_id})
            run.status, run.error = "failed", f"Internal error: {type(exc).__name__}: {exc}"[:500]
        run.finished_at = datetime.now(UTC)
        final = run.status  # read before a possible rollback expires the instance
        try:
            await session.commit()
        except Exception as exc:  # e.g. a result PostgreSQL refuses: never leave the run 'running'
            await session.rollback()
            log.exception("could not store backtest result", extra={"run_id": run_id})
            await session.execute(
                update(BacktestRun)
                .where(BacktestRun.id == run_id)
                .values(
                    status="failed",
                    error=f"Could not store the result: {type(exc).__name__}"[:500],
                    finished_at=datetime.now(UTC),
                    result=None,
                )
            )
            await session.commit()
            final = "failed"
        await _progress(run_id, final)
        log_event(log, "backtest finished", run_id=run_id, status=final)


async def recover_stuck(session: AsyncSession) -> None:
    """Runs left 'running' by a crashed worker go back to the queue."""
    await session.execute(update(BacktestRun).where(BacktestRun.status == "running").values(status="queued"))
    await session.commit()


async def load_validations(session: AsyncSession) -> dict[str, ValidationStatus]:
    """Latest completed experiment per strategy on real (non-simulated) data decides validation."""
    rows = (
        await session.scalars(select(BacktestRun).where(BacktestRun.status == "done").order_by(BacktestRun.id.desc()))
    ).all()
    out: dict[str, ValidationStatus] = {}
    for r in rows:
        if r.strategy_id in out or not r.result:
            continue
        if r.result["dataset"]["quality"] == "simulated":
            continue
        m = r.result["out_of_sample"]
        out[r.strategy_id] = ValidationStatus(
            validated=bool(r.validated),
            reason=(
                f"Passed research standards in experiment #{r.id}."
                if r.validated
                else f"Failed research standards in experiment #{r.id}: "
                + "; ".join(c["name"] for c in r.result["validation"]["checks"] if not c["passed"])
            ),
            sample_size=r.result["metrics"]["trades"],
            expectancy_r=m["expectancy_r"],
            profit_factor=m["profit_factor"],
        )
    return out
