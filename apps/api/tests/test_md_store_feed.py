from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.adapters.marketdata.base import (
    MarketDataProvider,
    ProviderAuthError,
    ProviderError,
    ProviderInfo,
)
from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.marketdata.feed import CANDLE_CHANNEL, STATUS_KEY, IngestionService
from app.marketdata.store import bar_times, last_bar_times, query_bars, upsert_candles
from app.marketdata.types import Candle, DataQuality, Timeframe
from app.models.candle import CandleRow

T0 = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)  # Wed 10:00 ET
NOW = T0 + timedelta(minutes=30, seconds=20)


def mk(minute: int, o: str, h: str, low: str, c: str, v: int = 10, **kw: object) -> Candle:
    base: dict[str, object] = dict(
        symbol="ES",
        timeframe=Timeframe.M1,
        ts=T0 + timedelta(minutes=minute),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=v,
        provider="test",
        quality=DataQuality.SIMULATED,
    )
    base.update(kw)
    return Candle(**base)  # type: ignore[arg-type]


async def _count() -> int:
    async with get_sessionmaker()() as s:
        return int(await s.scalar(select(func.count()).select_from(CandleRow)) or 0)


async def test_upsert_is_idempotent_and_applies_corrections() -> None:
    bars = [mk(0, "5000", "5002", "4999", "5001"), mk(1, "5001", "5003", "5000", "5002")]
    async with get_sessionmaker()() as s:
        assert await upsert_candles(s, bars) == 2
        assert await upsert_candles(s, bars) == 2  # replay: no duplicates
        await upsert_candles(s, [mk(1, "5001", "5004", "5000", "5003.5", v=99)])  # correction
        # Forming bars are never persisted.
        assert (
            await upsert_candles(s, [mk(2, "5002", "5003", "5001", "5002", is_closed=False)]) == 0
        )
        await s.commit()
    assert await _count() == 2
    async with get_sessionmaker()() as s:
        row = await s.get(CandleRow, ("test", "ES", T0 + timedelta(minutes=1)))
        assert row is not None and row.close == Decimal("5003.5") and row.volume == 99
        assert (await last_bar_times(s, "test"))["ES"] == T0 + timedelta(minutes=1)


async def test_aggregation_matches_hand_computed_bars() -> None:
    # 10 one-minute bars 10:00-10:09 ET -> two 5m bars.
    bars = [
        mk(0, "100", "101", "99", "100.5", 1),
        mk(1, "100.5", "103", "100", "102", 2),
        mk(2, "102", "102.5", "98", "99", 3),
        mk(3, "99", "100", "98.5", "99.5", 4),
        mk(4, "99.5", "101", "99", "100.75", 5),
        mk(5, "100.75", "104", "100.5", "103", 6),
        mk(6, "103", "103.5", "102", "102.5", 7),
        mk(7, "102.5", "105", "102", "104.5", 8),
        mk(8, "104.5", "104.75", "101", "101.5", 9),
        mk(9, "101.5", "102", "100", "100.25", 10),
    ]
    async with get_sessionmaker()() as s:
        await upsert_candles(s, bars)
        await s.commit()
        m5 = await query_bars(s, provider="test", symbol="ES", timeframe=Timeframe.M5)
        assert [(b.ts, b.open, b.high, b.low, b.close, b.volume, b.count) for b in m5] == [
            (T0, Decimal("100"), Decimal("103"), Decimal("98"), Decimal("100.75"), 15, 5),
            (
                T0 + timedelta(minutes=5),
                Decimal("100.75"),
                Decimal("105"),
                Decimal("100"),
                Decimal("100.25"),
                40,
                5,
            ),
        ]
        m3 = await query_bars(s, provider="test", symbol="ES", timeframe=Timeframe.M3)
        assert [b.ts.minute for b in m3] == [0, 3, 6, 9] and [b.count for b in m3] == [3, 3, 3, 1]
        h1 = await query_bars(s, provider="test", symbol="ES", timeframe=Timeframe.H1)
        assert len(h1) == 1 and h1[0].ts == T0 and h1[0].volume == 55
        assert (h1[0].open, h1[0].high, h1[0].low, h1[0].close) == (
            Decimal("100"),
            Decimal("105"),
            Decimal("98"),
            Decimal("100.25"),
        )
        # limit / before pagination, oldest first
        m1 = await query_bars(s, provider="test", symbol="ES", timeframe=Timeframe.M1, limit=3)
        assert [b.ts.minute for b in m1] == [7, 8, 9]
        older = await query_bars(
            s, provider="test", symbol="ES", timeframe=Timeframe.M1, limit=3, before=m1[0].ts
        )
        assert [b.ts.minute for b in older] == [4, 5, 6]
        # provider and symbol isolation
        assert await query_bars(s, provider="other", symbol="ES", timeframe=Timeframe.M1) == []
        assert await query_bars(s, provider="test", symbol="NQ", timeframe=Timeframe.M1) == []


