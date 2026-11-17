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


async def execute_run(sessionmaker: async_sessionmaker[AsyncSession], run_id: int, quality: str) -> None:
    async with sessionmaker() as session:
        run = await session.get(BacktestRun, run_id)
        if run is None:
            return
        try:
            params = params_from(run.params)
            bars = await load_bars(session, run.provider, run.symbol, run.start, run.end)
            if len(bars) < 1000:
                raise ValueError(f"only {len(bars)} stored 1-minute bars in the range; need at least 1000")
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
            result = await asyncio.to_thread(run_backtest, params, bars, quality, prior)
            run.result, run.validated, run.status = result, result["validation"]["passed"], "done"
        except (ValueError, KeyError) as exc:
            run.status, run.error = "failed", str(exc)[:500]
        run.finished_at = datetime.now(UTC)
        await session.commit()
        log_event(log, "backtest finished", run_id=run_id, status=run.status)


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
