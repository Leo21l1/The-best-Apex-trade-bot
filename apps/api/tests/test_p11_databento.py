"""Databento: historical cutoff clamping, 422 handling, live streaming with replay, no reconnect loops.

No network: the historical API is an httpx MockTransport and the Live client is a fake with the
same surface as ``databento.Live`` (subscribe / start / stop / async iteration of records).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.adapters.marketdata.base import ProviderAuthError, ProviderError, ProviderRequestError
from app.adapters.marketdata.databento import DatabentoProvider, redact
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.marketdata.feed import IngestionService
from app.marketdata.types import DataQuality

NOW = datetime(2026, 11, 17, 15, 0, 30, tzinfo=UTC)  # Tue 10:00 ET
KEY = "db-ABCDEFGHIJKLMNOPQRSTUVWX"
BAR = '{"hd":{"ts_event":"%s"},"open":"5000.25","high":"5001.00","low":"4999.75","close":"5000.50","volume":"120"}\n'


def bars(*ts: datetime) -> str:
    return "".join(BAR % t.strftime("%Y-%m-%dT%H:%M:%S.000000000Z") for t in ts)


def provider(
    handler: Any, live: Any = None, ends: list[datetime] | None = None
) -> tuple[DatabentoProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    ends = ends or [NOW - timedelta(minutes=20)]

    def route(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/v0/metadata.get_dataset_range":
            end = ends.pop(0) if len(ends) > 1 else ends[0]
            return httpx.Response(
                200,
                json={
                    "start": "2010-06-06T00:00:00Z",
                    "end": end.isoformat(),
                    "schema": {"ohlcv-1m": {"start": "2010-06-06T00:00:00Z", "end": end.isoformat()}},
                },
            )
        return handler(req)

    async def no_sleep(_: float) -> None:
        return None

    client = httpx.AsyncClient(transport=httpx.MockTransport(route), base_url="https://hist.databento.test")
    p = DatabentoProvider(
        KEY,
        client=client,
        min_interval_s=0,
        clock=lambda: NOW,
        sleep=no_sleep,
        live_factory=live or (lambda k: FakeLive([])),
        range_ttl_s=3600,
    )
    return p, seen


async def test_request_end_is_clamped_to_available_end() -> None:
    p, seen = provider(lambda r: httpx.Response(200, text=bars(NOW - timedelta(minutes=30))))
    out = await p.fetch_candles("ES", NOW - timedelta(hours=1), NOW)
    assert len(out) == 1 and out[0].quality == DataQuality.HISTORICAL
    q = next(r for r in seen if r.url.path == "/v0/timeseries.get_range").url.params
    assert q["end"] == "2026-11-17T14:40:00+00:00"  # available end 14:40:30 floored, not "now"
    assert q["symbols"] == "ES.v.0" and q["dataset"] == "GLBX.MDP3" and q["stype_in"] == "continuous"


async def test_window_entirely_after_cutoff_makes_no_billable_request() -> None:
    p, seen = provider(lambda r: pytest.fail("timeseries must not be requested"))
    assert await p.fetch_candles("ES", NOW - timedelta(minutes=10), NOW) == []
    assert [r.url.path for r in seen] == ["/v0/metadata.get_dataset_range"]


async def test_422_after_available_end_refreshes_range_and_retries_once_with_redacted_body() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                422,
                json={
                    "detail": {
                        "case": "data_end_after_available_end",
                        "status_code": 422,
                        "message": f"The end time is after the available end. key={KEY}",
                    }
                },
            )
        return httpx.Response(200, text=bars(NOW - timedelta(minutes=45)))

    # The cached range was optimistic (cutoff 14:59); the refreshed one says 14:30.
    p, seen = provider(handler, ends=[NOW - timedelta(minutes=1), NOW - timedelta(minutes=30)])
    out = await p.fetch_candles("ES", NOW - timedelta(hours=1), NOW)
    assert len(out) == 1
    ranges = [r for r in seen if r.url.path == "/v0/timeseries.get_range"]
    assert [r.url.params["end"] for r in ranges] == ["2026-11-17T14:59:00+00:00", "2026-11-17T14:30:00+00:00"]
    assert p.last_error["case"] == "data_end_after_available_end"
    assert KEY not in p.last_error["message"] and "db-***" in p.last_error["message"]


async def test_other_4xx_is_a_non_retryable_request_error_and_5xx_is_retryable() -> None:
    body = {"detail": {"case": "symbology_invalid_symbol", "message": "Invalid symbol ZZ.v.0"}}
    p, seen = provider(lambda r: httpx.Response(422, json=body))
    with pytest.raises(ProviderRequestError, match="symbology_invalid_symbol"):
        await p.fetch_candles("ES", NOW - timedelta(hours=1), NOW)
    assert len([r for r in seen if r.url.path == "/v0/timeseries.get_range"]) == 1
    p, _ = provider(lambda r: httpx.Response(503, text="maintenance"))
    with pytest.raises(ProviderError) as e:
        await p.fetch_candles("ES", NOW - timedelta(hours=1), NOW)
    assert not isinstance(e.value, ProviderRequestError)


def test_redaction() -> None:
    assert redact(f"bad key {KEY}") == "bad key db-***"
    assert redact("user secret-123 here", key="secret-123") == "user *** here"


# --- live ------------------------------------------------------------------------------------


@dataclass
class Mapping:
    instrument_id: int
    stype_in_symbol: str
    stype_out_symbol: str


@dataclass
class Ohlcv:
    instrument_id: int
    ts_event: int
    open: int
    high: int
    low: int
    close: int
    volume: int


@dataclass
class System:
    msg: str


@dataclass
class Error:
    err: str


def ns(t: datetime) -> int:
    return int(t.timestamp()) * 1_000_000_000


def px(v: float) -> int:
    return round(v * 1e9)


class FakeLive:
    def __init__(self, records: list[Any], hold: bool = False) -> None:
        self.records = records
        self.hold = hold
        self.subscriptions: list[dict[str, Any]] = []
        self.started = self.stopped = False

    def subscribe(self, **kw: Any) -> int:
        self.subscriptions.append(kw)
        return 1

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def __aiter__(self) -> FakeLive:
        self._it = iter(self.records)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._it)
        except StopIteration:
            if self.hold:
                await asyncio.sleep(3600)
            raise StopAsyncIteration from None


def live_records() -> list[Any]:
    t1, t2 = NOW.replace(second=0) - timedelta(minutes=2), NOW.replace(second=0) - timedelta(minutes=1)
    return [
        Mapping(101, "ES.v.0", "ESZ6"),
        Mapping(202, "NQ.v.0", "NQZ6"),
        System("Subscription request 1 for ohlcv-1m data succeeded"),
        Ohlcv(101, ns(t1), px(5000.25), px(5001.0), px(4999.75), px(5000.5), 310),
        Ohlcv(202, ns(t1), px(21000.0), px(21004.5), px(20998.25), px(21003.0), 150),
        Ohlcv(999, ns(t1), px(1), px(1), px(1), px(1), 1),  # unmapped instrument: ignored
        Ohlcv(101, ns(t2), px(5000.5), px(5002.0), px(5000.25), px(5001.75), 280),
    ]


async def test_live_stream_maps_contracts_labels_live_and_replays_from_since() -> None:
    fake = FakeLive(live_records())
    p, _ = provider(lambda r: httpx.Response(200, text=""), live=lambda key: fake)
    out = []
    with pytest.raises(ProviderError, match="live session ended"):
        async for c in p.stream_candles(["ES", "NQ"], since=NOW - timedelta(hours=2)):
            out.append(c)
    sub = fake.subscriptions[0]
    assert sub["dataset"] == "GLBX.MDP3" and sub["schema"] == "ohlcv-1m" and sub["stype_in"] == "continuous"
    assert sub["symbols"] == ["ES.v.0", "NQ.v.0"] and sub["start"] == NOW - timedelta(hours=2)
    assert fake.started and fake.stopped
    assert [(c.symbol, str(c.close), c.quality) for c in out] == [
        ("ES", "5000.5", DataQuality.LIVE),
        ("NQ", "21003", DataQuality.LIVE),
        ("ES", "5001.75", DataQuality.LIVE),
    ]
    assert out[0].ts == NOW.replace(second=0) - timedelta(minutes=2) and out[0].volume == 310


async def test_live_replay_start_is_capped_to_the_replay_window() -> None:
    fake = FakeLive([])
    p, _ = provider(lambda r: httpx.Response(200), live=lambda key: fake)
    with pytest.raises(ProviderError):
        async for _ in p.stream_candles(["ES"], since=NOW - timedelta(days=3)):
            pass
    assert fake.subscriptions[0]["start"] == NOW - timedelta(hours=23)


async def test_live_gateway_errors_are_classified() -> None:
    p, _ = provider(
        lambda r: httpx.Response(200),
        live=lambda key: FakeLive([Error("Authentication failed: no live license for GLBX.MDP3")]),
    )
    with pytest.raises(ProviderAuthError, match="no live license"):
        async for _ in p.stream_candles(["ES"]):
            pass

    def refuse(key: str) -> Any:
        raise ConnectionRefusedError(f"connection refused for {key}")

    p, _ = provider(lambda r: httpx.Response(200), live=refuse)
    with pytest.raises(ProviderError) as e:
        async for _ in p.stream_candles(["ES"]):
            pass
    assert KEY not in str(e.value) and not isinstance(e.value, ProviderAuthError)


# --- ingestion: historical -> live transition without a reconnect loop -----------------------


def ingest(p: DatabentoProvider, symbols: tuple[str, ...] = ("ES", "NQ")) -> IngestionService:
    return IngestionService(
        p,
        symbols,
        get_sessionmaker(),
        get_redis(),
        backfill_hours=2,
        clock=lambda: NOW,
        backoff_base_s=0.01,
        backoff_max_s=0.05,
    )


async def test_backfill_to_cutoff_then_live_replay_from_last_stored_bar() -> None:
    def hist(req: httpx.Request) -> httpx.Response:
        start = datetime.fromisoformat(req.url.params["start"])
        return httpx.Response(200, text=bars(start, start + timedelta(minutes=1)))

    fake = FakeLive(live_records(), hold=True)
    p, seen = provider(hist, live=lambda key: fake, ends=[NOW - timedelta(minutes=20)])
    svc = ingest(p)
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    for _ in range(300):
        if (
            svc.status["last_event"].get("ES", "").startswith(NOW.replace(second=0).isoformat()[:13])
            and svc.status["state"] == "streaming"
            and fake.started
        ):
            break
        await asyncio.sleep(0.01)
    try:
        assert svc.status["state"] == "streaming" and svc.status["reconnects"] == 0
        ranges = [r.url.params["end"] for r in seen if r.url.path == "/v0/timeseries.get_range"]
        assert ranges and max(ranges) <= "2026-11-17T14:40:00+00:00"  # never past the cutoff
        # Live replay starts right after the oldest per-symbol last stored bar.
        assert fake.subscriptions[0]["start"] <= NOW - timedelta(minutes=20)
        assert svc.status["last_event"]["ES"] == (NOW.replace(second=0) - timedelta(minutes=1)).isoformat()
    finally:
        stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_invalid_historical_request_does_not_cause_a_reconnect_loop() -> None:
    body = {"detail": {"case": "symbology_invalid_request", "message": "bad request"}}
    fake = FakeLive(live_records(), hold=True)
    p, seen = provider(lambda r: httpx.Response(422, json=body), live=lambda key: fake)
    svc = ingest(p)
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    for _ in range(300):
        if svc.status["state"] == "streaming" and "ES" in svc.status["last_event"]:
            break
        await asyncio.sleep(0.01)
    try:
        assert svc.status["state"] == "streaming" and svc.status["reconnects"] == 0
        assert "symbology_invalid_request" in svc.status["last_error"]
        # One rejected request per symbol, not one per retry cycle.
        assert len([r for r in seen if r.url.path == "/v0/timeseries.get_range"]) == 2
    finally:
        stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_live_disconnect_reconnects_with_backoff_and_resumes() -> None:
    sessions: list[FakeLive] = []

    def factory(key: str) -> FakeLive:
        f = FakeLive(live_records()[:3] if not sessions else live_records(), hold=bool(sessions))
        sessions.append(f)
        return f

    p, _ = provider(lambda r: httpx.Response(200, text=""), live=factory)
    svc = ingest(p)
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    for _ in range(300):
        if len(sessions) >= 2 and "ES" in svc.status["last_event"]:
            break
        await asyncio.sleep(0.01)
    try:
        assert len(sessions) >= 2 and svc.status["reconnects"] >= 1
        assert "live session ended" in svc.status["last_error"]
        assert svc.status["state"] == "streaming"
    finally:
        stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
