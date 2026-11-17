"""Broker statement import (manual, read-only): Tradovate report exports -> journal, balances, positions.

Why manual: as of the policy review recorded in docs/compliance/broker-connectivity.md, Tradovate
states that prop-firm and evaluation accounts are not eligible for API access, and Apex has not
documented any permitted API/third-party data access for evaluation accounts. PROJECT APEX
therefore never logs in to Tradovate, never calls its API and never automates its web pages; the
trader exports reports and uploads them here.

Supported exports (columns matched case-insensitively, common header variants accepted):

* ``performance`` - completed round trips (handled by services.journal.parse_trades_csv)
* ``generic``     - one row per completed trade (same parser)
* ``fills``       - executed fills/orders; paired FIFO per account and contract into round trips
* ``cash``        - cash history: realised P&L, commissions, fees, deposits and running balance
* ``positions``   - open positions with average price and open P&L
* ``balances``    - end-of-day balances (handled by services.account.parse_balance_csv)

Every parser reports rows it could not read instead of guessing.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.marketdata.calendar import trading_day
from app.marketdata.instruments import INSTRUMENTS
from app.services.journal import GENERIC_COLS, MAX_ROWS, TRADOVATE_COLS, ParsedTrade, _money, _ts, root_symbol

ALIASES: dict[str, tuple[str, ...]] = {
    "side": ("b/s", "side", "buy/sell", "action", "buysell"),
    "contract": ("contract", "symbol", "product", "instrument"),
    "qty": ("filled qty", "filledqty", "fill qty", "qty", "quantity", "filled"),
    "price": ("avg fill price", "avgfillprice", "avg price", "avgprice", "fill price", "price", "decimalfillavg"),
    "time": ("fill time", "filltime", "timestamp", "time", "date/time", "datetime", "date"),
    "id": ("fill id", "fillid", "execution id", "order id", "orderid", "id"),
    "status": ("status", "order status"),
    "commission": ("commission", "commissions", "fees", "fee"),
    "account": ("account", "account name", "accountid", "account id"),
    "cash_type": ("cash change type", "type", "description", "transaction type"),
    "amount": ("delta", "amount", "cash change", "change", "net amount"),
    "balance": ("total amount", "balance", "end balance", "account balance", "cash balance", "total"),
    "net_pos": ("net pos", "netpos", "net position", "position", "pos"),
    "avg_price": ("net price", "avg price", "avgprice", "average price", "avg. price"),
    "open_pnl": ("open p&l", "open p/l", "openpl", "unrealized p&l", "unrealized pnl", "p&l", "pnl"),
}


def _find(cols: dict[str, str], key: str) -> str | None:
    for a in ALIASES[key]:
        if a in cols:
            return cols[a]
    return None


def detect_layout(header: list[str]) -> str | None:
    cols = {h.strip().lower(): h for h in header}
    names = set(cols)
    if TRADOVATE_COLS <= names:
        return "performance"
    if GENERIC_COLS <= names:
        return "generic"
    has = {k: _find(cols, k) is not None for k in ALIASES}
    if has["side"] and has["contract"] and has["qty"] and has["price"] and has["time"]:
        return "fills"
    if has["contract"] and has["net_pos"] and has["avg_price"]:
        return "positions"
    if has["amount"] and has["cash_type"] and ("date" in names or "timestamp" in names or "time" in names):
        return "cash"
    if any(h in names for h in ("end of day balance", "eod balance", "balance", "total amount")) and (
        "date" in names or "trade date" in names
    ):
        return "balances"
    return None


def _reader(text: str) -> tuple[csv.DictReader, dict[str, str]] | None:
    r = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not r.fieldnames:
        return None
    return r, {c.strip().lower(): c for c in r.fieldnames}


# ------------------------------------------------------------------------------------- fills


@dataclass
class Fill:
    fid: str
    account: str
    contract: str
    symbol: str
    qty: int  # signed: + buy, - sell
    price: float
    ts: datetime
    commission: float | None  # total for the fill, None when the export has no commission column


@dataclass
class FillsResult:
    trades: list[ParsedTrade] = field(default_factory=list)
    open_lots: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fills: int = 0
    skipped_unfilled: int = 0
    fees_estimated: bool = False


def parse_fills(text: str, tz_name: str, fee_per_side: dict[str, float]) -> FillsResult:
    """Read executed fills and pair them FIFO into closed round trips (per account and contract)."""
    from zoneinfo import ZoneInfo

    res = FillsResult()
    rd = _reader(text)
    if rd is None:
        res.errors.append("File is empty.")
        return res
    reader, cols = rd
    c = {k: _find(cols, k) for k in ALIASES}
    tz = ZoneInfo(tz_name)
    fills: list[Fill] = []
    for n, row in enumerate(reader, start=2):
        if n - 1 > MAX_ROWS:
            res.errors.append(f"Stopped after {MAX_ROWS} rows.")
            break
        if not any((v or "").strip() for v in row.values()):
            continue

        def get(k: str, row: dict[str, str] = row) -> str:
            return (row.get(c[k]) or "").strip() if c[k] else ""

        status = get("status").lower()
        if status and "fill" not in status:
            res.skipped_unfilled += 1  # working, cancelled or rejected orders are not executions
            continue
        try:
            contract = get("contract")
            sym = root_symbol(contract)
            if sym is None:
                raise ValueError(f"unsupported contract {contract!r}")
            side = get("side").lower()
            if side not in ("buy", "sell", "b", "s", "bot", "sld", "bought", "sold"):
                raise ValueError(f"unrecognised side {get('side')!r}")
            q = int(float(get("qty") or "0"))
            if q <= 0:
                if status:
                    res.skipped_unfilled += 1
                    continue
                raise ValueError("quantity must be positive")
            price = _money(get("price"))
            if price <= 0:
                raise ValueError("price must be positive")
            comm = abs(_money(get("commission"))) if get("commission") else None
            sign = 1 if side in ("buy", "b", "bot", "bought") else -1
            fid = get("id") or f"r{n}"
            fills.append(
                Fill(
                    fid, get("account") or "default", contract.upper(), sym, sign * q, price, _ts(get("time"), tz), comm
                )
            )
        except (ValueError, KeyError) as exc:
            res.errors.append(f"Row {n}: {exc}")
    res.fills = len(fills)
    fills.sort(key=lambda f: (f.ts, f.fid))
    # lots: (remaining qty signed, price, time, fill id, commission per contract or None)
    books: dict[tuple[str, str], deque[list[Any]]] = defaultdict(deque)
    seen_keys: dict[str, int] = defaultdict(int)
    for f in fills:
        inst = INSTRUMENTS[f.symbol]
        pv = float(inst.point_value)
        est = fee_per_side["micro" if inst.is_micro else "standard"]
        per_c = f.commission / abs(f.qty) if f.commission is not None else None
        book = books[(f.account, f.contract)]
        remaining = f.qty
        while remaining and book and (book[0][0] > 0) != (remaining > 0):
            lot = book[0]
            q = min(abs(lot[0]), abs(remaining))
            long = lot[0] > 0
            s = 1 if long else -1
            entry_p, entry_t, entry_id, lot_c = lot[1], lot[2], lot[3], lot[4]
            gross = round((f.price - entry_p) * s * pv * q, 2)
            if lot_c is None or per_c is None:
                res.fees_estimated = True
            fees = q * ((lot_c if lot_c is not None else est) + (per_c if per_c is not None else est))
            base = f"tvfill:{f.account}:{entry_id}:{f.fid}:{q}"
            seen_keys[base] += 1
            key = base if seen_keys[base] == 1 else f"{base}#{seen_keys[base]}"
            res.trades.append(
                ParsedTrade(
                    key[:200],
                    f.symbol,
                    f.contract,
                    "long" if long else "short",
                    q,
                    entry_t,
                    f.ts,
                    entry_p,
                    f.price,
                    gross,
                    round(fees, 2),
                )
            )
            lot[0] -= q * s
            remaining += q * s
            if lot[0] == 0:
                book.popleft()
        if remaining:
            book.append([remaining, f.price, f.ts, f.fid, per_c])
    for (acct, contract), book in books.items():
        for lot in book:
            res.open_lots.append(
                {
                    "account": acct,
                    "contract": contract,
                    "symbol": root_symbol(contract),
                    "quantity": lot[0],
                    "avg_price": lot[1],
                    "opened_at": lot[2].isoformat(),
                }
            )
    return res


# -------------------------------------------------------------------------------------- cash


CASH_KIND = (
    ("commission", ("commission",)),
    ("fee", ("fee", "clearing", "exchange", "nfa", "routing", "platform", "data")),
    ("realized", ("trade paired", "paired", "realized", "realised", "p&l", "pnl", "trade")),
    ("transfer", ("deposit", "withdraw", "transfer", "adjust", "reset", "payout")),
)


def _cash_kind(label: str) -> str:
    low = label.lower()
    for kind, words in CASH_KIND:
        if any(w in low for w in words):
            return kind
    return "other"


@dataclass
class CashResult:
    days: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rows: int = 0
    has_balance: bool = False


def parse_cash(text: str, tz_name: str) -> CashResult:
    """Daily realised P&L, commissions, fees, transfers and end-of-day balance from a cash history."""
    from zoneinfo import ZoneInfo

    res = CashResult()
    rd = _reader(text)
    if rd is None:
        res.errors.append("File is empty.")
        return res
    reader, cols = rd
    tz = ZoneInfo(tz_name)
    date_col = cols.get("date") or cols.get("timestamp") or cols.get("time")
    type_col, amt_col, bal_col = _find(cols, "cash_type"), _find(cols, "amount"), _find(cols, "balance")
    res.has_balance = bal_col is not None
    days: dict[date, dict[str, Any]] = {}
    for n, row in enumerate(reader, start=2):
        if n - 1 > MAX_ROWS:
            res.errors.append(f"Stopped after {MAX_ROWS} rows.")
            break
        if not any((v or "").strip() for v in row.values()):
            continue
        try:
            raw_d = (row.get(date_col) or "").strip() if date_col else ""
            if re.search(r"\d{1,2}:\d{2}", raw_d):
                d = trading_day(_ts(raw_d, tz))  # with a time: the futures trading day (rolls 18:00 ET)
            else:
                d = _ts(raw_d + " 12:00:00" if "/" in raw_d else raw_d + "T12:00:00", tz).astimezone(tz).date()
            amount = _money((row.get(amt_col) or "0") if amt_col else "0")
            kind = _cash_kind((row.get(type_col) or "") if type_col else "")
            day = days.setdefault(
                d,
                {
                    "date": d.isoformat(),
                    "realized": 0.0,
                    "commission": 0.0,
                    "fee": 0.0,
                    "transfer": 0.0,
                    "other": 0.0,
                    "end_balance": None,
                    "rows": 0,
                },
            )
            day[kind] = round(day[kind] + amount, 2)
            day["rows"] += 1
            if bal_col and (row.get(bal_col) or "").strip():
                day["end_balance"] = round(_money(row[bal_col]), 2)  # last row of the day wins
            res.rows += 1
        except (ValueError, KeyError) as exc:
            res.errors.append(f"Row {n}: {exc}")
    for d in sorted(days):
        day = days[d]
        day["net"] = round(day["realized"] + day["commission"] + day["fee"], 2)
        res.days.append(day)
    return res


# ----------------------------------------------------------------------------------- positions


@dataclass
class PositionsResult:
    positions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    open_pnl: float | None = None


def parse_positions(text: str) -> PositionsResult:
    res = PositionsResult()
    rd = _reader(text)
    if rd is None:
        res.errors.append("File is empty.")
        return res
    reader, cols = rd
    cc, nc, ac, pc = _find(cols, "contract"), _find(cols, "net_pos"), _find(cols, "avg_price"), _find(cols, "open_pnl")
    total = 0.0
    have_pnl = False
    for n, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue
        try:
            contract = (row.get(cc) or "").strip()
            sym = root_symbol(contract)
            if sym is None:
                raise ValueError(f"unsupported contract {contract!r}")
            qty = int(float((row.get(nc) or "0").strip()))
            if qty == 0:
                continue  # flat line
            avg = _money(row.get(ac) or "")
            pnl = _money(row[pc]) if pc and (row.get(pc) or "").strip() else None
            if pnl is not None:
                have_pnl = True
                total += pnl
            res.positions.append(
                {"symbol": sym, "contract": contract.upper(), "quantity": qty, "avg_price": avg, "open_pnl": pnl}
            )
        except (ValueError, KeyError) as exc:
            res.errors.append(f"Row {n}: {exc}")
    res.open_pnl = round(total, 2) if have_pnl else None
    return res


# ------------------------------------------------------------------------------- dedupe & match


def same_trade(a: ParsedTrade, b: Any, tick: float) -> bool:
    """True when two records describe the same execution (cross-layout duplicate check).

    Same root symbol, direction and quantity; entry and exit within 2 seconds; prices within half a
    tick. Used so that a Performance export and a Fills export of the same day do not double count.
    """
    return (
        a.symbol == b.symbol
        and a.direction == b.direction
        and a.quantity == b.quantity
        and abs((a.entry_time - b.entry_time).total_seconds()) <= 2
        and abs((a.exit_time - b.exit_time).total_seconds()) <= 2
        and abs(a.entry_price - b.entry_price) <= tick / 2
        and abs(a.exit_price - b.exit_price) <= tick / 2
    )
