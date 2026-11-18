"""Databento adapter for CME Globex MDP 3.0 (dataset ``GLBX.MDP3``).

Two documented Databento APIs are used, each for what it supports:

* **Historical** HTTP API (``timeseries.get_range``, schema ``ohlcv-1m``) for backfill. Historical
  data is published with a delay, so every request is clamped to the dataset's available end
  (``metadata.get_dataset_range``, free). Asking for data past that end is what produced the
  HTTP 422 ``data_end_after_available_end`` loop; a 422 now refreshes the range and retries once,
  and any other 4xx is a non-retryable request error rather than a reconnect loop.
* **Live** API (official ``databento`` client, ``Live.subscribe``) for current bars, using
  *intraday replay*: the subscription starts at the last stored bar (within Databento's 24-hour
  replay window) so the gap between the historical cutoff and now is filled by the live gateway,
  then the same session continues in real time. Live bars are labelled ``LIVE``.

Futures are requested as continuous front-month symbols ``ROOT.<rule>.0``; the default rule ``v``
(highest previous-day volume) follows the contract traders actually use around rolls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.adapters.marketdata.base import (
    MarketDataProvider,
    ProviderAuthError,
    ProviderError,
    ProviderInfo,
    ProviderRequestError,
)
from app.core.logging import log_event
from app.marketdata.calendar import floor_minute
from app.marketdata.types import BASE_TIMEFRAME, Candle, DataQuality

log = logging.getLogger("apex.marketdata.databento")

BASE_URL = "https://hist.databento.com"
DATASET = "GLBX.MDP3"
SCHEMA = "ohlcv-1m"
REPLAY_WINDOW = timedelta(hours=23)  # Databento intraday replay covers the last 24 hours
_FIXED_PRICE_SCALE = Decimal("1e-9")
_KEY_RE = re.compile(r"db-[A-Za-z0-9]{8,}")


def redact(text: str, key: str | None = None) -> str:
    out = _KEY_RE.sub("db-***", text)
    if key:
        out = out.replace(key, "***")
    return out[:600]


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


def error_detail(r: httpx.Response, key: str | None) -> tuple[str, str]:
    """(case, redacted message) from a Databento error response body."""
    try:
        body = r.json()
        d = body.get("detail", body) if isinstance(body, dict) else body
        if isinstance(d, dict):
            return str(d.get("case", "")), redact(str(d.get("message") or d), key)
        return "", redact(str(d), key)
    except ValueError:
        return "", redact(r.text, key)


def _live_factory(key: str) -> Any:
    import databento as db

    return db.Live(key=key)


class DatabentoProvider(MarketDataProvider):
    info = ProviderInfo(
        name="databento",
        stream_quality=DataQuality.LIVE,
        description="Databento GLBX.MDP3: historical OHLCV backfill + Live API (intraday replay) for current bars.",
        verified=False,
    )

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        min_interval_s: float = 0.25,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Any] = asyncio.sleep,
        live_factory: Callable[[str], Any] = _live_factory,
        roll_rule: str = "v",
        range_ttl_s: float = 60.0,
        timeout_s: float = 60.0,
        max_retries: int = 3,
        backoff_base_s: float = 1.0,
    ) -> None:
        if not api_key:
            raise ProviderAuthError("APEX_DATABENTO_API_KEY is not set")
        self._key = api_key
        # Connect quickly; allow the server time to compute (cost estimates and large ranges can be slow).
        self._timeout = httpx.Timeout(timeout_s, connect=min(15.0, timeout_s))
        self._client = client or httpx.AsyncClient(base_url=BASE_URL, auth=(api_key, ""), timeout=self._timeout)
        self._timeout_s = timeout_s
        self._max_retries = max(0, min(int(max_retries), 6))  # hard cap: requests never retry indefinitely
        self._backoff = backoff_base_s
        self._min_interval = min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._live_factory = live_factory
        self._roll = roll_rule
        self._range_ttl = timedelta(seconds=range_ttl_s)
        self._range: tuple[datetime, datetime] | None = None  # (available end, fetched at)
        self._last_request = 0.0
        self._lock = asyncio.Lock()
        self.last_error: dict[str, Any] | None = None
        #: root symbol -> {"contract": e.g. "ESZ6", "since": ISO time} from live symbol mappings.
        self.contract_map: dict[str, dict[str, str]] = {}

    def continuous(self, symbol: str) -> str:
        return f"{symbol}.{self._roll}.0"

    async def _throttle(self) -> None:
        """Client-side rate limit: at most one request per ``min_interval_s``."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            wait = self._last_request + self._min_interval - loop.time()
            if wait > 0:
                await self._sleep(wait)
            self._last_request = loop.time()

    async def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        return await self._request("GET", path, params)

    async def resolve_contracts(self, symbol: str, start: date, end: date) -> dict[str, str]:
        """Actual contract behind the continuous symbol per date (``symbology.resolve``, metadata - not
        a data download). Intervals are [d0, d1). Returns {"YYYY-MM-DD": "ESZ6", ...}."""
        r = await self._request(
            "POST",
            "/v0/symbology.resolve",
            {
                "dataset": DATASET,
                "symbols": self.continuous(symbol),
                "stype_in": "continuous",
                "stype_out": "raw_symbol",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )
        if r.status_code >= 400:
            case, msg = error_detail(r, self._key)
            raise ProviderError(f"Databento symbology HTTP {r.status_code} {case}: {msg}")
        try:
            body = r.json()
            intervals = (body.get("result") or {}).get(self.continuous(symbol)) or []
        except (ValueError, AttributeError) as exc:
            raise ProviderError("unexpected Databento symbology response") from exc
        out: dict[str, str] = {}
        for iv in intervals:
            d, d1, sym = date.fromisoformat(str(iv["d0"])[:10]), date.fromisoformat(str(iv["d1"])[:10]), str(iv["s"])
            while d < d1:
                out[d.isoformat()] = sym
                d += timedelta(days=1)
        return out

    async def _request(self, method: str, path: str, params: dict[str, Any]) -> httpx.Response:
        """GET with bounded retries and exponential backoff (1 + ``max_retries`` attempts at most).

        * Metadata endpoints (cost, dataset range, ...) are free and idempotent: timeouts, dropped
          connections, HTTP 429 and 5xx are retried.
        * ``timeseries.*`` downloads are billed. A read timeout happens after Databento received the
          request, so retrying could be charged twice: only failures where the request was never
          sent (connect errors, connect/pool timeouts) and HTTP 429 are retried there.
        Errors never include the API key (it travels in the auth header, not the URL).
        """
        billed = path.startswith("/v0/timeseries")
        attempts = 1 + self._max_retries
        last = ""
        for attempt in range(attempts):
            await self._throttle()
            try:
                r = (
                    await self._client.get(path, params=params)
                    if method == "GET"
                    else await self._client.post(path, data=params)
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                last = f"{type(exc).__name__} (request not sent)"
            except (httpx.TimeoutException, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                last = f"{type(exc).__name__} after {self._timeout_s:g}s"
                if billed:
                    raise ProviderError(
                        f"Databento download {path} failed: {last}. Not retried automatically because the "
                        "request reached Databento and may be billed; the backfill resumes later without "
                        "re-downloading stored sessions."
                    ) from exc
            except httpx.HTTPError as exc:
                raise ProviderError(f"Databento request failed: {type(exc).__name__}") from exc
            else:
                if r.status_code in (401, 403):
                    _case, msg = error_detail(r, self._key)
                    raise ProviderAuthError(
                        f"Databento rejected the API key or entitlement (HTTP {r.status_code}): {msg}"
                    )
                if r.status_code == 429:
                    last = "HTTP 429 rate limited"
                    if attempt + 1 < attempts:
                        await self._sleep(min(float(r.headers.get("retry-after", self._backoff * 2**attempt)), 60.0))
                    continue
                if r.status_code >= 500 and not billed and attempt + 1 < attempts:
                    last = f"HTTP {r.status_code}"
                    await self._sleep(min(self._backoff * 2**attempt, 30.0))
                    continue
                return r
            if attempt + 1 < attempts:
                delay = min(self._backoff * 2**attempt, 30.0)
                log_event(
                    log,
                    "databento request retry",
                    level=logging.WARNING,
                    path=path,
                    attempt=attempt + 1,
                    of=attempts,
                    reason=last,
                    retry_in_s=delay,
                )
                await self._sleep(delay)
        raise ProviderError(f"Databento {path} failed after {attempts} attempts: {last}")

    async def available_end(self, refresh: bool = False) -> datetime:
        """Latest time with published historical data for ``ohlcv-1m`` (metadata call, not billed)."""
        now = self._clock()
        if not refresh and self._range and now - self._range[1] < self._range_ttl:
            return self._range[0]
        r = await self._get("/v0/metadata.get_dataset_range", {"dataset": DATASET})
        if r.status_code >= 400:
            case, msg = error_detail(r, self._key)
            raise ProviderError(f"Databento dataset range HTTP {r.status_code} {case}: {msg}")
        try:
            body = r.json()
            per_schema = (body.get("schema") or {}).get(SCHEMA) or {}
            end = _parse_ts(per_schema.get("end") or body["end"])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ProviderError(f"unexpected Databento dataset range response: {type(exc).__name__}") from exc
        self._range = (end, now)
        return end

    async def fetch_candles(self, symbol: str, start: datetime, end: datetime) -> list[Candle]:
        """Closed bars in [start, end), clamped to the historical available end. May return []."""
        for attempt in range(2):
            limit = floor_minute(await self.available_end(refresh=attempt > 0))
            stop = min(end, limit)
            if start >= stop:
                return []  # nothing published yet for this window; the live session covers it
            params = {
                "dataset": DATASET,
                "symbols": self.continuous(symbol),
                "stype_in": "continuous",
                "schema": SCHEMA,
                "encoding": "json",
                "pretty_px": "true",
                "pretty_ts": "true",
                "start": start.astimezone(UTC).isoformat(),
                "end": stop.astimezone(UTC).isoformat(),
            }
            r = await self._get("/v0/timeseries.get_range", params)
            if r.status_code < 400:
                return parse_ohlcv_lines(r.text, symbol, self.info.name)
            case, msg = error_detail(r, self._key)
            self.last_error = {"status": r.status_code, "case": case, "message": msg, "end": params["end"]}
            log_event(
                log,
                "databento request rejected",
                level=logging.WARNING,
                status=r.status_code,
                case=case,
                message=msg,
                symbol=symbol,
                start=params["start"],
                end=params["end"],
            )
            if r.status_code == 422 and "available" in (case + msg).lower() and attempt == 0:
                continue  # the cutoff moved or was cached: refresh the range and clamp again
            if r.status_code >= 500:
                raise ProviderError(f"Databento HTTP {r.status_code} {case}: {msg}")  # server side: retry later
            raise ProviderRequestError(f"Databento HTTP {r.status_code} {case}: {msg}")
        return []

    async def stream_candles(self, symbols: Sequence[str], since: datetime | None = None) -> AsyncIterator[Candle]:
        """Live ``ohlcv-1m`` bars via the Databento Live API, replaying from ``since`` (max 23h back).

        Each call creates its own ``Live`` client: subscribe first, then iterate once (which starts
        the session). The session is terminated when the generator finishes or is closed.
        """
        now = self._clock()
        start = max(since or now - timedelta(minutes=30), now - REPLAY_WINDOW)
        try:
            client = self._live_factory(self._key)
            client.subscribe(
                dataset=DATASET,
                schema=SCHEMA,
                stype_in="continuous",
                symbols=[self.continuous(s) for s in symbols],
                start=start,
            )
            # Lifecycle (databento >= 0.4x): creating the iterator starts the session
            # (LiveIterator.__init__ calls Live.start), so start() must NOT also be called here.
            # Calling both raised "Session can only be started once". One client = one session:
            # reconnects always build a new client through the factory, never restart this one.
            records = client.__aiter__()
        except Exception as exc:  # databento raises BentoError subclasses; classify by message
            raise _classify(exc) from exc
        roots = {self.continuous(s): s for s in symbols}
        by_id: dict[int, str] = {}
        try:
            while True:
                # Drive the LiveIterator directly: it only implements __anext__ (an `async for` over
                # it would call __aiter__ again), and the session was started when it was created.
                try:
                    rec = await records.__anext__()
                except StopAsyncIteration:
                    break
                if hasattr(rec, "stype_in_symbol"):  # SymbolMappingMsg: instrument_id -> our root
                    root = roots.get(str(rec.stype_in_symbol))
                    if root:
                        by_id[int(rec.instrument_id)] = root
                        self.contract_map[root] = {
                            "contract": str(rec.stype_out_symbol),
                            "rule": self.continuous(root),
                            "since": datetime.now(UTC).isoformat(),
                        }
                        log_event(log, "databento live mapping", symbol=root, contract=str(rec.stype_out_symbol))
                elif hasattr(rec, "err"):  # ErrorMsg from the gateway
                    raise _classify(RuntimeError(str(rec.err)))
                elif hasattr(rec, "volume") and hasattr(rec, "open"):  # OHLCVMsg
                    root = by_id.get(int(rec.instrument_id))
                    if root is None:
                        continue
                    yield Candle(
                        symbol=root,
                        timeframe=BASE_TIMEFRAME,
                        ts=_parse_ts(int(rec.ts_event)),
                        open=_parse_px(int(rec.open)).normalize(),
                        high=_parse_px(int(rec.high)).normalize(),
                        low=_parse_px(int(rec.low)).normalize(),
                        close=_parse_px(int(rec.close)).normalize(),
                        volume=int(rec.volume),
                        provider=self.info.name,
                        quality=DataQuality.LIVE,
                        is_closed=True,  # Databento publishes an ohlcv-1m bar when its minute ends
                    )
                # SystemMsg (heartbeats, replay-complete notices) need no action
        except ProviderError:
            raise
        except Exception as exc:
            raise _classify(exc) from exc
        finally:
            try:
                client.terminate()  # close this session for good (also when the consumer stops early)
            except Exception as exc:  # session may already be closed
                log_event(log, "databento live stop", level=logging.DEBUG, error=type(exc).__name__)
        raise ProviderError("Databento live session ended")

    async def aclose(self) -> None:
        await self._client.aclose()


def _classify(exc: Exception) -> ProviderError:
    msg = redact(str(exc))
    low = msg.lower()
    if any(w in low for w in ("auth", "api key", "entitle", "license", "permission", "not authorized")):
        return ProviderAuthError(f"Databento live: {msg}")
    return ProviderError(f"Databento live: {type(exc).__name__}: {msg}")
