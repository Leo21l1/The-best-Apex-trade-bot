from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RuleRow(Base):
    """One rule of a prop-firm rule profile with its verification record."""

    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("profile", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    profile: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(200))
    # "money" | "contracts" | "text"
    kind: Mapped[str] = mapped_column(String(16))
    value: Mapped[str] = mapped_column(String(1000))
    #: Numeric rules drive the risk engine; text rules document methodology.
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source_url: Mapped[str | None] = mapped_column(String(500))
    #: Where to look. Shown to the user as a starting point; not evidence.
    suggested_source_url: Mapped[str | None] = mapped_column(String(500))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    applies_to: Mapped[str | None] = mapped_column(String(200))
    version: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(String(1000))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AccountSnapshot(Base):
    """A point-in-time account reading entered manually or imported. Never from a live order API."""

    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String(16))  # manual | csv
    #: Closed (realised) account balance.
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    #: True when ``balance`` is the end-of-day balance for ``trading_date``.
    is_eod: Mapped[bool] = mapped_column(Boolean, default=False)
    trading_date: Mapped[date] = mapped_column(Date, index=True)
    positions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