async def test_daily_and_4h_bars_follow_the_trading_day() -> None:
    # Tue 17:59? (closed) -> use Tue 16:59 ET, Tue 18:00 ET (next trading day), Wed 10:00 ET.
    tue_1659 = datetime(2026, 11, 10, 21, 59, tzinfo=UTC)
    tue_1800 = datetime(2026, 11, 10, 23, 0, tzinfo=UTC)
    bars = [
        mk(0, "10", "11", "9", "10", 1, ts=tue_1659),
        mk(0, "20", "22", "19", "21", 2, ts=tue_1800),
        mk(0, "21", "25", "20", "24", 3, ts=T0),
    ]
    async with get_sessionmaker()() as s:
        await upsert_candles(s, bars)
        await s.commit()
        d1 = await query_bars(s, provider="test", symbol="ES", timeframe=Timeframe.D1)
        assert [(b.ts.date().isoformat(), b.open, b.close, b.volume) for b in d1] == [
            ("2026-11-10", Decimal("10"), Decimal("10"), 1),
            ("2026-11-11", Decimal("20"), Decimal("24"), 5),  # evening session joins Wednesday
        ]
        h4 = await query_bars(s, provider="test", symbol="ES", timeframe=Timeframe.H4)
        # 4h bins are anchored to the 18:00 ET open: 14:00 ET, 18:00 ET, 10:00 ET.
        assert [b.ts for b in h4] == [datetime(2026, 11, 10, 19, 0, tzinfo=UTC), tue_1800, T0]
        w1 = await query_bars(s, provider="test", symbol="ES", timeframe=Timeframe.W1)
        assert len(w1) == 1 and w1[0].ts.date().isoformat() == "2026-11-09" and w1[0].volume == 6


# --- ingestion ---------------------------------------------------------------------------


def service(
    provider: MarketDataProvider,
    symbols: Sequence[str] = ("ES",),
    hours: int = 2,
) -> IngestionService:
    return IngestionService(
        provider,
        symbols,
        get_sessionmaker(),
        get_redis(),
        backfill_hours=hours,
        clock=lambda: NOW,
        backoff_base_s=0.01,
        backoff_max_s=0.02,
    )


async def test_backfill_stores_history_and_resumes_without_refetching() -> None:
    sim = SimulatedProvider(clock=lambda: NOW, tick_seconds=0)
    svc = service(sim, ("ES", "CL"))
    stored = await svc.backfill()
    assert stored == 2 * 119  # 13:31..15:29 UTC for two symbols
    assert await _count() == 238

    calls: list[tuple[str, datetime, datetime]] = []
    original = sim.fetch_candles

    async def spy(symbol: str, start: datetime, end: datetime) -> list[Candle]:
        calls.append((symbol, start, end))
        return await original(symbol, start, end)

    sim.fetch_candles = spy  # type: ignore[method-assign]
    assert await service(sim, ("ES", "CL")).backfill() == 0  # nothing new
    # Resumes one minute after the last stored bar rather than re-downloading the window.
    assert all(start == T0 + timedelta(minutes=30) for _, start, _ in calls)
    assert await _count() == 238
    status = json.loads(await get_redis().get(STATUS_KEY))
    assert status["provider"] == "simulated" and status["quality"] == "simulated"


