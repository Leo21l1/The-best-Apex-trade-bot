"""Provider-independent market-data adapter interface.

Every provider (licensed vendor, broker read-only feed, simulator) implements this contract.
Adapters only READ market data. They never expose order functionality.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.marketdata.types import Candle, DataQuality


class ProviderError(Exception):
    """Recoverable provider failure (network, rate limit, bad response)."""


class ProviderAuthError(ProviderError):
    """Credentials missing or rejected. Not retried automatically."""


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    name: str
    #: Best quality class this adapter can deliver for streamed bars.
    stream_quality: DataQuality
    description: str
    #: False until the adapter has been exercised against the real service.
    verified: bool
    supports_ticks: bool = False
    supports_quotes: bool = False


class MarketDataProvider(abc.ABC):
    info: ProviderInfo

    @abc.abstractmethod
    async def fetch_candles(self, symbol: str, start: datetime, end: datetime) -> list[Candle]:
        """Closed 1-minute bars with open time in [start, end), oldest first."""

    @abc.abstractmethod
    def stream_candles(self, symbols: Sequence[str]) -> AsyncIterator[Candle]:
        """Yield 1-minute bar updates (in-progress and closed) until cancelled.

        Raises ProviderError on disconnect; the ingestion service reconnects with backoff.
        """

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        """Release network resources."""
