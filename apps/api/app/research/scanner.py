"""Market scanner: runs every strategy on every symbol and maintains signal state.

Runs in the worker. Each cycle it loads closed 1-minute bars, builds the radar, evaluates the
strategies, stores new signals (deduplicated by provider/strategy/symbol/bar), advances the
lifecycle of active signals and raises alerts. The latest scan is cached in Redis for the API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import log_event
from app.marketdata.calendar import ET, floor_minute, is_market_open
from app.marketdata.feed import STATUS_KEY
from app.models.signal import SignalRow
from app.research.bars import Bar
from app.research.radar import symbol_radar
from app.research.scoring import build_opportunity
from app.services.alerts import emit_alert
from app.strategies.base import MarketContext
from app.strategies.library import STRATEGIES

log = logging.getLogger("apex.scanner")
SCAN_KEY = "apex:scan:latest"
SCAN_TTL_S = 180
SIGNAL_MAX_AGE = timedelta(minutes=60)


async def load_bars(session: AsyncSession, provider: str, symbol: str, since: datetime, until: datetime) -> list[Bar]:
    """Closed 1-minute bars in [since, until), oldest first."""
    res = await session.execute(
        text(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE provider = :p AND symbol = :s AND ts >= :a AND ts < :b ORDER BY ts"
        ),
        {"p": provider, "s": symbol, "a": since, "b": until},
    )
    return [Bar(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(r[5])) for r in res]


def scan_symbol(
    symbol: str,
    bars: list[Bar],
    now: datetime,
    settings: Settings,
    provider: str,
    quality: str,
    validations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure scan of one symbol: radar, every strategy evaluation, and triggered opportunities."""
    ctx = MarketContext(symbol, now, bars)
    evaluations, opportunities = [], []
    for strat in STRATEGIES:
        ev = strat.evaluate(ctx)
        evaluations.append(
            {
                "strategy_id": ev.strategy_id,
                "triggered": ev.signal is not None,
                "side": ev.side,
                "note": ev.note,
                "passed": sum(c.passed for c in ev.conditions),
                "total": len(ev.conditions),
                "conditions": [asdict(c) for c in ev.conditions],
            }
        )
        if ev.signal is not None:
            opportunities.append(build_opportunity(ctx, ev, settings, provider, quality, validations))
    return {"radar": symbol_radar(ctx), "evaluations": evaluations, "opportunities": opportunities}


def signal_outcome(row: SignalRow, bars: list[Bar], now: datetime) -> str | None:
    """New status for an active signal given bars after its trigger bar, else None.

    If a bar touches both stop and target the stop is assumed first (conservative).
    """
    s = 1 if row.direction == "long" else -1
    after = row.bar_ts + timedelta(minutes=5)
    target = row.targets[0]
    for b in bars:
        if b.ts < after:
            continue
        if (b.low <= row.stop) if s == 1 else (b.high >= row.stop):
            return "invalidated"
        if (b.high >= target) if s == 1 else (b.low <= target):
            return "target_reached"
    return "expired" if now - after > SIGNAL_MAX_AGE else None