async def test_handle_dedupes_validates_persists_and_publishes() -> None:
    svc = service(SimulatedProvider(clock=lambda: NOW))
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(CANDLE_CHANNEL)
    await pubsub.get_message(timeout=1)  # subscribe ack

    closed = mk(29, "5000", "5001", "4999", "5000.5", provider="simulated")
    forming = mk(30, "5000.5", "5001", "5000", "5000.75", provider="simulated", is_closed=False)
    assert await svc.handle(closed) is True
    assert await svc.handle(closed) is False  # duplicate event
    assert await svc.handle(forming) is True  # published, not stored
    bad = mk(28, "5000", "4990", "4999", "5000.5", provider="simulated")  # high < open
    assert await svc.handle(bad) is False
    off_tick = mk(27, "5000", "5001", "4999", "5000.1", provider="simulated")
    assert await svc.handle(off_tick) is False

    assert await _count() == 1
    assert svc.status["invalid_dropped"] == 2 and svc._dupes.dropped == 1

    wire = []
    for _ in range(5):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
        if msg:
            wire.append(json.loads(msg["data"]))
    await pubsub.aclose()
    assert [(w["symbol"], w["closed"], w["quality"]) for w in wire] == [
        ("ES", True, "simulated"),
        ("ES", False, "simulated"),
    ]
    assert wire[0]["t"] == int((T0 + timedelta(minutes=29)).timestamp())


class FlakyProvider(MarketDataProvider):
    """Disconnects twice, then delivers one bar and waits."""

    info = ProviderInfo("simulated", DataQuality.SIMULATED, "flaky", True)

    def __init__(self) -> None:
        self.connects = 0

    async def fetch_candles(self, symbol: str, start: datetime, end: datetime) -> list[Candle]:
        return []

    async def stream_candles(self, symbols: Sequence[str]) -> AsyncIterator[Candle]:
        self.connects += 1
        if self.connects <= 2:
            raise ProviderError("socket closed")
        yield mk(29, "5000", "5001", "4999", "5000.5", provider="simulated")
        await asyncio.sleep(3600)


async def test_run_reconnects_after_disconnects_and_recovers() -> None:
    provider = FlakyProvider()
    svc = service(provider)
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    for _ in range(200):
        if await _count() == 1:
            break
        await asyncio.sleep(0.01)
    assert await _count() == 1
    assert provider.connects == 3
    assert svc.status["reconnects"] == 2 and svc.status["state"] == "streaming"
    assert "socket closed" in svc.status["last_error"]
    stop.set()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


class NoAuthProvider(FlakyProvider):
    async def fetch_candles(self, symbol: str, start: datetime, end: datetime) -> list[Candle]:
        raise ProviderAuthError("key rejected")


async def test_run_reports_auth_error_state() -> None:
    svc = service(NoAuthProvider())
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    for _ in range(100):
        if svc.status["state"] == "auth_error":
            break
        await asyncio.sleep(0.01)
    assert svc.status["state"] == "auth_error" and svc.status["last_error"] == "key rejected"
    stop.set()
    await asyncio.wait_for(task, 2)
    assert svc.status["state"] == "stopped"


async def test_gap_inputs_from_store() -> None:
    async with get_sessionmaker()() as s:
        await upsert_candles(s, [mk(0, "1", "1", "1", "1"), mk(3, "1", "1", "1", "1")])
        await s.commit()
        times = await bar_times(s, "test", "ES", T0, T0 + timedelta(minutes=5))
    assert [t.minute for t in times] == [0, 3]
