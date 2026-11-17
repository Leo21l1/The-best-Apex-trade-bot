from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.adapters.marketdata.base import ProviderAuthError, ProviderError
from app.adapters.marketdata.databento import DatabentoProvider, parse_ohlcv_lines
from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import Settings
from app.marketdata.factory import build_provider
from app.marketdata.instruments import INSTRUMENTS
from app.marketdata.types import DataQuality
from app.marketdata.validation import validate_candle

NOW = datetime(2026, 11, 11, 15, 30, 20, tzinfo=UTC)  # Wed 10:30:20 ET


def sim() -> SimulatedProvider:
    return SimulatedProvider(clock=lambda: NOW, tick_seconds=0)


async def test_simulated_bars_are_valid_labelled_and_deterministic() -> None:
    start = NOW - timedelta(hours=30)
    for symbol in INSTRUMENTS:
        bars = await sim().fetch_candles(symbol, start, NOW)
        assert bars, symbol
        assert all(b.quality == DataQuality.SIMULATED and b.provider == "simulated" for b in bars)
        assert all(validate_candle(b, NOW) == [] for b in bars), symbol
        assert [b.ts for b in bars] == sorted({b.ts for b in bars})
        assert bars == await sim().fetch_candles(symbol, start, NOW)  # reproducible


async def test_simulated_history_excludes_closed_market_and_unfinished_bar() -> None:
    bars = await sim().fetch_candles("ES", NOW - timedelta(days=7), NOW + timedelta(hours=1))
    assert bars[-1].ts == datetime(2026, 11, 11, 15, 29, tzinfo=UTC)  # 15:30 bar still forming
    assert not any(b.ts.weekday() == 5 for b in bars)  # no Saturday bars
    # 22:00-22:59 UTC is the daily maintenance break in November.
    assert not any(b.ts.hour == 22 for b in bars)


async def test_simulated_micro_tracks_standard_contract() -> None:
    es = await sim().fetch_candles("ES", NOW - timedelta(minutes=30), NOW)
    mes = await sim().fetch_candles("MES", NOW - timedelta(minutes=30), NOW)
    assert [b.close for b in es] == [b.close for b in mes]


async def test_simulated_stream_emits_closed_then_forming_bar() -> None:
    out = []
    async for c in sim().stream_candles(["ES", "CL"]):
        out.append(c)
        if len(out) == 4:
            break
    assert [(c.symbol, c.is_closed, c.ts.minute) for c in out] == [
        ("ES", True, 29),
        ("ES", False, 30),
        ("CL", True, 29),
        ("CL", False, 30),
    ]
    assert all(validate_candle(c, NOW) == [] for c in out)


def test_factory() -> None:
    assert build_provider(Settings(market_data_provider="none")) is None
    assert isinstance(build_provider(Settings(market_data_provider="simulated")), SimulatedProvider)
    with pytest.raises(ProviderAuthError):
        build_provider(Settings(market_data_provider="databento", databento_api_key=None))


# --- Databento (recorded response shapes; not verified against the live service) -----------

PRETTY = (
    '{"hd":{"ts_event":"2026-11-11T15:00:00.000000000Z","rtype":33,"publisher_id":1,'
    '"instrument_id":1},"open":"5000.250000000","high":"5001.000000000",'
    '"low":"4999.750000000","close":"5000.500000000","volume":"1234"}\n'
    '{"hd":{"ts_event":"2026-11-11T15:01:00.000000000Z","rtype":33,"publisher_id":1,'
    '"instrument_id":1},"open":"5000.500000000","high":"5000.750000000",'
    '"low":"5000.000000000","close":"5000.250000000","volume":"987"}\n'
)
FIXED = (
    '{"hd":{"ts_event":"1794495600000000000","rtype":33},"open":"5000250000000",'
    '"high":"5001000000000","low":"4999750000000","close":"5000500000000","volume":"10"}\n'
)


def test_databento_parses_pretty_and_fixed_point() -> None:
    bars = parse_ohlcv_lines(PRETTY, "ES", "databento")
    assert len(bars) == 2
    assert bars[0].ts == datetime(2026, 11, 11, 15, 0, tzinfo=UTC)
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume) == (
        Decimal("5000.25"),
        Decimal("5001"),
        Decimal("4999.75"),
        Decimal("5000.5"),
        1234,
    )
    assert bars[0].quality == DataQuality.HISTORICAL  # never labelled live
    assert validate_candle(bars[0], NOW) == []
    fixed = parse_ohlcv_lines(FIXED, "ES", "databento")[0]
    assert fixed.open == Decimal("5000.25")
    assert fixed.ts == datetime.fromtimestamp(1794495600, tz=UTC)


def test_databento_malformed_record_raises() -> None:
    with pytest.raises(ProviderError):
        parse_ohlcv_lines('{"hd":{"ts_event":"x"},"open":"1"}\n', "ES", "databento")
    with pytest.raises(ProviderError):
        parse_ohlcv_lines("not json\n", "ES", "databento")


def _provider(handler: httpx.MockTransport, meta: bool = True) -> tuple[DatabentoProvider, list[float]]:
    """``meta``: answer metadata.get_dataset_range with an available end one hour after NOW."""
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    inner = handler

    async def route(req: httpx.Request) -> httpx.Response:
        if meta and req.url.path == "/v0/metadata.get_dataset_range":
            return httpx.Response(
                200, json={"start": "2010-06-06T00:00:00Z", "end": (NOW + timedelta(hours=1)).isoformat()}
            )
        return await inner.handle_async_request(req)

    client = httpx.AsyncClient(transport=httpx.MockTransport(route), base_url="https://hist.databento.test")
    return DatabentoProvider("key", client=client, min_interval_s=0, sleep=fake_sleep, clock=lambda: NOW), sleeps


async def test_databento_request_shape_and_success() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, text=PRETTY)

    p, _ = _provider(httpx.MockTransport(handler))
    bars = await p.fetch_candles("ES", NOW - timedelta(hours=1), NOW)
    assert len(bars) == 2
    q = seen[0].url.params
    assert seen[0].url.path == "/v0/timeseries.get_range"
    assert (q["dataset"], q["symbols"], q["stype_in"], q["schema"]) == (
        "GLBX.MDP3",
        "ES.v.0",  # continuous front month by previous-day volume (default roll rule)
        "continuous",
        "ohlcv-1m",
    )


async def test_databento_auth_error_and_http_error() -> None:
    p, _ = _provider(httpx.MockTransport(lambda r: httpx.Response(401)), meta=False)
    with pytest.raises(ProviderAuthError):
        await p.fetch_candles("ES", NOW - timedelta(hours=1), NOW)
    p, _ = _provider(httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(ProviderError):
        await p.fetch_candles("ES", NOW - timedelta(hours=1), NOW)

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    p, _ = _provider(httpx.MockTransport(boom), meta=False)
    with pytest.raises(ProviderError):
        await p.fetch_candles("ES", NOW - timedelta(hours=1), NOW)


async def test_databento_rate_limit_honours_retry_after_then_gives_up() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "7"})
        return httpx.Response(200, text=PRETTY)

    p, sleeps = _provider(httpx.MockTransport(handler))
    assert len(await p.fetch_candles("ES", NOW - timedelta(hours=1), NOW)) == 2
    assert sleeps == [7.0, 7.0]

    p, sleeps = _provider(httpx.MockTransport(lambda r: httpx.Response(429)))
    with pytest.raises(ProviderError, match="retries exhausted"):
        await p.fetch_candles("ES", NOW - timedelta(hours=1), NOW)
    assert len(sleeps) == 4
