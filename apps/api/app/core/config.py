"""Environment-based configuration.

All settings come from environment variables (or a local ``.env`` file in development).
Secrets are never hard-coded; development defaults are deliberately unusable in production.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

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
    model_config = SettingsConfigDict(
        env_prefix="APEX_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

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
    rate_limit_per_minute: int = 240
    auth_rate_limit_per_minute: int = 10

    # Worker heartbeat considered stale after this many seconds
    worker_heartbeat_stale_seconds: int = 30

    log_level: str = "INFO"
    log_json: bool = True

    # AI (Phase 5). Optional; never sent to the browser.
    anthropic_api_key: SecretStr | None = None

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
