"""CME Globex session calendar (regular weekly schedule).

All supported products trade Sunday 18:00 ET to Friday 17:00 ET with a daily maintenance break
17:00-18:00 ET. A "trading day" runs 18:00 ET (previous calendar day) to 17:00 ET.

LIMITATION: exchange holidays and early closes are not modelled yet, so holiday closures are
reported as data gaps by the gap detector. This is noted wherever gap counts are shown.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SESSION_OPEN = time(18, 0)
SESSION_CLOSE = time(17, 0)


def to_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("naive datetime not allowed; timestamps must be timezone-aware")
    return ts.astimezone(UTC)


def is_market_open(ts: datetime) -> bool:
    """True if a bar that OPENS at ``ts`` belongs to a Globex session."""
    local = to_utc(ts).astimezone(ET)
    wd, t = local.weekday(), local.time()  # Mon=0 .. Sun=6
    if wd == 5:  # Saturday
        return False
    if wd == 6:  # Sunday: opens 18:00
        return t >= SESSION_OPEN
    if wd == 4:  # Friday: closes 17:00
        return t < SESSION_CLOSE
    return t < SESSION_CLOSE or t >= SESSION_OPEN


def trading_day(ts: datetime) -> date:
    """Trading date a timestamp belongs to (sessions opening at 18:00 ET roll forward)."""
    local = to_utc(ts).astimezone(ET)
    return (local + timedelta(hours=6)).date()


def floor_minute(ts: datetime) -> datetime:
    return to_utc(ts).replace(second=0, microsecond=0)


def expected_minutes(start: datetime, end: datetime) -> Iterator[datetime]:
    """Every 1-minute bar open in [start, end] that falls inside a session."""
    cur = floor_minute(start)
    if cur < to_utc(start):
        cur += timedelta(minutes=1)
    end = to_utc(end)
    step = timedelta(minutes=1)
    while cur <= end:
        if is_market_open(cur):
            yield cur
        cur += step
