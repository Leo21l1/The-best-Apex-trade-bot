"""Trading Session Intelligence: deterministic New York session context for one instrument.

* Time is always ``America/New_York`` (IANA, DST-aware) - never the host's local zone or a fixed
  UTC offset.
* Whether the market is open comes from the CME Globex schedule for the instrument's product group
  (``pandas_market_calendars``: CME_Equity / CMEGlobex_Energy / CMEGlobex_Metals), which includes
  exchange holidays and early closes, combined with the 17:00-18:00 ET daily maintenance break.
  CME futures hours are NOT NYSE cash hours: the futures trade overnight.
* The reference windows (premarket, opening window, lunch ...) are configurable labels for
  describing context. They are not predictions and not trading rules.
* Volume and volatility are compared with the SAME New York clock window on previous trading
  days (time-of-day normalised), never with a different part of the session.
* Liquidity: only proxies derived from 1-minute OHLCV (volume, range). Order-book depth is NOT
  known unless a licensed depth-of-market feed is connected; none is.
"""

from __future__ import annotations

import functools
import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from statistics import median
from typing import Any

from app.marketdata.calendar import ET, trading_day
from app.marketdata.instruments import INSTRUMENTS
from app.research.bars import Bar

# --- configuration ------------------------------------------------------------------------------

DEFAULT_WINDOWS: tuple[tuple[str, str, time, time], ...] = (
    ("premarket", "Premarket / preparation", time(6, 0), time(9, 30)),
    ("cash_open", "US cash market opening window", time(9, 30), time(10, 30)),
    ("morning", "Morning continuation", time(10, 30), time(11, 30)),
    ("lunch", "Typical midday / lunch window", time(11, 30), time(13, 30)),
    ("afternoon", "Afternoon session", time(13, 30), time(15, 0)),
    ("cash_close", "Cash market closing window", time(15, 0), time(16, 0)),
    ("post_close", "Post-cash-close futures trading", time(16, 0), time(17, 0)),
    ("maintenance", "Typical CME daily maintenance break", time(17, 0), time(18, 0)),
    ("overnight", "Overnight futures session", time(18, 0), time(6, 0)),  # wraps midnight
)

CALENDAR_BY_CLASS = {"equity_index": "CME_Equity", "energy": "CMEGlobex_Energy", "metals": "CMEGlobex_Metals"}


@dataclass(frozen=True)
class SessionConfig:
    windows: tuple[tuple[str, str, time, time], ...] = DEFAULT_WINDOWS
    #: Previous trading days used for time-of-day volume/volatility baselines.
    baseline_days: int = 10
    #: Minutes in the "current" comparison window.
    window_minutes: int = 15
    #: Data older than this while the market is open is STALE.
    stale_seconds: int = 180


# --- exchange schedule --------------------------------------------------------------------------


@functools.lru_cache(maxsize=32)
def _schedule(calendar: str, year: int) -> dict[date, tuple[datetime, datetime]]:
    """Trade date -> (session open UTC, session close UTC) for one product group and year."""
    import pandas_market_calendars as mcal

    cal = mcal.get_calendar(calendar)
    sched = cal.schedule(start_date=f"{year}-01-01", end_date=f"{year + 1}-01-05")
    out: dict[date, tuple[datetime, datetime]] = {}
    for idx, row in sched.iterrows():
        out[idx.date()] = (row["market_open"].to_pydatetime(), row["market_close"].to_pydatetime())
    return out


def calendar_for(symbol: str) -> str:
    inst = INSTRUMENTS.get(symbol)
    return CALENDAR_BY_CLASS.get(inst.asset_class if inst else "", "CME_Equity")


def session_bounds(symbol: str, d: date) -> tuple[datetime, datetime] | None:
    """Exchange session for trade date ``d`` (None = no session: holiday or weekend)."""
    return _schedule(calendar_for(symbol), d.year).get(d)


def standard_close(d: date) -> datetime:
    return datetime.combine(d, time(17, 0), ET).astimezone(UTC)


