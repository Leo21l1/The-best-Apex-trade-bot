from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EconomicEvent(Base):
    __tablename__ = "economic_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    #: Stable identity from the source (ICS UID) or generated for manual entries; prevents duplicates.
    uid: Mapped[str] = mapped_column(String(300), unique=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    title: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(32))
    #: high | medium | low (volatility relevance by event type; a classification, not a forecast)
    importance: Mapped[str] = mapped_column(String(8))
    affected: Mapped[list[str]] = mapped_column(JSONB)
    country: Mapped[str] = mapped_column(String(8), default="US")
    source: Mapped[str] = mapped_column(String(16))  # ics_file | ics_url | manual
    source_ref: Mapped[str | None] = mapped_column(String(500))
    #: Values exactly as published by the source; ``None`` when the source supplied none (never inferred).
    forecast: Mapped[str | None] = mapped_column(String(64))
    previous: Mapped[str | None] = mapped_column(String(64))
    actual: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceSyncState(Base):
    """Download bookkeeping for an external source (shared by every worker; survives restarts)."""

    __tablename__ = "source_sync_state"

    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(300))
    events: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class NewsItem(Base):
    """A published announcement from a configured source (official RSS feeds), never fabricated.

    ``published_at`` is the source's own timestamp; ``detected_at`` is when APEX first stored it.
    Replays and reviews may only use an item from its ``detected_at`` onward.
    """

    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(500), unique=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_name: Mapped[str] = mapped_column(String(120))
    credibility: Mapped[str] = mapped_column(String(16))  # official | licensed | unverified
    title: Mapped[str] = mapped_column(String(500))
    link: Mapped[str | None] = mapped_column(String(1000))
    summary: Mapped[str | None] = mapped_column(String(2000))
    categories: Mapped[list[str]] = mapped_column(JSONB, default=list)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    #: LOW | MODERATE | HIGH | CRITICAL | UNKNOWN (rule-based relevance, not a forecast)
    impact: Mapped[str] = mapped_column(String(10))
    impact_reason: Mapped[str] = mapped_column(String(300))
    instruments: Mapped[list[str]] = mapped_column(JSONB, default=list)
    cluster: Mapped[str] = mapped_column(String(500), index=True)
    #: Observed price reaction per instrument once enough bars exist (None until measured).
    reaction: Mapped[dict | None] = mapped_column(JSONB)
