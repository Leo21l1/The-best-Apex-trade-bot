"""Trading journal: trade import, reports, behaviour analysis and rule-adherence checks.

Analysis is descriptive and statistical. A "recurring mistake" is reported with its sample size
and a Welch t-test against trades without that tag; with few trades the result says so instead of
drawing a conclusion. Nothing here changes strategy parameters.
"""

from __future__ import annotations

import csv
import io
import math
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from statistics import NormalDist
from typing import Any

from app.marketdata.calendar import ET, trading_day
from app.marketdata.instruments import INSTRUMENTS
from app.models.research import JournalTrade

MAX_ROWS = 5000
MONTH_CODES = "FGHJKMNQUVXZ"
# Tradovate "Performance" report columns (commonly exported); matched case-insensitively.
TRADOVATE_COLS = {"symbol", "qty", "buyprice", "sellprice", "pnl", "boughttimestamp", "soldtimestamp"}
GENERIC_COLS = {"symbol", "side", "qty", "entry_time", "exit_time", "entry_price", "exit_price"}
MISTAKE_TAGS = (
    "chased_entry",
    "moved_stop",
    "no_stop",
    "early_exit",
    "oversized",
    "revenge_trade",
    "traded_news",
    "outside_plan",
    "late_exit",
    "fomo",
)


def root_symbol(contract: str) -> str | None:
    """Map a contract code such as MESZ6 / MESZ26 / ES to its root symbol (MES / ES)."""
    c = contract.strip().upper()
    if c in INSTRUMENTS:
        return c
    m = re.fullmatch(r"([A-Z0-9]+?)([" + MONTH_CODES + r"])(\d{1,2})", c)
    if m and m.group(1) in INSTRUMENTS:
        return m.group(1)
    return None


def _money(raw: str) -> float:
    s = raw.strip().replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    v = float(s)
    if not math.isfinite(v):
        raise ValueError("not a finite number")
    return -v if neg else v


_TS_FORMATS = ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M")


