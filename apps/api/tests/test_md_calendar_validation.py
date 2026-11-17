from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.marketdata.calendar import (
    expected_minutes,
    is_market_open,
    to_utc,
    trading_day,
)
from app.marketdata.types import Candle, DataQuality, Timeframe
from app.marketdata.validation import (
    DuplicateFilter,
    find_gaps,
    round_to_tick,
    sort_dedupe,
    validate_candle,
)


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


# 2026-11-11 is a Wednesday; EST (UTC-5). 2026-07-15 is a Wednesday; EDT (UTC-4).
@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        (utc(2026, 11, 11, 15, 0), True),  # Wed 10:00 ET
        (utc(2026, 11, 11, 21, 59), True),  # Wed 16:59 ET
        (utc(2026, 11, 11, 22, 0), False),  # Wed 17:00 ET maintenance break
        (utc(2026, 11, 11, 22, 59), False),
        (utc(2026, 11, 11, 23, 0), True),  # Wed 18:00 ET reopen
        (utc(2026, 11, 13, 21, 59), True),  # Fri 16:59 ET
        (utc(2026, 11, 13, 22, 0), False),  # Fri 17:00 ET weekly close
        (utc(2026, 11, 14, 15, 0), False),  # Saturday
        (utc(2026, 11, 15, 22, 59), False),  # Sun 17:59 ET
        (utc(2026, 11, 15, 23, 0), True),  # Sun 18:00 ET weekly open
        (utc(2026, 7, 15, 20, 59), True),  # EDT: Wed 16:59 ET
        (utc(2026, 7, 15, 21, 0), False),  # EDT: Wed 17:00 ET
        (utc(2026, 7, 15, 22, 0), True),  # EDT: Wed 18:00 ET
    ],
)
def test_session_boundaries(ts: datetime, expected: bool) -> None:
    assert is_market_open(ts) is expected


def test_trading_day_rolls_at_1800_et() -> None:
    assert trading_day(utc(2026, 11, 11, 21, 59)).isoformat() == "2026-11-11"
    assert trading_day(utc(2026, 11, 11, 23, 0)).isoformat() == "2026-11-12"
    assert trading_day(utc(2026, 11, 15, 23, 0)).isoformat() == "2026-11-16"  # Sunday -> Monday


def test_naive_timestamps_rejected() -> None:
    with pytest.raises(ValueError, match="naive"):
        to_utc(datetime(2026, 1, 1))


def test_expected_minutes_skips_break_and_weekend() -> None:
    # Wed 16:58 ET .. 18:01 ET: 16:58, 16:59, then 18:00, 18:01
    mins = list(expected_minutes(utc(2026, 11, 11, 21, 58), utc(2026, 11, 11, 23, 1)))
    assert [m.strftime("%H:%M") for m in mins] == ["21:58", "21:59", "23:00", "23:01"]
    assert list(expected_minutes(utc(2026, 11, 14, 0, 0), utc(2026, 11, 14, 23, 0))) == []
    # A full weekday trading day has 23 hours of minutes.
    day = list(expected_minutes(utc(2026, 11, 10, 23, 0), utc(2026, 11, 11, 21, 59)))
    assert len(day) == 23 * 60


NOW = utc(2026, 11, 11, 15, 30)


def bar(**kw: object) -> Candle:
    base: dict[str, object] = dict(
        symbol="ES",
        timeframe=Timeframe.M1,
        ts=utc(2026, 11, 11, 15, 0),
        open=Decimal("5000.00"),
        high=Decimal("5001.25"),
        low=Decimal("4999.50"),
        close=Decimal("5000.75"),
        volume=100,
        provider="test",
        quality=DataQuality.SIMULATED,
    )
    base.update(kw)
    return Candle(**base)  # type: ignore[arg-type]


