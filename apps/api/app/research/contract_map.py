"""Actual contract maps (Databento symbology) for rollover-safe research: document, validation, checks.

Document written by ``python -m app.cli contract-map`` and read by ``research-study --contract-map``::

    {
      "schema": "apex.contract_map", "version": 1,
      "source": "Databento symbology.resolve: continuous -> instrument_id -> raw_symbol",
      "dataset": "GLBX.MDP3", "generated_at": "...",
      "symbols": {
        "ES": {"continuous": "ES.v.0", "start": "YYYY-MM-DD", "end": "YYYY-MM-DD (exclusive)",
               "map": {"YYYY-MM-DD": "ESZ6", ...},            # UTC date -> raw symbol
               "instrument_ids": {"YYYY-MM-DD": "1234", ...},
               "switches": [{"date": ..., "from": ..., "to": ...}], "unresolved": [...]}
      }
    }

Date convention: the key is the UTC calendar date. A CME trade date D runs from 18:00 ET on D-1 to
17:00 ET on D, so most of it lies on UTC date D; trade date D uses ``map[D]``. Around a switch the
evening hours may still carry the old contract, which is why roll windows are excluded.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

from app.research.bars import Bar
from app.research.rollover import compare_to_calendar, detect_discontinuities, switch_dates

SCHEMA, VERSION = "apex.contract_map", 1
SOURCE = "Databento symbology.resolve: continuous -> instrument_id -> raw_symbol"
MONTHS = "FGHJKMNQUVXZ"
QUARTERLY_MONTHS = "HMUZ"
RAW = re.compile(r"^(?P<root>[A-Z0-9]+?)(?P<month>[FGHJKMNQUVXZ])(?P<year>\d{1,2})$")


class ContractMapError(ValueError):
    """The contract map is missing, malformed or does not cover the data: the study must not start."""


def build_document(dataset: str, generated_at: str, resolved: dict[str, dict[str, Any]]) -> dict[str, Any]:
    symbols = {}
    for sym, r in resolved.items():
        m = dict(sorted(r["map"].items()))
        symbols[sym] = {
            **{k: r[k] for k in ("continuous", "start", "end")},
            "map": m,
            "instrument_ids": dict(sorted(r.get("instrument_ids", {}).items())),
            "switches": [{"date": d.isoformat(), "from": a, "to": b} for d, a, b in switch_dates(m)],
            "unresolved": list(r.get("unresolved", [])),
        }
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "source": SOURCE,
        "dataset": dataset,
        "generated_at": generated_at,
        "symbols": symbols,
    }


def validate_document(doc: Any, symbols: Iterable[str]) -> dict[str, dict[str, str]]:
    """Structural validation. Returns {symbol: {date: raw symbol}} or raises ContractMapError."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise ContractMapError(
            "not an APEX contract map (expected schema 'apex.contract_map'); regenerate it with "
            "`python -m app.cli contract-map`"
        )
    if doc.get("version") != VERSION:
        raise ContractMapError(f"unsupported contract map version {doc.get('version')!r} (expected {VERSION})")
    out: dict[str, dict[str, str]] = {}
    for sym in symbols:
        entry = (doc.get("symbols") or {}).get(sym)
        if not isinstance(entry, dict) or not isinstance(entry.get("map"), dict) or not entry["map"]:
            raise ContractMapError(f"the contract map has no mapping for {sym}; regenerate it including {sym}")
        bad: list[str] = []
        for d, raw in entry["map"].items():
            try:
                date.fromisoformat(d)
            except (TypeError, ValueError):
                bad.append(f"bad date {d!r}")
                continue
            mm = RAW.match(str(raw))
            if not mm or mm.group("root") != sym:
                bad.append(f"{d}: {raw!r} is not a {sym} contract")
        if bad:
            raise ContractMapError(f"{sym} contract map is invalid: " + "; ".join(bad[:5]))
        out[sym] = {d: str(v) for d, v in entry["map"].items()}
    return out


def require_coverage(symbol: str, mapping: dict[str, str], sessions: Sequence[str]) -> None:
    """Every usable session must have an actual contract; otherwise the study refuses to start."""
    missing = [d for d in sessions if d not in mapping]
    if missing:
        raise ContractMapError(
            f"{symbol}: {len(missing)} usable sessions have no actual contract mapping "
            f"(first: {', '.join(missing[:5])}). "
            "Regenerate the map with a range covering them (contract-map --days N); dates are never filled from the "
            "calendar."
        )


def verify(symbol: str, mapping: dict[str, str], bars: Sequence[Bar], trade_dates: Iterable[str]) -> dict[str, Any]:
    """Checks against the stored history (no downloads)."""
    problems: list[str] = []
    seen: list[str] = []
    for d in sorted(mapping):
        if not seen or seen[-1] != mapping[d]:
            if mapping[d] in seen:
                problems.append(f"{d}: mapping returns to {mapping[d]} after a later contract (inconsistent)")
            seen.append(mapping[d])
    for d, raw in sorted(mapping.items()):
        mm = RAW.match(raw)
        if (
            mm
            and symbol in ("ES", "NQ", "YM", "RTY", "MES", "MNQ", "MYM", "M2K")
            and mm.group("month") not in QUARTERLY_MONTHS
        ):
            problems.append(f"{d}: {raw} is not a quarterly contract")
            break
    td = sorted(set(trade_dates))
    unmapped = [d for d in td if d not in mapping]
    sw_days = [d for d, _, _ in switch_dates(mapping)]
    disc = detect_discontinuities(symbol, bars)

    def near_switch(ds: str) -> bool:  # UTC-date map vs trade date: allow one day either side
        x = date.fromisoformat(ds)
        return any(abs((x - d).days) <= 1 for d in sw_days)

    return {
        "symbol": symbol,
        "contracts": seen,
        "switches_vs_calendar": compare_to_calendar(symbol, mapping),
        "stored_trade_dates": len(td),
        "stored_trade_dates_unmapped": unmapped,
        "discontinuities": [{**x, "at_actual_switch": near_switch(x["date"])} for x in disc],
        "unexplained_discontinuities": [x for x in disc if not near_switch(x["date"])],
        "problems": problems,
        "ok": not problems,
    }
