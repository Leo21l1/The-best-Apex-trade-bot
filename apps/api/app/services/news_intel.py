"""News intelligence: re-evaluates the cached economic calendar against the clock (every 60 s).

The source (ForexFactory weekly export) is downloaded on its own, slower schedule
(``calendar.guarded_sync_forexfactory``). This module never downloads anything: it only reads the
events stored in PostgreSQL and the time of the last SUCCESSFUL download, so a cached calendar is
never presented as freshly downloaded, and a scheduled calendar is never presented as live headlines.

Status labels (most urgent first):
* NEWS DATA STALE / UNAVAILABLE - no calendar, or the last successful download is too old
* NEWS WINDOW ACTIVE            - a high-impact event within [-pre, +active_after] minutes
* POST-NEWS VOLATILITY          - a high-impact event released (+active_after, +post] minutes ago
* HIGH IMPACT UPCOMING          - a high-impact event within the next ``news_upcoming_minutes``
* NO MAJOR SCHEDULED EVENT NEARBY

The direction of any reaction is never predicted: it depends on expectations and positioning.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.marketdata.calendar import ET
from app.models.calendar import EconomicEvent, SourceSyncState

STALE = "NEWS DATA STALE / UNAVAILABLE"
ACTIVE = "NEWS WINDOW ACTIVE"
POST = "POST-NEWS VOLATILITY"
UPCOMING = "HIGH IMPACT UPCOMING"
CLEAR = "NO MAJOR SCHEDULED EVENT NEARBY"

#: Event families that matter most for equity-index futures (keyword categories from calendar.classify).
PRIORITY = {"fomc", "rates", "cpi", "ppi", "nfp", "employment", "jobless", "gdp", "pce", "retail", "ism"}

DIRECTION_NOTE = (
    "Reactions depend on the result versus expectations, positioning and conditions; a 'good' number "
    "does not mean prices will rise."
)
CALENDAR_NOTE = "Scheduled economic calendar (cached), not a breaking-news feed. Unscheduled headlines are not covered."


def _event(e: EconomicEvent, now: datetime) -> dict[str, Any]:
    mins = (e.ts - now).total_seconds() / 60
    return {
        "id": e.id,
        "title": e.title,
        "ts": e.ts.isoformat(),
        "time_et": e.ts.astimezone(ET).strftime("%a %b %d %H:%M ET"),
        "date_et": e.ts.astimezone(ET).date().isoformat(),
        "impact": e.importance,
        "category": e.category,
        "country": e.country,
        "affected": e.affected,
        "forecast": e.forecast,
        "previous": e.previous,
        "actual": e.actual,
        "actual_note": None if e.actual else "Not supplied by the source.",
        "source": e.source,
        "minutes_until": round(mins, 1),
        "priority": e.category in PRIORITY,
    }


def classify_window(minutes_until: float, s: Settings) -> str | None:
    """Status contributed by one high-impact event ``minutes_until`` from now (negative = released)."""
    pre, after, post, up = (
        s.calendar_pre_event_minutes,
        s.news_active_after_minutes,
        s.calendar_post_event_minutes,
        s.news_upcoming_minutes,
    )
    if -after <= minutes_until <= pre:
        return ACTIVE
    if -max(post, after) <= minutes_until < -after:
        return POST
    if pre < minutes_until <= up:
        return UPCOMING
    return None


def freshness(state: SourceSyncState | None, has_events: bool, now: datetime, s: Settings) -> dict[str, Any]:
    last = state.last_success_at if state else None
    age_h = (now - last).total_seconds() / 3600 if last else None
    stale = (not has_events) or (s.calendar_forexfactory and (age_h is None or age_h > s.news_stale_hours))
    return {
        "source": "ForexFactory weekly calendar export" if s.calendar_forexfactory else "Imported calendars",
        "last_success_at": last.isoformat() if last else None,
        "last_attempt_at": state.last_attempt_at.isoformat() if state and state.last_attempt_at else None,
        "next_attempt_at": state.next_attempt_at.isoformat() if state and state.next_attempt_at else None,
        "last_error": state.last_error if state else None,
        "age_hours": round(age_h, 2) if age_h is not None else None,
        "stale_after_hours": s.news_stale_hours,
        "stale": bool(stale),
        "note": CALENDAR_NOTE,
    }


async def evaluate(session: AsyncSession, now: datetime | None = None, symbol: str = "ES") -> dict[str, Any]:
    """Current news-risk status for ``symbol`` plus today's / tomorrow's events (Eastern time)."""
    s = get_settings()
    now = now or datetime.now(UTC)
    today = now.astimezone(ET).date()
    start = datetime.combine(today, datetime.min.time(), tzinfo=ET).astimezone(UTC)
    end = start + timedelta(days=4)  # covers "tomorrow" across a weekend
    rows = list(
        (
            await session.scalars(
                select(EconomicEvent)
                .where(EconomicEvent.ts >= start - timedelta(hours=2), EconomicEvent.ts < end)
                .order_by(EconomicEvent.ts)
            )
        ).all()
    )
    has_any = rows or (await session.scalars(select(EconomicEvent.id).limit(1))).first() is not None
    state = await session.get(SourceSyncState, "forexfactory")
    fresh = freshness(state, bool(has_any), now, s)
    relevant = [e for e in rows if symbol in (e.affected or [])]
    high = [e for e in relevant if e.importance == "high"]
    hits: list[tuple[str, EconomicEvent]] = []
    for e in high:
        st = classify_window((e.ts - now).total_seconds() / 60, s)
        if st:
            hits.append((st, e))
    order = [ACTIVE, POST, UPCOMING]
    hits.sort(key=lambda h: (order.index(h[0]), abs((h[1].ts - now).total_seconds())))
    if fresh["stale"]:
        status = STALE
        summary = (
            "No economic calendar is loaded."
            if not has_any
            else f"Calendar last downloaded {fresh['age_hours'] or '?'} h ago (stale after {s.news_stale_hours} h)."
        )
    elif hits:
        status, ev = hits[0]
        m = round((ev.ts - now).total_seconds() / 60)
        when = f"in {m} min" if m > 0 else "now" if m == 0 else f"{-m} min ago"
        summary = f"{ev.title} {when} ({ev.ts.astimezone(ET):%H:%M} ET, high impact)."
    else:
        status = CLEAR
        summary = "No high-impact event affecting this instrument within the configured windows."
    nxt = next((e for e in high if e.ts > now), None)
    tomorrow_d = next(
        (d for d in sorted({e.ts.astimezone(ET).date() for e in relevant}) if d > today),
        None,
    )
    return {
        "as_of": now.isoformat(),
        "evaluated_every_seconds": 60,
        "symbol": symbol,
        "status": status,
        "summary": summary,
        # A stale calendar still blocks nothing silently: callers treat STALE as caution.
        "caution": status in (STALE, ACTIVE, POST),
        "next_high_impact": _event(nxt, now) if nxt else None,
        "today": [_event(e, now) for e in relevant if e.ts.astimezone(ET).date() == today],
        "next_day": {
            "date": tomorrow_d.isoformat() if tomorrow_d else None,
            "events": [
                _event(e, now)
                for e in relevant
                if tomorrow_d and e.ts.astimezone(ET).date() == tomorrow_d and e.importance in ("high", "medium")
            ],
        },
        "windows": {
            "upcoming_minutes": s.news_upcoming_minutes,
            "pre_minutes": s.calendar_pre_event_minutes,
            "active_after_minutes": s.news_active_after_minutes,
            "post_minutes": s.calendar_post_event_minutes,
        },
        "freshness": fresh,
        "direction_note": DIRECTION_NOTE,
    }
