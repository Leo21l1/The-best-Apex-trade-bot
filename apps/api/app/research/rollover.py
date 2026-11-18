"""Rollover-safe research data: contract inference, roll exclusion windows and per-contract segments.

Policy (documented in docs/research-methodology.md and tested):

* Continuous front-month prices (Databento ``ROOT.v.0``, roll by previous-day volume) are NOT
  back-adjusted: at each roll the series jumps from one contract's price to the next.
* Equity-index futures (ES, NQ and micros) roll quarterly (Mar/Jun/Sep/Dec). The conventional roll
  date is the Thursday 8 days before the third-Friday expiry; the volume-based switch can happen a
  day either side of it.
* Research therefore EXCLUDES the trade dates from one session before to two sessions after each
  roll date (the ambiguity window), and splits the remaining history into per-contract SEGMENTS.
  Strategies are evaluated on each segment separately, so every indicator, previous-day level and
  higher-timeframe bar is computed from one contract only, and no trade can span two contracts
  (positions are also flat by 15:55 ET).
* Fills and P&L keep the real (unadjusted) prices of the contract being traded.
* The contract label is INFERRED from the roll calendar; the data itself does not carry the
  contract id. ``detect_discontinuities`` checks the data independently: an unexplained jump
  outside a roll window is reported as a data-quality warning.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import date, timedelta
from statistics import median
from typing import Any

from app.marketdata.calendar import trading_day
from app.research.bars import Bar
from app.services.session_intel import session_bounds

QUARTERLY_ROOTS = {"ES", "NQ", "YM", "RTY", "MES", "MNQ", "MYM", "M2K"}
MONTH_CODE = {3: "H", 6: "M", 9: "U", 12: "Z"}
PRE_SESSIONS, POST_SESSIONS = 1, 2


def third_friday(y: int, m: int) -> date:
    first = date(y, m, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def roll_date(y: int, m: int) -> date:
    """Conventional equity-index roll date: the Thursday 8 days before the third-Friday expiry."""
    return third_friday(y, m) - timedelta(days=8)


def is_quarterly(symbol: str) -> bool:
    return symbol in QUARTERLY_ROOTS


def roll_dates(symbol: str, start: date, end: date) -> list[date]:
    if not is_quarterly(symbol):
        return []
    out = []
    for y in range(start.year - 1, end.year + 2):
        for m in (3, 6, 9, 12):
            r = roll_date(y, m)
            if start - timedelta(days=10) <= r <= end + timedelta(days=10):
                out.append(r)
    return sorted(out)


def front_contract(symbol: str, d: date) -> str:
    """Inferred front-month contract for trade date ``d`` (e.g. ESZ6): the first quarterly contract
    whose roll date is after ``d``."""
    if not is_quarterly(symbol):
        return f"{symbol} (continuous; contract not inferred)"
    for y in (d.year, d.year + 1):
        for m in (3, 6, 9, 12):
            if roll_date(y, m) > d:
                return f"{symbol}{MONTH_CODE[m]}{y % 10}"
    raise AssertionError("unreachable")


def _sessions_around(symbol: str, r: date, before: int, after: int) -> set[date]:
    out: set[date] = set()
    d, n = r, 0
    while n < before:  # sessions strictly before r
        d -= timedelta(days=1)
        if session_bounds(symbol, d):
            out.add(d)
            n += 1
    d, n = r, 0
    if session_bounds(symbol, r):
        out.add(r)
    while n < after:
        d += timedelta(days=1)
        if session_bounds(symbol, d):
            out.add(d)
            n += 1
    return out


def excluded_dates(symbol: str, start: date, end: date) -> dict[date, str]:
    """Trade dates excluded from research, with the reason."""
    out: dict[date, str] = {}
    for r in roll_dates(symbol, start, end):
        for d in _sessions_around(symbol, r, PRE_SESSIONS, POST_SESSIONS):
            out[d] = (
                f"roll window around {r.isoformat()} ({front_contract(symbol, r - timedelta(days=1))} -> "
                f"{front_contract(symbol, r)})"
            )
    return out


def switch_dates(contract_map: dict[str, str]) -> list[tuple[date, str, str]]:
    """(first trade date of the new contract, old, new) from an actual date -> contract mapping."""
    days = sorted(contract_map)
    out = []
    for a, b in itertools.pairwise(days):
        if contract_map[a] != contract_map[b]:
            out.append((date.fromisoformat(b), contract_map[a], contract_map[b]))
    return out


def segments(
    symbol: str, bars: Sequence[Bar], contract_map: dict[str, str] | None = None
) -> tuple[list[tuple[str, list[Bar]]], dict[str, str]]:
    """Split bars into per-contract segments with the roll windows removed.

    With ``contract_map`` (trade date -> actual contract, from Databento symbology) the windows sit
    around the ACTUAL switch dates and segments use the actual contracts; otherwise both are inferred
    from the roll calendar. Returns ([(contract, bars), ...] in time order, {excluded date: reason})."""
    if not bars:
        return [], {}
    first, last = trading_day(bars[0].ts), trading_day(bars[-1].ts)
    if contract_map:
        excl: dict[date, str] = {}
        for sw, old, new in switch_dates(contract_map):
            for d in _sessions_around(symbol, sw, PRE_SESSIONS, POST_SESSIONS):
                excl[d] = f"roll window around the actual switch on {sw.isoformat()} ({old} -> {new})"

        def contract_of(d: date) -> str:
            return contract_map.get(d.isoformat()) or front_contract(symbol, d) + " (inferred)"
    else:
        excl = excluded_dates(symbol, first, last)

        def contract_of(d: date) -> str:
            return front_contract(symbol, d)

    segs: list[tuple[str, list[Bar]]] = []
    cur: list[Bar] = []
    cur_c: str | None = None
    for b in bars:
        d = trading_day(b.ts)
        if d in excl:
            if cur:
                segs.append((cur_c or "", cur))
                cur, cur_c = [], None
            continue
        c = contract_of(d)
        if cur_c is not None and c != cur_c:
            segs.append((cur_c, cur))
            cur = []
        cur_c = c
        cur.append(b)
    if cur:
        segs.append((cur_c or "", cur))
    return segs, {d.isoformat(): why for d, why in excl.items()}


def compare_to_calendar(symbol: str, contract_map: dict[str, str]) -> list[dict[str, Any]]:
    """Each actual switch vs the calendar roll date (and whether the exclusion window would cover it)."""
    out = []
    for sw, old, new in switch_dates(contract_map):
        rolls = roll_dates(symbol, sw - timedelta(days=40), sw + timedelta(days=40))
        nearest = min(rolls, key=lambda r: abs((r - sw).days)) if rolls else None
        window = _sessions_around(symbol, nearest, PRE_SESSIONS, POST_SESSIONS) if nearest else set()
        out.append(
            {
                "switch": sw.isoformat(),
                "from": old,
                "to": new,
                "calendar_roll": nearest.isoformat() if nearest else None,
                "days_from_calendar": (sw - nearest).days if nearest else None,
                "inside_calendar_window": sw in window,
                "inferred_contract_matches": front_contract(symbol, sw) == new,
            }
        )
    return out


def detect_discontinuities(symbol: str, bars: Sequence[Bar], factor: float = 8.0) -> list[dict[str, Any]]:
    """Session-open gaps much larger than usual (|open - previous session close|).

    A large gap on a roll date is the expected contract switch; elsewhere it is a data warning."""
    by_day: dict[date, list[Bar]] = {}
    for b in bars:
        by_day.setdefault(trading_day(b.ts), []).append(b)
    days = sorted(by_day)
    gaps = []
    for a, b in itertools.pairwise(days):
        gaps.append((b, by_day[b][0].open - by_day[a][-1].close))
    if len(gaps) < 5:
        return []
    typical = median(abs(g) for _, g in gaps) or 1e-9
    rolls = set(roll_dates(symbol, days[0], days[-1]))
    near = {d for r in rolls for d in _sessions_around(symbol, r, PRE_SESSIONS, POST_SESSIONS)} if rolls else set()
    out = []
    for d, g in gaps:
        if abs(g) > factor * typical:
            out.append(
                {
                    "date": d.isoformat(),
                    "gap_points": round(g, 4),
                    "typical_gap_points": round(typical, 4),
                    "explained_by_roll": d in near,
                }
            )
    return out
