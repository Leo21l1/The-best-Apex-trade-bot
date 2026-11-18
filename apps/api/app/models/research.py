from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class BacktestRun(Base):
    """One backtest experiment: parameters, dataset fingerprint and results, kept for reproducibility."""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), index=True)  # queued | running | done | failed
    strategy_id: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(32))
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    validated: Mapped[bool | None]
    error: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaperAccount(Base):
    """Isolated simulation account. Has no connection to any broker or Apex account."""

    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(32))
    #: Full book state (orders, positions, fills, balances) serialized by ``app.paper.engine``.
    state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    #: Open time of the last 1-minute bar processed, per symbol (JSON {symbol: iso}).
    cursor: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JournalTrade(Base):
    """A completed round-trip trade imported from a broker export, entered manually, or from paper trading."""

    __tablename__ = "journal_trades"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    #: Dedupe key from the source (e.g. buy/sell fill ids) so re-importing never duplicates.
    source_key: Mapped[str] = mapped_column(String(200), unique=True)
    source: Mapped[str] = mapped_column(String(16))  # csv | manual | paper
    trading_date: Mapped[date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    contract: Mapped[str | None] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    gross_pnl: Mapped[float] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float)
    strategy_id: Mapped[str | None] = mapped_column(String(32))
    planned_stop: Mapped[float | None] = mapped_column(Float)
    planned_target: Mapped[float | None] = mapped_column(Float)
    followed_plan: Mapped[bool | None]
    mistakes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    #: Slippage in dollars (already reflected in the fill prices; reported separately).
    slippage: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    #: Maximum favourable / adverse excursion in dollars while open (from stored 1-minute bars).
    mfe: Mapped[float | None] = mapped_column(Float)
    mae: Mapped[float | None] = mapped_column(Float)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("trade_plans.id", ondelete="SET NULL"), index=True)
    #: Market context, signal and news attribution at the time of the trade (computed, not AI).
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: Generated journal, coaching findings and hypothetical scenarios.
    review: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # --- annotations (trader's own words; never part of the execution record) ---
    setup: Mapped[str | None] = mapped_column(String(64))
    thesis: Mapped[str | None] = mapped_column(Text)
    psychology: Mapped[str | None] = mapped_column(Text)
    emotion_tags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    lessons: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    chart_links: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    # --- provenance of manual corrections ---
    #: Execution fields exactly as first recorded (import / manual entry / simulator). Written once,
    #: before the first correction, and never modified afterwards.
    original: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: Field -> {"value", "at", "by"} for every execution field changed by hand.
    corrections: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TradePlan(Base):
    """A trade idea written down before execution (paper, or manual in Tradovate)."""

    __tablename__ = "trade_plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    symbol: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(8))
    entry: Mapped[float | None] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    target: Mapped[float | None] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer)
    strategy_id: Mapped[str | None] = mapped_column(String(32))
    thesis: Mapped[str | None] = mapped_column(Text)
    #: BUY / SHORT / WAIT label, strategy signal and market context captured when the plan was made.
    signal_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    risk_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: draft | executed_paper | executed_manual | cancelled
    status: Mapped[str] = mapped_column(String(20), default="draft")
    execution: Mapped[str | None] = mapped_column(String(20))


class JournalNote(Base):
    """A personal note attached to a journal trade (add / edit / delete)."""

    __tablename__ = "journal_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("journal_trades.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text)


class JournalAttachment(Base):
    """A screenshot attached to a journal trade, stored in PostgreSQL (images only, size-limited)."""

    __tablename__ = "journal_attachments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("journal_trades.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(32))
    size: Mapped[int] = mapped_column(Integer)
    caption: Mapped[str | None] = mapped_column(String(500))
    data: Mapped[bytes] = mapped_column(LargeBinary)


class JournalEdit(Base):
    """Audit trail of journal changes: one row per changed field (or a full snapshot on delete).

    ``trade_id`` is deliberately not a foreign key so the history survives deletion.
    """

    __tablename__ = "journal_edits"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(Integer, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    user_email: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(24))  # edit | correct | note_* | attachment_* | delete | restore
    field: Mapped[str | None] = mapped_column(String(64))
    old: Mapped[Any] = mapped_column(JSONB, nullable=True)
    new: Mapped[Any] = mapped_column(JSONB, nullable=True)


class ResearchStudy(Base):
    """A complete research study for one instrument (see app.research.study): summary and trade log."""

    __tablename__ = "research_studies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    data_class: Mapped[str] = mapped_column(String(16))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    trades: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