class ScannerService:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        redis: Redis,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self._sessionmaker = sessionmaker
        self._redis = redis
        self._clock = clock
        self._regimes: dict[str, str] = {}

    async def _feed(self) -> dict[str, Any] | None:
        try:
            raw = await self._redis.get(STATUS_KEY)
            doc = json.loads(raw) if raw else None
        except (RedisError, OSError, ValueError):
            return None
        if not isinstance(doc, dict) or time.time() - float(doc.get("updated_at", 0)) > 60:
            return None
        return doc

    async def scan_once(self) -> dict[str, Any]:
        s = self.settings
        now = self._clock()
        provider = s.market_data_provider
        feed = await self._feed()
        quality = str(feed["quality"]) if feed else "unavailable"
        doc: dict[str, Any] = {
            "as_of": now.isoformat(),
            "provider": None if provider == "none" else provider,
            "quality": quality,
            "market_open": is_market_open(now),
            "symbols": [],
            "opportunities": [],
            "note": None,
        }
        if provider == "none":
            doc["note"] = "No market data provider configured."
        elif feed is None:
            doc["note"] = "Ingestion worker is not reporting; scan skipped (no stale signals)."
        else:
            until = floor_minute(now)
            since = until - timedelta(hours=s.scanner_lookback_hours)
            async with self._sessionmaker() as session:
                from app.services.research import load_validations

                validations = await load_validations(session)
                for symbol in s.market_data_symbols:
                    bars = await load_bars(session, provider, symbol, since, until)
                    result = scan_symbol(symbol, bars, now, s, provider, quality, validations)
                    doc["symbols"].append({**result["radar"], "evaluations": result["evaluations"]})
                    await self._lifecycle(session, provider, symbol, bars, now, quality)
                    for opp in result["opportunities"]:
                        if await self._store(session, opp):
                            doc["opportunities"].append(opp)
                    await self._regime_alert(session, symbol, result["radar"], quality)
                await session.commit()
        async with self._sessionmaker() as session:
            await self._feed_alert(session, provider, feed)
            await self._account_alerts(session, now)
            await self._event_alerts(session, now)
            await session.commit()
        try:
            await self._redis.set(SCAN_KEY, json.dumps(doc), ex=SCAN_TTL_S)
        except (RedisError, OSError):
            log_event(log, "scan cache write failed", level=logging.WARNING)
        return doc

    def _tag(self, quality: str) -> str:
        return "[SIMULATED DATA] " if quality == "simulated" else ""

    async def _store(self, session: AsyncSession, opp: dict[str, Any]) -> bool:
        """Insert a signal once; True if it is new."""
        stmt = (
            insert(SignalRow)
            .values(
                provider=opp["provider"],
                quality=opp["quality"],
                strategy_id=opp["strategy_id"],
                symbol=opp["symbol"],
                direction=opp["direction"],
                bar_ts=datetime.fromisoformat(opp["bar_ts"]),
                entry=opp["entry"],
                entry_low=opp["entry_low"],
                entry_high=opp["entry_high"],
                stop=opp["stop"],
                targets=opp["targets"],
                score=opp["score"],
                detail=opp,
                status="active",
            )
            .on_conflict_do_nothing(index_elements=["provider", "strategy_id", "symbol", "bar_ts"])
        )
        res = await session.execute(stmt.returning(SignalRow.id))
        if res.scalar_one_or_none() is None:
            return False
        await emit_alert(
            session,
            self._redis,
            kind="opportunity_detected",
            severity="info",
            symbol=opp["symbol"],
            title=f"{self._tag(opp['quality'])}{opp['symbol']} {opp['direction']} candidate: {opp['strategy_id']}",
            body=f"Unvalidated research candidate. Entry {opp['entry']}, stop {opp['stop']}, "
            f"first target {opp['targets'][0]} ({opp['reward_risk']}R). "
            f"Setup score {opp['score']} (not a probability).",
            dedupe_key=f"opp:{opp['symbol']}:{opp['strategy_id']}:{opp['direction']}",
            cooldown_s=self.settings.alert_cooldown_seconds,
            data={"strategy_id": opp["strategy_id"], "bar_ts": opp["bar_ts"]},
        )
        return True

    async def _lifecycle(
        self,
        session: AsyncSession,
        provider: str,
        symbol: str,
        bars: list[Bar],
        now: datetime,
        quality: str,
    ) -> None:
        rows = (
            await session.scalars(
                select(SignalRow).where(
                    SignalRow.provider == provider,
                    SignalRow.symbol == symbol,
                    SignalRow.status == "active",
                )
            )
        ).all()
        for row in rows:
            outcome = signal_outcome(row, bars, now)
            if outcome is None:
                continue
            row.status, row.status_at = outcome, now
            if outcome == "invalidated":
                await emit_alert(
                    session,
                    self._redis,
                    kind="setup_invalidated",
                    severity="warning",
                    symbol=symbol,
                    title=f"{self._tag(quality)}{symbol} {row.strategy_id} setup invalidated",
                    body=f"Price traded through the stop level {row.stop}.",
                    dedupe_key=f"inv:{row.id}",
                    cooldown_s=3600,
                    data={"signal_id": row.id},
                )

    async def _regime_alert(self, session: AsyncSession, symbol: str, radar: dict[str, Any], quality: str) -> None:
        label = radar["regime"]
        prev = self._regimes.get(symbol)
        self._regimes[symbol] = label
        if prev is None or prev == label or "unknown" in (prev, label):
            return
        await emit_alert(
            session,
            self._redis,
            kind="regime_changed",
            severity="info",
            symbol=symbol,
            title=f"{self._tag(quality)}{symbol} regime: {prev} -> {label}",
            body=radar["regime_definition"],
            dedupe_key=f"regime:{symbol}",
            cooldown_s=self.settings.alert_cooldown_seconds,
            data={"from": prev, "to": label},
        )

    async def _feed_alert(self, session: AsyncSession, provider: str, feed: dict[str, Any] | None) -> None:
        if provider == "none":
            return
        state = feed.get("state") if feed else "not_reporting"
        if state in ("streaming", "starting", "backfilling"):
            return
        await emit_alert(
            session,
            self._redis,
            kind="data_feed",
            severity="critical",
            title=f"Market data feed problem: {state}",
            body=str((feed or {}).get("last_error") or "The ingestion worker is not reporting."),
            dedupe_key=f"feed:{state}",
            cooldown_s=self.settings.alert_cooldown_seconds,
        )

    async def _account_alerts(self, session: AsyncSession, now: datetime) -> None:
        """Warn when account data goes stale during a session or the risk budget runs low."""
        from app.services.account import current_state

        rules, state = await current_state(session, now)
        if not state.known or not is_market_open(now):
            return
        day = state.trading_date.isoformat() if state.trading_date else ""
        if state.stale:
            await emit_alert(
                session,
                self._redis,
                kind="account_stale",
                severity="warning",
                title="Account data is stale",
                body="Enter a fresh balance on Apex Account Health. Remaining risk is treated as unknown.",
                dedupe_key=f"acct-stale:{day}",
                cooldown_s=3600,
            )
        budget, limit = state.risk_budget, rules.daily_loss_limit
        if budget is not None and limit > 0:
            if budget <= 0:
                level, sev = "exhausted", "critical"
            elif budget <= limit * Decimal("0.25"):
                level, sev = "below 25%", "warning"
            else:
                return
            await emit_alert(
                session,
                self._redis,
                kind="risk_threshold",
                severity=sev,
                title=f"Risk budget {level}",
                body=f"Remaining risk budget ${budget} of the ${limit} daily allowance "
                "(on user-entered figures; advisory).",
                dedupe_key=f"risk:{level}:{day}",
                cooldown_s=4 * 3600,
            )

    async def _event_alerts(self, session: AsyncSession, now: datetime) -> None:
        """One alert per high/medium event once it enters the pre-event window."""
        from app.services.calendar import events_between

        pre = self.settings.calendar_pre_event_minutes
        for ev in await events_between(session, now, now + timedelta(minutes=pre)):
            if ev.importance not in ("high", "medium") or not ev.affected:
                continue
            minutes = max(0, round((ev.ts - now).total_seconds() / 60))
            await emit_alert(
                session,
                self._redis,
                kind="economic_event",
                severity="warning" if ev.importance == "high" else "info",
                title=f"{ev.title} in {minutes} min",
                body=f"Scheduled {ev.ts.astimezone(ET):%H:%M} ET; affects {', '.join(ev.affected)}. "
                "Apex news-trading restrictions are unverified; check the rule profile.",
                dedupe_key=f"event:{ev.id}",
                cooldown_s=24 * 3600,
                data={"event_id": ev.id},
            )

    async def run(self, stop: asyncio.Event) -> None:
        log_event(log, "scanner started", interval_s=self.settings.scanner_interval_seconds)
        while not stop.is_set():
            started = time.monotonic()
            try:
                doc = await self.scan_once()
                log_event(
                    log,
                    "scan complete",
                    symbols=len(doc["symbols"]),
                    new_signals=len(doc["opportunities"]),
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            except (SQLAlchemyError, RedisError, OSError):
                log.exception("scan failed")
            try:
                await asyncio.wait_for(stop.wait(), self.settings.scanner_interval_seconds)
            except TimeoutError:
                pass
