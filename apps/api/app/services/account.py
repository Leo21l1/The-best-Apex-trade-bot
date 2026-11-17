"""Account snapshots: manual entry, CSV import and the current account state.

PROJECT APEX has no broker connection. Every balance comes from the trader (typed in or
imported from a CSV export), so every figure derived from it is labelled as user-supplied.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.marketdata.calendar import ET, trading_day
from app.marketdata.instruments import INSTRUMENTS
from app.models.account import AccountSnapshot
from app.risk.engine import AccountState, Position, RuleSet, Snapshot, account_state
from app.risk.profile import load_rules, to_ruleset

MAX_CSV_BYTES = 200_000
MAX_CSV_ROWS = 2_000
_DATE_HEADERS = ("date", "trade date", "trading date", "day", "session date")
_BALANCE_HEADERS = (
    "end of day balance",
    "eod balance",
    "closing balance",
    "balance",
    "account balance",
    "total amount",
    "net liquidating value",
)
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%Y/%m/%d")


def to_engine_snapshot(row: AccountSnapshot) -> Snapshot:
    return Snapshot(
        as_of=row.as_of,
        balance=row.balance,
        unrealized_pnl=row.unrealized_pnl,
        source=row.source,
        positions=tuple(
            Position(
                p["symbol"],
                int(p["quantity"]),
                Decimal(str(p["avg_price"])),
                Decimal(str(p["stop"])) if p.get("stop") is not None else None,
            )
            for p in row.positions
        ),
    )


async def eod_history(session: AsyncSession) -> list[tuple[date, Decimal]]:
    """One end-of-day balance per trading date (the most recently entered one wins)."""
    rows = (
        await session.scalars(
            select(AccountSnapshot)
            .where(AccountSnapshot.is_eod.is_(True))
            .order_by(AccountSnapshot.trading_date, AccountSnapshot.created_at, AccountSnapshot.id)
        )
    ).all()
    latest: dict[date, Decimal] = {}
    for r in rows:
        latest[r.trading_date] = r.balance
    return sorted(latest.items())


async def latest_snapshot(session: AsyncSession) -> AccountSnapshot | None:
    return await session.scalar(
        select(AccountSnapshot).order_by(AccountSnapshot.as_of.desc(), AccountSnapshot.id.desc()).limit(1)
    )


async def current_state(session: AsyncSession, now: datetime | None = None) -> tuple[RuleSet, AccountState]:
    now = now or datetime.now(UTC)
    rules = to_ruleset(await load_rules(session))
    snap = await latest_snapshot(session)
    state = account_state(
        rules,
        to_engine_snapshot(snap) if snap else None,
        await eod_history(session),
        now,
        INSTRUMENTS,
        stale_after=timedelta(minutes=get_settings().account_stale_minutes),
    )
    return rules, state


@dataclass
class CsvResult:
    imported: list[tuple[date, Decimal]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    date_column: str | None = None
    balance_column: str | None = None


def _parse_date(raw: str) -> date:
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    # Timestamps such as "2026-11-10 16:59:00": take the date part.
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError as exc:
        raise ValueError(f"unrecognised date {raw!r}") from exc


def _parse_money(raw: str) -> Decimal:
    s = raw.strip().replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f"unrecognised amount {raw!r}") from exc
    if not v.is_finite():
        raise ValueError(f"unrecognised amount {raw!r}")
    return -v if neg else v


def parse_balance_csv(text: str) -> CsvResult:
    """Parse a CSV of end-of-day balances.

    Needs a date column and a balance column (header names matched case-insensitively against
    common export names; see ``_DATE_HEADERS`` / ``_BALANCE_HEADERS``). Rows that fail to parse
    are reported, not guessed. A later row for the same date replaces an earlier one.
    """
    res = CsvResult()
    if len(text.encode()) > MAX_CSV_BYTES:
        res.errors.append(f"File too large (limit {MAX_CSV_BYTES // 1000} KB).")
        return res
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header = next(reader)
    except StopIteration:
        res.errors.append("File is empty.")
        return res
    norm = [h.strip().lower() for h in header]
    di = next((norm.index(h) for h in _DATE_HEADERS if h in norm), None)
    bi = next((norm.index(h) for h in _BALANCE_HEADERS if h in norm), None)
    if di is None or bi is None:
        res.errors.append(
            "Could not find a date column and a balance column. Expected headers such as "
            "'Date' and 'End of Day Balance' (or 'Balance'). Found: " + ", ".join(header[:12])
        )
        return res
    res.date_column, res.balance_column = header[di].strip(), header[bi].strip()
    by_date: dict[date, Decimal] = {}
    for n, row in enumerate(reader, start=2):
        if not any(c.strip() for c in row):
            continue
        if n - 1 > MAX_CSV_ROWS:
            res.errors.append(f"Stopped after {MAX_CSV_ROWS} rows.")
            break
        try:
            if max(di, bi) >= len(row):
                raise ValueError("missing columns")
            d, bal = _parse_date(row[di]), _parse_money(row[bi])
            if bal <= 0:
                raise ValueError(f"balance must be positive, got {bal}")
            by_date[d] = bal
        except ValueError as exc:
            res.errors.append(f"Row {n}: {exc}")
    res.imported = sorted(by_date.items())
    return res


def eod_timestamp(d: date) -> datetime:
    """The close (16:59 ET) of trading date ``d`` in UTC, used as the as-of time of an EOD balance."""
    return datetime.combine(d, time(16, 59), tzinfo=ET).astimezone(UTC)


async def import_balances(session: AsyncSession, rows: list[tuple[date, Decimal]], user_id: uuid.UUID | None) -> int:
    for d, bal in rows:
        session.add(
            AccountSnapshot(
                as_of=eod_timestamp(d),
                source="csv",
                balance=bal,
                unrealized_pnl=Decimal("0"),
                is_eod=True,
                trading_date=d,
                positions=[],
                note="CSV import",
                created_by=user_id,
            )
        )
    await session.flush()
    return len(rows)


def default_trading_date(as_of: datetime) -> date:
    return trading_day(as_of)
