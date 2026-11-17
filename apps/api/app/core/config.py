"""Environment-based configuration.

All settings come from environment variables (or a local ``.env`` file in development).
Secrets are never hard-coded; development defaults are deliberately unusable in production.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class ExecutionMode(StrEnum):
    """The only supported execution mode.

    PROJECT APEX is an analysis assistant. There is intentionally no live-execution mode;
    paper trading lives in an isolated simulator and is not an execution mode.
    """

    ANALYSIS_ONLY = "ANALYSIS_ONLY"


_DEV_SECRET = "dev-insecure-secret-change-me-0123456789abcdef"  # noqa: S105 - dev-only, rejected in production


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APEX_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Environment = Environment.DEVELOPMENT
    app_name: str = "PROJECT APEX"
    version: str = "0.1.0"
    timezone: str = "America/New_York"

    database_url: str = "postgresql+asyncpg://apex:apex_dev_password@localhost:5432/apex"
    redis_url: str = "redis://localhost:6379/0"

    secret_key: SecretStr = SecretStr(_DEV_SECRET)
    access_token_ttl_minutes: int = Field(default=60 * 8, ge=5, le=60 * 24)
    cookie_secure: bool = False
    cookie_name: str = "apex_session"
    # Required to create the first admin through the API when set (always required in production).
    bootstrap_token: SecretStr | None = None
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # Rate limiting (per client IP, token bucket)
    # Unauthenticated requests are limited per client IP; signed-in requests per user.
    rate_limit_per_minute: int = 240
    user_rate_limit_per_minute: int = 1200
    auth_rate_limit_per_minute: int = 10
    # Direct peers whose X-Forwarded-For header is trusted (the Next.js server / reverse proxy).
    trusted_proxies: list[str] = ["127.0.0.1", "::1"]

    # Worker heartbeat considered stale after this many seconds
    worker_heartbeat_stale_seconds: int = 30

    log_level: str = "INFO"
    log_json: bool = True

    # Market data (Phase 2). "none" shows no prices anywhere.
    market_data_provider: Literal["none", "simulated", "databento"] = "none"
    market_data_symbols: list[str] = [
        "ES",
        "MES",
        "NQ",
        "MNQ",
        "YM",
        "MYM",
        "RTY",
        "M2K",
        "CL",
        "MCL",
        "GC",
        "MGC",
    ]
    market_data_backfill_hours: int = Field(default=72, ge=1, le=24 * 30)
    # A feed is stale when the market is open and no bar arrived for this long.
    market_data_stale_seconds: int = Field(default=150, ge=10)
    databento_api_key: SecretStr | None = None

    # Scanner (Phase 3)
    scanner_interval_seconds: int = Field(default=20, ge=5, le=600)
    scanner_lookback_hours: int = Field(default=120, ge=24, le=24 * 14)
    alert_cooldown_seconds: int = Field(default=900, ge=0)

    # Transaction-cost ASSUMPTIONS used for estimates (not broker quotes; adjust to your plan).
    cost_commission_per_side_standard: float = Field(default=2.50, ge=0)
    cost_commission_per_side_micro: float = Field(default=0.60, ge=0)
    cost_slippage_ticks_per_side: float = Field(default=1.0, ge=0)

    # Risk (Phase 4)
    # An account snapshot older than this while the market is open is treated as stale.
    account_stale_minutes: int = Field(default=30, ge=1)
    # Advisory cap: warn when one trade would use more than this share of the remaining budget.
    risk_max_trade_fraction: float = Field(default=0.25, gt=0, le=1)

    # AI copilot (Phase 5). Optional; the key never leaves the server.
    anthropic_api_key: SecretStr | None = None
    copilot_model: str = "claude-opus-5-5"
    copilot_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    copilot_max_tool_rounds: int = Field(default=8, ge=1, le=20)
    copilot_rate_limit_per_minute: int = Field(default=12, ge=1)
    copilot_timeout_seconds: float = Field(default=120, ge=5)

    # Economic calendar (Phase 5)
    calendar_pre_event_minutes: int = Field(default=15, ge=0, le=240)
    calendar_post_event_minutes: int = Field(default=15, ge=0, le=240)
    #: Official .ics calendar URLs fetched daily by the worker (e.g. the BLS release schedule).
    calendar_ics_urls: list[str] = []

    @property
    def execution_mode(self) -> ExecutionMode:
        # Not configurable by design.
        return ExecutionMode.ANALYSIS_ONLY

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.environment == Environment.PRODUCTION:
            key = self.secret_key.get_secret_value()
            if key == _DEV_SECRET or len(key) < 32:
                raise ValueError("APEX_SECRET_KEY must be set to a random value >= 32 chars")
            if self.bootstrap_token is None:
                raise ValueError("APEX_BOOTSTRAP_TOKEN must be set in production")
            if not self.cookie_secure:
                raise ValueError("APEX_COOKIE_SECURE must be true in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