def _ts(raw: str, tz: Any) -> datetime:
    s = raw.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        dt = None
        for fmt in _TS_FORMATS:
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            raise ValueError(f"unrecognised timestamp {raw!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(UTC)


@dataclass
class ParsedTrade:
    source_key: str
    symbol: str
    contract: str
    direction: str
    quantity: int
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    gross_pnl: float
    fees: float


@dataclass
class ImportResult:
    layout: str | None = None
    trades: list[ParsedTrade] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_trades_csv(
    text: str, tz_name: str = "America/New_York", fee_per_side: dict[str, float] | None = None
) -> ImportResult:
    """Parse completed trades from a Tradovate Performance export or the generic layout.

    Timestamps without a zone are read in ``tz_name`` (Tradovate exports local time). When the file
    has no fee column, fees are estimated from ``fee_per_side`` (standard/micro) and labelled.
    """
    from zoneinfo import ZoneInfo

    res = ImportResult()
    tz = ZoneInfo(tz_name)
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        res.errors.append("File is empty.")
        return res
    cols = {c.strip().lower(): c for c in reader.fieldnames}
    if TRADOVATE_COLS <= set(cols):
        res.layout = "tradovate_performance"
    elif GENERIC_COLS <= set(cols):
        res.layout = "generic"
    else:
        res.errors.append(
            "Unrecognised columns. Expected a Tradovate Performance export (symbol, qty, buyPrice, sellPrice, pnl, "
            "boughtTimestamp, soldTimestamp) or: symbol, side, qty, entry_time, exit_time, entry_price, exit_price "
            "[, fees, pnl]. Found: " + ", ".join(reader.fieldnames[:15])
        )
        return res
    fee_per_side = fee_per_side or {"standard": 2.50, "micro": 0.60}

    def get(row: dict[str, str], key: str) -> str:
        return (row.get(cols[key]) or "").strip() if key in cols else ""

    for n, row in enumerate(reader, start=2):
        if n - 1 > MAX_ROWS:
            res.errors.append(f"Stopped after {MAX_ROWS} rows.")
            break
        if not any((v or "").strip() for v in row.values()):
            continue
        try:
            contract = get(row, "symbol")
            sym = root_symbol(contract)
            if sym is None:
                raise ValueError(f"unsupported symbol {contract!r}")
            inst = INSTRUMENTS[sym]
            qty = int(float(get(row, "qty")))
            if qty <= 0:
                raise ValueError("qty must be positive")
            if res.layout == "tradovate_performance":
                buy_t, sell_t = _ts(get(row, "boughttimestamp"), tz), _ts(get(row, "soldtimestamp"), tz)
                buy_p, sell_p = _money(get(row, "buyprice")), _money(get(row, "sellprice"))
                direction = "long" if buy_t <= sell_t else "short"
                entry_t, exit_t = (buy_t, sell_t) if direction == "long" else (sell_t, buy_t)
                entry_p, exit_p = (buy_p, sell_p) if direction == "long" else (sell_p, buy_p)
                gross = _money(get(row, "pnl"))
                key = "tv:" + "|".join(get(row, k) for k in ("buyfillid", "sellfillid") if k in cols) or ""
                if key == "tv:":
                    key = f"tv:{contract}|{entry_t.isoformat()}|{exit_t.isoformat()}|{qty}|{entry_p}|{exit_p}"
            else:
                side = get(row, "side").lower()
                if side not in ("long", "short", "buy", "sell"):
                    raise ValueError("side must be long/short (or buy/sell for the opening side)")
                direction = "long" if side in ("long", "buy") else "short"
                entry_t, exit_t = _ts(get(row, "entry_time"), tz), _ts(get(row, "exit_time"), tz)
                entry_p, exit_p = _money(get(row, "entry_price")), _money(get(row, "exit_price"))
                s = 1 if direction == "long" else -1
                gross = (
                    _money(get(row, "pnl"))
                    if get(row, "pnl")
                    else (exit_p - entry_p) * s * float(inst.point_value) * qty
                )
                key = f"gen:{contract}|{entry_t.isoformat()}|{exit_t.isoformat()}|{qty}|{entry_p}|{exit_p}"
            if exit_t < entry_t:
                raise ValueError("exit is before entry")
            for p in (entry_p, exit_p):
                if p <= 0:
                    raise ValueError("prices must be positive")
            if get(row, "fees") or get(row, "commission"):
                fees = abs(_money(get(row, "fees") or get(row, "commission")))
            else:
                fees = 2 * qty * fee_per_side["micro" if inst.is_micro else "standard"]
            res.trades.append(
                ParsedTrade(
                    key[:200],
                    sym,
                    contract,
                    direction,
                    qty,
                    entry_t,
                    exit_t,
                    entry_p,
                    exit_p,
                    round(gross, 2),
                    round(fees, 2),
                )
            )
        except (ValueError, KeyError) as exc:
            res.errors.append(f"Row {n}: {exc}")
    return res


def to_row(t: ParsedTrade, source: str) -> JournalTrade:
    return JournalTrade(
        source_key=t.source_key,
        source=source,
        trading_date=trading_day(t.exit_time),
        symbol=t.symbol,
        contract=t.contract,
        direction=t.direction,
        quantity=t.quantity,
        entry_time=t.entry_time,
        exit_time=t.exit_time,
        entry_price=t.entry_price,
        exit_price=t.exit_price,
        gross_pnl=t.gross_pnl,
        fees=t.fees,
        net_pnl=round(t.gross_pnl - t.fees, 2),
        mistakes=[],
    )


# --- reports ---------------------------------------------------------------------------------


def stats(trades: Sequence[JournalTrade]) -> dict[str, Any]:
    n = len(trades)
    pnl = [t.net_pnl for t in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    eq = pk = dd = 0.0
    for t in sorted(trades, key=lambda t: t.exit_time):
        eq += t.net_pnl
        pk = max(pk, eq)
        dd = max(dd, pk - eq)
    gl = -sum(losses)
    return {
        "trades": n,
        "net_pnl": round(sum(pnl), 2),
        "fees": round(sum(t.fees for t in trades), 2),
        "win_rate": round(len(wins) / n, 4) if n else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "profit_factor": round(sum(wins) / gl, 3) if gl > 0 else None,
        "expectancy": round(sum(pnl) / n, 2) if n else None,
        "max_drawdown": round(dd, 2),
        "contracts": sum(t.quantity for t in trades),
    }


def _period_key(d: date, period: str) -> str:
    if period == "day":
        return d.isoformat()
    if period == "week":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    return f"{d.year}-{d.month:02d}"


def grouped(trades: Sequence[JournalTrade], key: Any) -> list[dict[str, Any]]:
    groups: dict[str, list[JournalTrade]] = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return [{"key": k, **stats(v)} for k, v in sorted(groups.items())]


def welch(a: Sequence[float], b: Sequence[float]) -> dict[str, Any]:
    """Welch's t-test (normal approximation for the p-value; flagged when samples are small)."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"t": None, "p": None, "note": "not enough trades in both groups to test"}
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return {"t": None, "p": None, "note": "no variance"}
    t = (ma - mb) / se
    p = 2 * (1 - NormalDist().cdf(abs(t)))
    small = min(na, nb) < 30
    return {
        "t": round(t, 3),
        "p": round(p, 4),
        "note": "small sample: treat as a lead, not a conclusion" if small else None,
    }


def mistake_analysis(trades: Sequence[JournalTrade]) -> list[dict[str, Any]]:
    out = []
    tags = sorted({m for t in trades for m in (t.mistakes or [])})
    for tag in tags:
        with_tag = [t.net_pnl for t in trades if tag in (t.mistakes or [])]
        without = [t.net_pnl for t in trades if tag not in (t.mistakes or [])]
        test = welch(with_tag, without)
        out.append(
            {
                "tag": tag,
                "count": len(with_tag),
                "share": round(len(with_tag) / len(trades), 3) if trades else 0,
                "total_pnl": round(sum(with_tag), 2),
                "avg_pnl_with": round(sum(with_tag) / len(with_tag), 2) if with_tag else None,
                "avg_pnl_without": round(sum(without) / len(without), 2) if without else None,
                "welch": test,
                "evidence": (
                    "statistically significant at 5%"
                    if test["p"] is not None and test["p"] < 0.05
                    else "not significant"
                    if test["p"] is not None
                    else test["note"]
                ),
            }
        )
    return sorted(out, key=lambda r: r["total_pnl"])


def rule_violations(
    trades: Sequence[JournalTrade],
    max_contracts: float,
    daily_loss_limit: float,
    events: Sequence[Any] = (),
    pre: int = 15,
    post: int = 15,
) -> list[dict[str, Any]]:
    """Checks against the CONFIGURED rule values (which may be unverified)."""
    out: list[dict[str, Any]] = []
    for t in trades:
        eq = t.quantity * float(INSTRUMENTS[t.symbol].standard_equivalent)
        if eq > max_contracts:
            out.append(
                {
                    "trade_id": t.id,
                    "date": t.trading_date.isoformat(),
                    "rule": "contract limit",
                    "detail": f"{t.quantity} {t.symbol} = {eq:g} standard contracts > {max_contracts:g}",
                }
            )
        if t.exit_time.astimezone(ET).time() >= time(16, 59) and t.exit_time.astimezone(ET).time() < time(18, 0):
            out.append(
                {
                    "trade_id": t.id,
                    "date": t.trading_date.isoformat(),
                    "rule": "held into session close",
                    "detail": f"exited {t.exit_time.astimezone(ET):%H:%M} ET",
                }
            )
        for e in events:
            if (
                t.symbol in e.affected
                and e.importance == "high"
                and (t.entry_time <= e.ts + timedelta(minutes=post) and t.exit_time >= e.ts - timedelta(minutes=pre))
            ):
                out.append(
                    {
                        "trade_id": t.id,
                        "date": t.trading_date.isoformat(),
                        "rule": "open during news window",
                        "detail": f"{e.title} at {e.ts.astimezone(ET):%H:%M} ET (Apex news rules unverified)",
                    }
                )
    daily: dict[date, float] = defaultdict(float)
    for t in sorted(trades, key=lambda t: t.exit_time):
        daily[t.trading_date] += t.net_pnl
        if daily[t.trading_date] <= -daily_loss_limit:
            out.append(
                {
                    "trade_id": t.id,
                    "date": t.trading_date.isoformat(),
                    "rule": "daily loss limit",
                    "detail": f"realised day P&L {daily[t.trading_date]:.2f} <= -{daily_loss_limit:g} "
                    "(unrealised drawdown during the day is not visible in exports)",
                }
            )
            daily[t.trading_date] = -math.inf  # report once per day
    return out


def planned_vs_actual(trades: Sequence[JournalTrade]) -> dict[str, Any]:
    """How exits compared with the planned stop/target, for trades where a plan was recorded."""
    planned = [t for t in trades if t.planned_stop is not None]
    rows = []
    for t in planned:
        s = 1 if t.direction == "long" else -1
        risk = abs(t.entry_price - t.planned_stop)
        r = (t.exit_price - t.entry_price) * s / risk if risk else None
        beyond = (t.exit_price - t.planned_stop) * s < 0
        rows.append(
            {
                "trade_id": t.id,
                "r_multiple": round(r, 2) if r is not None else None,
                "exit_beyond_stop": beyond,
                "reached_target": t.planned_target is not None and (t.exit_price - t.planned_target) * s >= 0,
            }
        )
    rs = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
    return {
        "trades_with_plan": len(planned),
        "trades_without_plan": len(trades) - len(planned),
        "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
        "exits_beyond_stop": sum(1 for r in rows if r["exit_beyond_stop"]),
        "reached_target": sum(1 for r in rows if r["reached_target"]),
        "followed_plan_rate": round(
            sum(1 for t in trades if t.followed_plan) / sum(1 for t in trades if t.followed_plan is not None), 3
        )
        if any(t.followed_plan is not None for t in trades)
        else None,
    }


def report(trades: Sequence[JournalTrade], period: str) -> list[dict[str, Any]]:
    return grouped(trades, lambda t: _period_key(t.trading_date, period))
