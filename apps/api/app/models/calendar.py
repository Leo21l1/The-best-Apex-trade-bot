from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
