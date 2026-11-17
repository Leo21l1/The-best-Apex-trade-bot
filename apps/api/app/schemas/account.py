from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.marketdata.instruments import get_instrument

Money = Decimal


class PositionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    quantity: int = Field(description="Signed: positive long, negative short", ge=-1000, le=1000)
    avg_price: Decimal = Field(gt=0)
    stop: Decimal | None = Field(default=None, gt=0)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, v: str) -> str:
        inst = get_instrument(v)
        if inst is None:
            raise ValueError("unknown symbol")
        return inst.symbol

    @field_validator("quantity")
    @classmethod
    def _nonzero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("quantity must not be zero")
        return v


class SnapshotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    balance: Decimal = Field(gt=0, lt=Decimal("100000000"), decimal_places=2)
    unrealized_pnl: Decimal = Field(
        default=Decimal("0"), gt=-Decimal("10000000"), lt=Decimal("10000000"), decimal_places=2
    )
    as_of: datetime | None = None
    is_eod: bool = False
    trading_date: date | None = None
    positions: list[PositionIn] = Field(default_factory=list, max_length=50)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("as_of")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        return v


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    as_of: datetime
    created_at: datetime
    source: str
    balance: Decimal
    unrealized_pnl: Decimal
    is_eod: bool
    trading_date: date
    positions: list[dict]
    note: str | None


class CsvImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csv: str = Field(min_length=1, max_length=400_000)
    dry_run: bool = False


class CsvImportOut(BaseModel):
    dry_run: bool
    imported: int
    rows: list[tuple[date, Decimal]]
    errors: list[str]
    date_column: str | None
    balance_column: str | None


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    key: str
    label: str
    kind: str
    value: str
    required: bool
    verified: bool
    source_url: str | None
    suggested_source_url: str | None
    verified_at: datetime | None
    applies_to: str | None
    version: str | None
    notes: str | None


class RulesOut(BaseModel):
    profile: str
    label: str
    verified: bool
    unverified: list[str]
    rules: list[RuleOut]
    warning: str | None


class RuleUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(max_length=1000)
    notes: str | None = Field(default=None, max_length=1000)


class RuleVerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_url: str = Field(min_length=12, max_length=500)
    applies_to: str = Field(min_length=3, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=1000)
    confirm: Literal[True] = Field(description="Confirms the value was checked against this source")

    @field_validator("source_url")
    @classmethod
    def _https(cls, v: str) -> str:
        from urllib.parse import urlparse

        u = urlparse(v.strip())
        if u.scheme != "https" or not u.netloc:
            raise ValueError("source_url must be an https URL")
        return v.strip()


class TradeCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    direction: Literal["long", "short"]
    entry: Decimal = Field(gt=0)
    stop: Decimal = Field(gt=0)
    target: Decimal | None = Field(default=None, gt=0)
    quantity: int = Field(default=1, ge=1, le=1000)