def test_valid_bar_has_no_problems() -> None:
    assert validate_candle(bar(), NOW) == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"symbol": "ZZZ"}, "unknown symbol"),
        ({"ts": datetime(2026, 11, 11, 15, 0)}, "naive"),
        ({"ts": utc(2026, 11, 11, 15, 0, 30)}, "minute boundary"),
        ({"ts": utc(2026, 11, 11, 15, 40)}, "future"),
        ({"high": Decimal("5000.50")}, "OHLC inconsistent"),  # high < close
        ({"low": Decimal("5000.25")}, "OHLC inconsistent"),  # low > open
        ({"close": Decimal("5000.10")}, "tick grid"),
        ({"open": Decimal("0")}, "non-positive"),
        ({"low": Decimal("-1")}, "non-positive"),
        ({"open": Decimal("NaN")}, "non-finite"),
        ({"volume": -1}, "negative volume"),
        ({"ts": utc(2026, 11, 7, 15, 0)}, "outside exchange session"),  # Saturday
    ],
)
def test_invalid_bars_rejected(overrides: dict[str, object], fragment: str) -> None:
    problems = validate_candle(bar(**overrides), NOW)
    assert any(fragment in p for p in problems), problems


def test_tick_sizes_per_instrument() -> None:
    cl = dict(
        symbol="CL",
        open=Decimal("70.01"),
        high=Decimal("70.05"),
        low=Decimal("69.99"),
        close=Decimal("70.03"),
    )
    assert validate_candle(bar(**cl), NOW) == []
    assert validate_candle(bar(**{**cl, "close": Decimal("70.035")}), NOW) != []
    ym = dict(
        symbol="YM",
        open=Decimal("40000"),
        high=Decimal("40010"),
        low=Decimal("39990"),
        close=Decimal("40005"),
    )
    assert validate_candle(bar(**ym), NOW) == []
    assert validate_candle(bar(**{**ym, "close": Decimal("40005.5")}), NOW) != []
    assert round_to_tick(Decimal("5000.13"), Decimal("0.25")) == Decimal("5000.25")
    assert round_to_tick(Decimal("5000.12"), Decimal("0.25")) == Decimal("5000.00")


def test_duplicate_filter_drops_identical_but_passes_updates() -> None:
    f = DuplicateFilter()
    a = bar(is_closed=False)
    assert not f.is_duplicate(a)
    assert f.is_duplicate(a)  # exact repeat
    assert not f.is_duplicate(bar(is_closed=False, close=Decimal("5001.00")))  # update
    assert not f.is_duplicate(bar(is_closed=True, close=Decimal("5001.00")))  # close event
    assert f.is_duplicate(bar(is_closed=True, close=Decimal("5001.00")))
    assert f.dropped == 2


def test_duplicate_filter_memory_is_bounded() -> None:
    f = DuplicateFilter(max_keys=10)
    for i in range(100):
        f.is_duplicate(bar(ts=utc(2026, 11, 11, 15, 0) + timedelta(minutes=i)))
    assert len(f._seen) <= 10


def test_find_gaps_reports_runs() -> None:
    start, end = utc(2026, 11, 11, 15, 0), utc(2026, 11, 11, 15, 9)
    present = [start + timedelta(minutes=m) for m in (0, 1, 2, 5, 9)]
    gaps = find_gaps(present, start, end)
    assert [(g.start.minute, g.end.minute, g.missing_bars) for g in gaps] == [(3, 4, 2), (6, 8, 3)]
    assert find_gaps([start + timedelta(minutes=m) for m in range(10)], start, end) == []
    # Trailing gap is reported; the maintenance break is not a gap.
    assert find_gaps([start], start, start + timedelta(minutes=2))[0].missing_bars == 2
    brk = find_gaps(
        [utc(2026, 11, 11, 21, 59), utc(2026, 11, 11, 23, 0)],
        utc(2026, 11, 11, 21, 59),
        utc(2026, 11, 11, 23, 0),
    )
    assert brk == []


def test_sort_dedupe_keeps_last() -> None:
    a = bar(close=Decimal("5000.25"))
    b = bar(close=Decimal("5000.50"))
    later = bar(ts=utc(2026, 11, 11, 15, 1))
    out = sort_dedupe([later, a, b])
    assert [c.ts.minute for c in out] == [0, 1]
    assert out[0].close == Decimal("5000.50")
