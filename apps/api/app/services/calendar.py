"""Economic calendar: ICS import, classification and event-window exposure.

Events come only from sources the user supplies: official calendar files (.ics) such as the BLS
release schedule or the Federal Reserve FOMC calendar, configured official ICS URLs, or manual
entry. Classification is keyword-based and deterministic. "Importance" describes the event type's
typical relevance to index, energy and metals futures; it is not a prediction of any move, and the
app never claims an event caused a move.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.marketdata.calendar import ET
from app.models.calendar import EconomicEvent

EQUITY = ["ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K"]
ENERGY = ["CL", "MCL"]
METALS = ["GC", "MGC"]
ALL = EQUITY + ENERGY + METALS

# (category, pattern, importance, affected). First match wins; order matters.
RULES: tuple[tuple[str, str, str, list[str]], ...] = (
    (
        "fomc",
        r"\bfomc\b|federal open market|fed(eral reserve)? (rate|interest rate) decision|fomc minutes",
        "high",
        ALL,
    ),
    ("rates", r"interest rate decision|rate decision", "high", ALL),
    ("cpi", r"\bcpi\b|consumer price index", "high", ALL),
    ("employment", r"employment situation|non.?farm|\bnfp\b|payroll", "high", ALL),
    ("pce", r"\bpce\b|personal income and outlays|personal consumption expenditures", "high", ALL),
    ("gdp", r"\bgdp\b|gross domestic product", "high", ALL),
    ("ppi", r"\bppi\b|producer price index", "medium", ALL),
    ("retail_sales", r"retail sales|retail trade", "medium", EQUITY),
    ("jobless_claims", r"jobless claims|unemployment insurance weekly claims", "medium", EQUITY),
    ("jolts", r"\bjolts\b|job openings and labor turnover", "medium", EQUITY),
    ("ism", r"\bism\b|purchasing managers", "medium", EQUITY),
    ("eci", r"employment cost index", "medium", EQUITY),
    ("energy_inventories", r"petroleum status|crude (oil )?inventor|\beia\b", "medium", ENERGY),
    ("earnings", r"earnings|quarterly results", "medium", EQUITY),
    ("import_prices", r"import and export price", "low", ALL),
    ("productivity", r"productivity and costs", "low", EQUITY),
)


def classify(title: str) -> tuple[str, str, list[str]]:
    t = title.lower()
    for category, pattern, importance, affected in RULES:
        if re.search(pattern, t):
            return category, importance, list(affected)
    return "other", "low", []


@dataclass(frozen=True, slots=True)
class ParsedEvent:
    uid: str
    ts: datetime
    title: str


def _unfold(text: str) -> list[str]:
    """RFC 5545 line unfolding: continuation lines start with a space or tab."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(v: str) -> str:
    return v.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _parse_dt(value: str, params: dict[str, str]) -> datetime:
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        # All-day entry: no time published. Anchor at 08:30 ET (the usual US release time) and
        # mark it in the title so the user knows the time is assumed.
        d = datetime.strptime(value, "%Y%m%d").date()
        return datetime.combine(d, time(8, 30), tzinfo=ET).astimezone(UTC)
    fmt = "%Y%m%dT%H%M%S" if len(value.rstrip("Z")) == 15 else "%Y%m%dT%H%M"
    naive = datetime.strptime(value.rstrip("Z"), fmt)
    if value.endswith("Z"):
        return naive.replace(tzinfo=UTC)
    tzid = params.get("TZID")
    try:
        tz = ZoneInfo(tzid) if tzid else ET  # floating times are read as New York time
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown time zone {tzid!r}") from exc
    return naive.replace(tzinfo=tz).astimezone(UTC)