def market_status(symbol: str, now: datetime) -> dict[str, Any]:
    """Open/closed from the exchange calendar, with the reason when closed."""
    local = now.astimezone(ET)
    td = trading_day(now)
    bounds = session_bounds(symbol, td)
    early = bounds is not None and bounds[1] < standard_close(td)
    info: dict[str, Any] = {
        "calendar": calendar_for(symbol),
        "trading_date": td.isoformat(),
        "early_close": early,
        "session_open": bounds[0].isoformat() if bounds else None,
        "session_close": bounds[1].isoformat() if bounds else None,
    }
    if bounds and bounds[0] <= now < bounds[1]:
        return {**info, "open": True, "reason": "Exchange session open" + (" (early close today)" if early else "")}
    if bounds is None:
        reason = (
            "Weekend"
            if local.weekday() >= 5 or (local.weekday() == 4 and local.time() >= time(17))
            else "Exchange holiday (no session for this trade date)"
        )
    elif now >= bounds[1] and early:
        reason = f"Early close at {bounds[1].astimezone(ET):%H:%M} ET (exchange holiday schedule)"
    elif time(17, 0) <= local.time() < time(18, 0):
        reason = "CME daily maintenance break (17:00-18:00 ET)"
    else:
        reason = "Outside the exchange session"
    return {**info, "open": False, "reason": reason}


def next_open(symbol: str, now: datetime) -> datetime | None:
    td = trading_day(now)
    for k in range(0, 10):
        b = session_bounds(symbol, td + timedelta(days=k))
        if b and b[0] > now:
            return b[0]
        if b and b[0] <= now < b[1]:
            return None
    return None


# --- reference windows --------------------------------------------------------------------------


def _in(t: time, start: time, end: time) -> bool:
    return start <= t < end if start < end else (t >= start or t < end)


def reference_window(now: datetime, cfg: SessionConfig = SessionConfig()) -> dict[str, Any]:
    t = now.astimezone(ET).time()
    for key, label, start, end in cfg.windows:
        if _in(t, start, end):
            return {"key": key, "label": label, "start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")}
    return {"key": "other", "label": "Outside configured windows", "start": None, "end": None}


