from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class ComponentState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
    NOT_CONFIGURED = "not_configured"


class ComponentStatus(BaseModel):
    name: str
    state: ComponentState
    detail: str
    latency_ms: float | None = None
    checked_at: datetime


class SystemStatus(BaseModel):
    app: str
    version: str
    environment: str
    execution_mode: str
    live_order_routing: bool
    timezone: str
    server_time: datetime
    overall: ComponentState
    #: Provenance class of market data currently being served ("unavailable" if none).
    market_data_quality: str
    market_data_provider: str | None
    components: list[ComponentStatus]


class Liveness(BaseModel):
    status: str
    server_time: datetime


class Readiness(BaseModel):
    status: str
    checks: dict[str, ComponentState]