def parse_ics(text: str, max_events: int = 5000) -> tuple[list[ParsedEvent], list[str]]:
    """Parse VEVENTs from an iCalendar file. Returns (events, errors)."""
    events: list[ParsedEvent] = []
    errors: list[str] = []
    cur: dict[str, tuple[str, dict[str, str]]] | None = None
    if "BEGIN:VCALENDAR" not in text[:2000].upper():
        return [], ["Not an iCalendar file (missing BEGIN:VCALENDAR)."]
    for line in _unfold(text):
        if not line.strip():
            continue
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            cur = {}
            continue
        if upper == "END:VEVENT":
            if cur is not None:
                try:
                    summary = _unescape(cur.get("SUMMARY", ("", {}))[0]).strip()
                    if not summary or "DTSTART" not in cur:
                        raise ValueError("missing SUMMARY or DTSTART")
                    value, params = cur["DTSTART"]
                    ts = _parse_dt(value, params)
                    all_day = params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value.strip())
                    title = summary + (" (time not published; 08:30 ET assumed)" if all_day else "")
                    uid = (
                        cur.get("UID", ("", {}))[0].strip()
                        or hashlib.sha256(f"{summary}|{ts.isoformat()}".encode()).hexdigest()
                    )
                    events.append(ParsedEvent(uid[:300], ts, title[:300]))
                except ValueError as exc:
                    errors.append(f"Event skipped: {exc}")
            cur = None
            if len(events) >= max_events:
                errors.append(f"Stopped after {max_events} events.")
                break
            continue
        if cur is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, *raw_params = head.split(";")
        params = {}
        for p in raw_params:
            if "=" in p:
                k, v = p.split("=", 1)
                params[k.upper()] = v.strip('"')
        cur[name.upper()] = (value, params)
    return events, errors


async def upsert_events(
    session: AsyncSession, events: Sequence[ParsedEvent], source: str, source_ref: str | None
) -> int:
    """Insert or update events by UID. Returns the number of rows written."""
    n = 0
    for e in events:
        category, importance, affected = classify(e.title)
        stmt = insert(EconomicEvent).values(
            uid=e.uid,
            ts=e.ts,
            title=e.title,
            category=category,
            importance=importance,
            affected=affected,
            country="US",
            source=source,
            source_ref=source_ref,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["uid"],
            set_={
                "ts": stmt.excluded.ts,
                "title": stmt.excluded.title,
                "category": stmt.excluded.category,
                "importance": stmt.excluded.importance,
                "affected": stmt.excluded.affected,
            },
        )
        await session.execute(stmt)
        n += 1
    return n


async def events_between(session: AsyncSession, start: datetime, end: datetime) -> list[EconomicEvent]:
    return list(
        (
            await session.scalars(
                select(EconomicEvent)
                .where(EconomicEvent.ts >= start, EconomicEvent.ts <= end)
                .order_by(EconomicEvent.ts)
            )
        ).all()
    )


def event_to_dict(e: EconomicEvent, now: datetime, pre: int, post: int) -> dict[str, Any]:
    minutes = (e.ts - now).total_seconds() / 60
    return {
        "id": e.id,
        "ts": e.ts.isoformat(),
        "title": e.title,
        "category": e.category,
        "importance": e.importance,
        "affected": e.affected,
        "country": e.country,
        "source": e.source,
        "source_ref": e.source_ref,
        "minutes_until": round(minutes, 1),
        "in_window": -post <= minutes <= pre,
        "window": {"pre_minutes": pre, "post_minutes": post},
    }


def exposure(
    events: Sequence[EconomicEvent],
    symbol: str,
    now: datetime,
    pre: int,
    post: int,
    min_importance: tuple[str, ...] = ("high", "medium"),
) -> list[EconomicEvent]:
    """Events affecting ``symbol`` whose alert window [ts - pre, ts + post] contains ``now``."""
    out = []
    for e in events:
        if symbol not in e.affected or e.importance not in min_importance:
            continue
        if e.ts - timedelta(minutes=pre) <= now <= e.ts + timedelta(minutes=post):
            out.append(e)
    return out


def day_bounds_et(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time(0, 0), tzinfo=ET).astimezone(UTC)
    return start, start + timedelta(days=1)


async def news_exposure(session: AsyncSession, symbol: str, now: datetime, pre: int, post: int) -> dict[str, Any]:
    """Whether ``now`` falls inside the alert window of a high/medium event affecting ``symbol``."""
    has_any = (await session.scalars(select(EconomicEvent.id).limit(1))).first() is not None
    if not has_any:
        return {"status": "unknown", "summary": "Unknown: no economic calendar has been imported.", "events": []}
    nearby = await events_between(session, now - timedelta(minutes=post), now + timedelta(minutes=pre))
    hits = exposure(nearby, symbol, now, pre, post)
    if not hits:
        return {
            "status": "clear",
            "summary": f"No high/medium event affecting {symbol} within -{post}/+{pre} minutes.",
            "events": [],
        }
    return {
        "status": "inside_window",
        "summary": "Inside event window: " + ", ".join(f"{e.title} ({e.ts.astimezone(ET):%H:%M} ET)" for e in hits),
        "events": [{"title": e.title, "ts": e.ts.isoformat(), "importance": e.importance} for e in hits],
    }