def next_transition(now: datetime, cfg: SessionConfig = SessionConfig()) -> dict[str, Any]:
    """The next window boundary on the New York wall clock (DST-safe via zone arithmetic)."""
    local = now.astimezone(ET)
    best: tuple[datetime, str] | None = None
    for _key, label, start, _end in cfg.windows:
        for add in (0, 1):
            d = local.date() + timedelta(days=add)
            cand = datetime.combine(d, start, ET).astimezone(UTC)
            if cand > now.astimezone(UTC) and (best is None or cand < best[0]):
                best = (cand, label)
    assert best is not None
    # All arithmetic in UTC: subtracting two datetimes that share a ZoneInfo gives WALL-CLOCK time in
    # Python, which is wrong by an hour across a daylight-saving change.
    return {
        "label": best[1],
        "at": best[0].isoformat(),
        "at_et": best[0].astimezone(ET).strftime("%H:%M"),
        "in_minutes": int((best[0] - now.astimezone(UTC)).total_seconds() // 60),
    }


# --- market metrics (time-of-day normalised) ----------------------------------------------------


def _clock_window_volume(bars: Sequence[Bar], day: date, start: time, end: time) -> tuple[int, int]:
    """(volume, bars) of 1m bars whose NY clock time is in [start, end) on trade date ``day``."""
    vol = n = 0
    for b in bars:
        loc = b.ts.astimezone(ET)
        if trading_day(b.ts) == day and start <= loc.time() < end:
            vol += b.volume
            n += 1
    return vol, n


def relative_volume(bars: Sequence[Bar], now: datetime, cfg: SessionConfig = SessionConfig()) -> dict[str, Any]:
    """Volume of the last ``window_minutes`` completed minutes vs the same NY clock window on the
    previous ``baseline_days`` trade dates that had bars in that window."""
    end_local = now.astimezone(ET).replace(second=0, microsecond=0)
    start_local = end_local - timedelta(minutes=cfg.window_minutes)
    if start_local.date() != end_local.date():
        return {"available": False, "reason": "Comparison window spans midnight; not computed."}
    s_t, e_t = start_local.time(), end_local.time()
    today = trading_day(now)
    cur_vol, cur_n = _clock_window_volume(bars, today, s_t, e_t)
    days = sorted({trading_day(b.ts) for b in bars if trading_day(b.ts) < today}, reverse=True)
    ref = []
    for d in days:
        v, n = _clock_window_volume(bars, d, s_t, e_t)
        if n:
            ref.append(v)
        if len(ref) >= cfg.baseline_days:
            break
    if cur_n == 0:
        return {"available": False, "reason": "No completed bars in the current window."}
    if len(ref) < 3:
        return {
            "available": False,
            "reason": f"Only {len(ref)} comparable prior sessions; need at least 3.",
            "current_volume": cur_vol,
        }
    base = median(ref)
    # Acceleration: last 5 minutes vs the average 5-minute volume of the rest of the window.
    last5 = sum(b.volume for b in bars if end_local - timedelta(minutes=5) <= b.ts.astimezone(ET) < end_local)
    rest = cur_vol - last5
    rest_per5 = rest / max(cfg.window_minutes / 5 - 1, 1)
    return {
        "available": True,
        "window_et": f"{s_t:%H:%M}-{e_t:%H:%M}",
        "current_volume": cur_vol,
        "reference_volume": round(base, 1),
        "reference_sessions": len(ref),
        "ratio": round(cur_vol / base, 2) if base > 0 else None,
        "acceleration": round(last5 / rest_per5, 2) if rest_per5 > 0 else None,
        "method": f"Median volume of the same {cfg.window_minutes}-minute New York clock window on the previous "
        f"{len(ref)} sessions.",
    }


def relative_volatility(bars: Sequence[Bar], now: datetime, cfg: SessionConfig = SessionConfig()) -> dict[str, Any]:
    """Mean 1-minute range in the current window vs the same clock window on prior sessions."""
    end_local = now.astimezone(ET).replace(second=0, microsecond=0)
    start_local = end_local - timedelta(minutes=cfg.window_minutes)
    if start_local.date() != end_local.date():
        return {"available": False, "reason": "Comparison window spans midnight; not computed."}
    s_t, e_t = start_local.time(), end_local.time()
    today = trading_day(now)

    def avg_range(d: date) -> float | None:
        r = [b.high - b.low for b in bars if trading_day(b.ts) == d and s_t <= b.ts.astimezone(ET).time() < e_t]
        return sum(r) / len(r) if r else None

    cur = avg_range(today)
    days = sorted({trading_day(b.ts) for b in bars if trading_day(b.ts) < today}, reverse=True)
    ref = [x for x in (avg_range(d) for d in days[: cfg.baseline_days * 2]) if x is not None][: cfg.baseline_days]
    if cur is None or len(ref) < 3:
        return {"available": False, "reason": "Not enough comparable bars."}
    base = median(ref)
    ratio = cur / base if base > 0 else None
    label = "unknown" if ratio is None else "low" if ratio < 0.7 else "high" if ratio > 1.4 else "normal"
    return {
        "available": True,
        "current_avg_range": round(cur, 4),
        "reference_avg_range": round(base, 4),
        "ratio": round(ratio, 2) if ratio else None,
        "label": label,
        "reference_sessions": len(ref),
    }


def efficiency_ratio(closes: Sequence[float]) -> float | None:
    """|net change| / sum of |changes| over the series (1 = straight line, ~0 = back and forth)."""
    if len(closes) < 3:
        return None
    path = sum(abs(b - a) for a, b in itertools.pairwise(closes))
    return abs(closes[-1] - closes[0]) / path if path > 0 else 0.0


def data_status(
    feed_quality: str | None,
    last_bar: datetime | None,
    now: datetime,
    market_open: bool,
    cfg: SessionConfig = SessionConfig(),
) -> dict[str, Any]:
    """LIVE / HISTORICAL / SIMULATED / STALE / UNAVAILABLE from the feed class and bar age."""
    if last_bar is None or not feed_quality or feed_quality == "unavailable":
        return {"status": "UNAVAILABLE", "reason": "No market data stored for this instrument."}
    age = (now - (last_bar + timedelta(minutes=1))).total_seconds()
    if market_open and age > cfg.stale_seconds:
        return {
            "status": "STALE",
            "reason": f"Newest bar ended {int(age // 60)} min ago while the market is open.",
            "age_seconds": round(age),
        }
    status = {"live": "LIVE", "delayed": "HISTORICAL", "historical": "HISTORICAL", "simulated": "SIMULATED"}.get(
        feed_quality, feed_quality.upper()
    )
    return {
        "status": status,
        "age_seconds": round(age),
        "reason": "Market closed: the last bar is expected to be old." if not market_open else None,
    }


@dataclass
class SessionContext:
    symbol: str
    now: datetime
    payload: dict[str, Any] = field(default_factory=dict)


def session_context(symbol: str, now: datetime, cfg: SessionConfig = SessionConfig()) -> dict[str, Any]:
    """Clock and exchange context only (no market data)."""
    local = now.astimezone(ET)
    status = market_status(symbol, now)
    nxt = next_open(symbol, now) if not status["open"] else None
    return {
        "symbol": symbol,
        "now_utc": now.astimezone(UTC).isoformat(),
        "ny_time": local.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "ny_offset": local.strftime("%z"),
        "trading_date": trading_day(now).isoformat(),
        "window": reference_window(now, cfg),
        "next_transition": next_transition(now, cfg),
        "market": {**status, "next_open": nxt.isoformat() if nxt else None},
        "disclaimer": "Reference windows are configurable descriptions of the trading day, not predictions or rules.",
    }
