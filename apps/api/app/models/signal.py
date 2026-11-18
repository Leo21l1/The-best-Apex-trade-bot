from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SignalRow(Base):
    """A strategy signal (candidate setup) detected by the scanner. Analysis only."""

    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("provider", "strategy_id", "symbol", "bar_ts"),
        Index("ix_signals_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    provider: Mapped[str] = mapped_column(String(32))
    quality: Mapped[str] = mapped_column(String(16))
    strategy_id: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(8))
    bar_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry: Mapped[float] = mapped_column(Float)
    entry_low: Mapped[float] = mapped_column(Float)
    entry_high: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    targets: Mapped[list[float]] = mapped_column(JSONB)
    score: Mapped[float] = mapped_column(Float)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # active | invalidated | target_reached | expired
    status: Mapped[str] = mapped_column(String(16), default="active")
    status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertRow(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(8))  # info | warning | critical
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(1000))
    symbol: Mapped[str | None] = mapped_column(String(16))
    dedupe_key: Mapped[str] = mapped_column(String(200))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SetupSignal(Base):
    """A technical setup recorded at the moment it was evaluated (forward-test history; never orders).

    The columns describing the setup (timestamps, levels, factors in ``original``) are written once and
    never changed. Only the lifecycle columns (status*, filled_at, outcome*) are updated afterwards.
    """

    __tablename__ = "setup_signals"
    __table_args__ = (Index("ix_setup_signals_symbol_confirmed", "symbol", "confirmed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    contract: Mapped[str | None] = mapped_column(String(16))
    data_class: Mapped[str] = mapped_column(String(16))
    strategy_id: Mapped[str] = mapped_column(String(32))
    engine_version: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(8))
    score: Mapped[int] = mapped_column(Integer)
    #: True when every requirement passed (TECHNICAL ... SETUP CONFIRMED); False = research observation (>= 3/5).
    qualified: Mapped[bool] = mapped_column(Boolean)
    evidence: Mapped[str] = mapped_column(String(32))
    trigger_bar: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Close time of the last candle whose data completed the evaluation (when it became knowable).
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry: Mapped[float] = mapped_column(Float)
    entry_low: Mapped[float] = mapped_column(Float)
    entry_high: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    tp1: Mapped[float] = mapped_column(Float)
    tp2: Mapped[float | None] = mapped_column(Float)
    net_rr_tp1: Mapped[float | None] = mapped_column(Float)
    net_risk_usd: Mapped[float | None] = mapped_column(Float)
    net_reward_usd: Mapped[float | None] = mapped_column(Float)
    original: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # --- lifecycle (updated) ---
    status: Mapped[str] = mapped_column(String(16), index=True)
    status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_reason: Mapped[str | None] = mapped_column(String(200))
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(16))
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_r: Mapped[float | None] = mapped_column(Float)
    outcome_usd: Mapped[float | None] = mapped_column(Float)