# --- ForexFactory weekly calendar export ---------------------------------------------------------
#
# ForexFactory publishes its weekly calendar as a downloadable export (JSON). Each item has a
# title, currency ("country"), ISO date with offset, impact (High / Medium / Low / Holiday),
# forecast and previous. The impact level is ForexFactory's own rating: High = "red folder".
# Holidays are skipped (exchange holidays come from the CME calendar). The feed is rate-limited by
# its publisher, so the worker fetches it at most once an hour.

FF_IMPACT = {"high": "high", "medium": "medium", "low": "low"}


@dataclass(frozen=True, slots=True)
class FFEvent:
    uid: str
    ts: datetime
    title: str
    currency: str
    impact: str  # high | medium | low
    forecast: str | None
    previous: str | None


def parse_forexfactory(data: Any, currencies: Sequence[str] = ("USD",)) -> tuple[list[FFEvent], list[str]]:
    """Parse the ForexFactory JSON export. Unknown or malformed items are reported, never guessed."""
    if not isinstance(data, list):
        return [], ["Expected a JSON list of events."]
    wanted = {c.upper() for c in currencies}
    out: list[FFEvent] = []
    errors: list[str] = []
    for i, it in enumerate(data[:5000]):
        if not isinstance(it, dict):
            errors.append(f"item {i}: not an object")
            continue
        cur = str(it.get("country") or "").upper()
        impact = FF_IMPACT.get(str(it.get("impact") or "").lower())
        title = str(it.get("title") or "").strip()[:250]
        if cur not in wanted or impact is None or not title:
            continue  # other currency, Holiday / non-economic, or empty
        try:
            ts = datetime.fromisoformat(str(it["date"]))
        except (KeyError, ValueError):
            errors.append(f"item {i}: bad date {it.get('date')!r}")
            continue
        if ts.tzinfo is None:
            errors.append(f"item {i}: date without a time zone")
            continue
        ts = ts.astimezone(UTC)
        day = ts.astimezone(ET).date().isoformat()
        uid = "ff:" + hashlib.sha1(f"{cur}|{title}|{day}".encode(), usedforsecurity=False).hexdigest()[:24]
        out.append(
            FFEvent(
                uid=uid,
                ts=ts,
                title=title,
                currency=cur,
                impact=impact,
                forecast=(str(it["forecast"]) or None) if it.get("forecast") not in (None, "") else None,
                previous=(str(it["previous"]) or None) if it.get("previous") not in (None, "") else None,
            )
        )
    return out, errors


def ff_affected(title: str, currency: str, impact: str) -> tuple[str, list[str]]:
    """Category and affected instruments: the keyword rules first, then ForexFactory's impact."""
    category, _imp, affected = classify(title)
    if affected:
        return category, affected
    if currency == "USD":
        return category, list(ALL) if impact == "high" else list(EQUITY) if impact == "medium" else []
    return category, list(METALS + ENERGY) if impact == "high" else []


async def upsert_ff_events(session: AsyncSession, events: Sequence[FFEvent], source_ref: str) -> int:
    """Insert or update ForexFactory events; importance is ForexFactory's impact rating."""
    for e in events:
        category, affected = ff_affected(e.title, e.currency, e.impact)
        title = e.title if e.currency == "USD" else f"{e.currency} {e.title}"
        stmt = insert(EconomicEvent).values(
            uid=e.uid,
            ts=e.ts,
            title=title[:300],
            category=category,
            importance=e.impact,
            affected=affected,
            country=e.currency[:8],
            source="forexfactory",
            source_ref=source_ref[:500],
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["uid"],
            set_={
                "ts": stmt.excluded.ts,
                "title": stmt.excluded.title,
                "category": stmt.excluded.category,
                "importance": stmt.excluded.importance,
                "affected": stmt.excluded.affected,
            },
        )
        await session.execute(stmt)
    return len(events)


async def sync_forexfactory(session: AsyncSession, client: Any, url: str, currencies: Sequence[str]) -> dict[str, Any]:
    """Download the export once and store it. Raises httpx errors to the caller."""
    r = await client.get(url)
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        return {"events": 0, "errors": ["The response was not JSON (the feed may be rate-limiting requests)."]}
    events, errors = parse_forexfactory(data, currencies)
    n = await upsert_ff_events(session, events, url)
    await session.commit()
    return {"events": n, "high": sum(e.impact == "high" for e in events), "errors": errors[:20]}
