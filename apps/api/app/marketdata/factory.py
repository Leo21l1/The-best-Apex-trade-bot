from __future__ import annotations

from app.adapters.marketdata.base import MarketDataProvider
from app.core.config import Settings


def build_provider(settings: Settings) -> MarketDataProvider | None:
    """Instantiate the configured provider, or None when market data is disabled."""
    name = settings.market_data_provider
    if name == "none":
        return None
    if name == "simulated":
        from app.adapters.marketdata.simulated import SimulatedProvider

        return SimulatedProvider()
    if name == "databento":
        from app.adapters.marketdata.databento import DatabentoProvider

        key = settings.databento_api_key.get_secret_value() if settings.databento_api_key else ""
        return DatabentoProvider(
            api_key=key,
            roll_rule=settings.databento_roll_rule,
            timeout_s=settings.databento_timeout_seconds,
            max_retries=settings.databento_max_retries,
        )
    raise ValueError(f"unknown market data provider {name!r}")
