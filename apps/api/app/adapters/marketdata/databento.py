"""Databento historical adapter (CME Globex MDP 3.0, dataset ``GLBX.MDP3``).

Databento is an independently licensed market-data vendor. This adapter uses the documented
HTTP historical API (``timeseries.get_range``, schema ``ohlcv-1m``, continuous front-month
symbology ``ROOT.c.0``).

STATUS: UNVERIFIED. Written from public API documentation and tested only against recorded
response shapes; it has not been run against the live service because no API key was available
in the build environment. Because it reads the *historical* API, all bars are labelled
``HISTORICAL`` even when recent. It never claims to be live. A real-time adapter (Databento
Live or another licensed feed) is a separate, future adapter.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from app.adapters.marketdata.base import (
    MarketDataProvider,
    ProviderAuthError,
    ProviderError,
    ProviderInfo,
)
from app.marketdata.calendar import floor_minute
from app.marketdata.types import BASE_TIMEFRAME, Candle, DataQuality

BASE_URL = "https://hist.databento.com"
DATASET = "GLBX.MDP3"
_FIXED_PRICE_SCALE = Decimal("1e-9")


def _parse_ts(value: object) -> datetime:
    if isinstance(value, str) and not value.isdigit():
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    ns = int(value)  # type: ignore[arg-type]
    return datetime.fromtimestamp(ns // 1_000_000_000, tz=UTC)


def _parse_px(value: object) -> Decimal:
    if isinstance(value, str) and "." in value:
        return Decimal(value)
    return Decimal(int(value)) * _FIXED_PRICE_SCALE  # type: ignore[arg-type]


def parse_ohlcv_lines(body: str, symbol: str, provider: str) -> list[Candle]:
    """Parse newline-delimited JSON OHLCV records. Malformed lines raise ProviderError."""
    out: list[Candle] = []
    for line in body.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            hd = rec.get("hd", rec)
            out.append(
                Candle(
                    symbol=symbol,
                    timeframe=BASE_TIMEFRAME,
                    ts=_parse_ts(hd["ts_event"]),
                    open=_parse_px(rec["open"]).normalize(),
                    high=_parse_px(rec["high"]).normalize(),
                    low=_parse_px(rec["low"]).normalize(),
                    close=_parse_px(rec["close"]).normalize(),
                    volume=int(rec["volume"]),
                    provider=provider,
                    quality=DataQuality.HISTORICAL,
                    is_closed=True,
                )
            )
        except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
            raise ProviderError(f"malformed Databento record: {type(exc).__name__}") from exc
    return out


class DatabentoProvider(MarketDataProvider):
    info = ProviderInfo(
        name="databento",
        stream_quality=DataQuality.HISTORICAL,
        description="Databento GLBX.MDP3 historical OHLCV (adapter not yet verified live).",
        verified=False,
    )

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        min_interval_s: float = 0.25,
        poll_seconds: float = 60.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], asyncio.Future | object] = asyncio.sleep,
    ) -> None:
        if not api_key:
            raise ProviderAuthError("APEX_DATABENTO_API_KEY is not set")
        self._client = client or httpx.AsyncClient(base_url=BASE_URL, auth=(api_key, ""), timeout=30.0)
        self._min_interval = min_interval_s
        self._poll = poll_seconds
        self._clock = clock
        self._sleep = sleep
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def _throttle(self) -> None:
        """Client-side rate limit: at most one request per ``min_interval_s``."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            wait = self._last_request + self._min_interval - loop.time()
            if wait > 0:
                await self._sleep(wait)  # type: ignore[misc]
            self._last_request = loop.time()

    async def fetch_candles(self, symbol: str, start: datetime, end: datetime) -> list[Candle]:
        params = {
            "dataset": DATASET,
            "symbols": f"{symbol}.c.0",
            "stype_in": "continuous",
            "schema": "ohlcv-1m",
            "encoding": "json",
            "pretty_px": "true",
            "pretty_ts": "true",
            "start": start.astimezone(UTC).isoformat(),
            "end": end.astimezone(UTC).isoformat(),
        }
        for attempt in range(4):
            await self._throttle()
            try:
                r = await self._client.get("/v0/timeseries.get_range", params=params)
            except httpx.HTTPError as exc:
                raise ProviderError(f"Databento request failed: {type(exc).__name__}") from exc
            if r.status_code in (401, 403):
                raise ProviderAuthError("Databento rejected the API key or entitlement")
            if r.status_code == 429:
                retry_after = float(r.headers.get("retry-after", 2**attempt))
                await self._sleep(min(retry_after, 60.0))  # type: ignore[misc]
                continue
            if r.status_code >= 400:
                raise ProviderError(f"Databento HTTP {r.status_code}")
            return parse_ohlcv_lines(r.text, symbol, self.info.name)
        raise ProviderError("Databento rate limit: retries exhausted")

    async def stream_candles(self, symbols: Sequence[str]) -> AsyncIterator[Candle]:
        """Poll the historical API for newly published bars (labelled HISTORICAL)."""
        cursor = {s: floor_minute(self._clock()) - timedelta(minutes=30) for s in symbols}
        while True:
            now = floor_minute(self._clock())
            for s in symbols:
                for c in await self.fetch_candles(s, cursor[s], now):
                    cursor[s] = max(cursor[s], c.ts + timedelta(minutes=1))
                    yield c
            await self._sleep(self._poll)  # type: ignore[misc]

    async def aclose(self) -> None:
        await self._client.aclose()
