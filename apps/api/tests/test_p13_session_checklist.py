"""Trading Session Intelligence and the eight-condition readiness checklist."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest

from app.marketdata.calendar import ET
from app.research.bars import Bar
from app.services.checklist import ChecklistConfig, confirmed_vwap_crosses, evaluate, reward_risk
from app.services.session_intel import (
    data_status,
    market_status,
    next_transition,
    reference_window,
    relative_volume,
    session_context,
)


def et(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=ET).astimezone(UTC)


# --- clock, windows, DST, holidays ------------------------------------------------------------


@pytest.mark.parametrize(
    ("ts", "key"),
    [
        (et(2026, 11, 17, 7, 0), "premarket"),
        (et(2026, 11, 17, 9, 45), "cash_open"),
        (et(2026, 11, 17, 11, 0), "morning"),
        (et(2026, 11, 17, 12, 45), "lunch"),
        (et(2026, 11, 17, 14, 0), "afternoon"),
        (et(2026, 11, 17, 15, 30), "cash_close"),
        (et(2026, 11, 17, 16, 30), "post_close"),
        (et(2026, 11, 17, 17, 30), "maintenance"),
        (et(2026, 11, 17, 20, 0), "overnight"),
        (et(2026, 11, 18, 2, 0), "overnight"),
    ],
)
def test_reference_windows(ts: datetime, key: str) -> None:
    assert reference_window(ts)["key"] == key


def test_new_york_time_follows_dst_not_a_fixed_offset() -> None:
    summer = session_context("ES", datetime(2026, 7, 15, 13, 45, tzinfo=UTC))  # 09:45 EDT
    winter = session_context("ES", datetime(2026, 11, 17, 14, 45, tzinfo=UTC))  # 09:45 EST
    assert summer["window"]["key"] == winter["window"]["key"] == "cash_open"
    assert summer["ny_offset"] == "-0400" and winter["ny_offset"] == "-0500"
    # Spring forward (2026-03-08 02:00 -> 03:00): from 01:30 EST the next boundary is 06:00 EDT,
    # which is only 3.5 elapsed hours later.
    nt = next_transition(et(2026, 3, 8, 1, 30))
    assert nt["at_et"] == "06:00" and nt["in_minutes"] == 210
    # Fall back (2026-11-01): 01:30 EDT -> 06:00 EST is 5.5 elapsed hours.
    nt = next_transition(datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    assert nt["at_et"] == "06:00" and nt["in_minutes"] == 330


def test_cme_holidays_early_closes_and_maintenance() -> None:
    assert market_status("ES", et(2026, 11, 17, 10, 0))["open"] is True
    xmas = market_status("ES", et(2026, 12, 25, 10, 0))
    assert xmas["open"] is False and "holiday" in xmas["reason"].lower()
    tg = market_status("ES", et(2026, 11, 26, 13, 30))  # Thanksgiving: equity futures halt 13:00 ET
    assert tg["open"] is False and tg["early_close"] and "13:00" in tg["reason"]
    assert market_status("ES", et(2026, 11, 26, 12, 30))["open"] is True
    m = market_status("ES", et(2026, 11, 17, 17, 30))
    assert m["open"] is False and "maintenance" in m["reason"]
    assert "Weekend" in market_status("ES", et(2026, 11, 21, 10, 0))["reason"]
    assert market_status("ES", et(2026, 11, 22, 18, 30))["open"] is True  # Sunday evening open
    assert market_status("CL", et(2026, 11, 17, 10, 0))["calendar"] == "CMEGlobex_Energy"


# --- bars helpers ---------------------------------------------------------------------------------


def bar(ts: datetime, c: float, v: int = 100, rng: float = 1.0, o: float | None = None) -> Bar:
    o = c if o is None else o
    return Bar(ts, o, max(o, c) + rng / 2, min(o, c) - rng / 2, c, v)


def history(days: list[date], start: time, minutes: int, vol: int, price: float = 5000.0) -> list[Bar]:
    out = []
    for d in days:
        t0 = datetime.combine(d, start, ET).astimezone(UTC)
        out += [bar(t0 + timedelta(minutes=i), price, vol) for i in range(minutes)]
    return out


PRIOR = [date(2026, 11, d) for d in (3, 4, 5, 6, 9, 10, 11, 12, 13, 16)]  # 10 weekdays


def test_relative_volume_is_time_of_day_normalised() -> None:
    now = et(2026, 11, 17, 10, 0)
    bars = history(PRIOR, time(9, 45), 15, 100)  # same clock window: 1,500 per session
    bars += history(PRIOR, time(12, 0), 15, 10_000)  # a different window with huge volume: ignored
    bars += history([date(2026, 11, 17)], time(9, 45), 15, 200)
    rv = relative_volume(sorted(bars, key=lambda b: b.ts), now)
    assert rv["available"] and rv["current_volume"] == 3000 and rv["reference_volume"] == 1500
    assert rv["ratio"] == 2.0 and rv["reference_sessions"] == 10 and rv["window_et"] == "09:45-10:00"
    short = relative_volume(
        history(PRIOR[:2], time(9, 45), 15, 100) + history([date(2026, 11, 17)], time(9, 45), 15, 1), now
    )
    assert short["available"] is False and "need at least 3" in short["reason"]


def test_vwap_crosses_ignore_flicker_and_reset_each_session() -> None:
    d = date(2026, 11, 17)
    t0 = datetime.combine(d, time(9, 30), ET).astimezone(UTC)
    closes = [5000, 5004, 5004.25, 4999.75, 5000.25, 4996, 5004, 4995, 5005]
    bars5 = [bar(t0 + timedelta(minutes=5 * i), c, 1000, rng=0.5) for i, c in enumerate(closes)]
    crosses = confirmed_vwap_crosses(bars5, 0.25, buffer_atr=0.1)
    # Closes within the buffer around VWAP (4999.75 / 5000.25 near ~5001) do not flip the side.
    assert [c["direction"] for c in crosses] == ["down", "up", "down", "up"]
    nxt = [bar(t0 + timedelta(days=1, minutes=5 * i), c, 1000, rng=0.5) for i, c in enumerate([5000, 4990])]
    assert confirmed_vwap_crosses(bars5 + nxt, 0.25, 0.1)[-1]["trading_date"] == "2026-11-17"  # no cross on reset


def _oscillating(n_crosses: int) -> tuple[list[Bar], datetime]:
    """Today's 1m bars from 09:30 ET (one hour) giving exactly ``n_crosses`` confirmed 5m VWAP crosses."""
    t0 = datetime.combine(date(2026, 11, 17), time(9, 30), ET).astimezone(UTC)
    levels = [4980, 5020] + [4980 if i % 2 == 0 else 5020 for i in range(n_crosses)]
    levels += [levels[-1]] * (12 - len(levels))
    bars = [bar(t0 + timedelta(minutes=5 * k + m), lvl, 100, rng=0.5) for k, lvl in enumerate(levels) for m in range(5)]
    return bars, t0 + timedelta(minutes=60)


@pytest.mark.parametrize(("n", "expected"), [(3, "PASS"), (4, "FAIL")])
def test_chop_threshold_boundary(n: int, expected: str) -> None:
    bars, now = _oscillating(n)
    found = len([c for c in confirmed_vwap_crosses([b for b in _five(bars)], 0.25, 0.1)])
    assert found == n
    res = evaluate("ES", now, bars, ChecklistConfig())
    item = next(i for i in res["items"] if i["key"] == "not_choppy")
    assert item["status"] == expected and item["evidence"]["crosses"] == n


def _five(bars1: list[Bar]) -> list[Bar]:
    from app.marketdata.types import Timeframe
    from app.research.bars import aggregate

    return aggregate(bars1, Timeframe.M5)


@pytest.mark.parametrize(
    ("symbol", "entry", "stop", "target", "costs", "ratio"),
    [
        # ES $50/pt: reward 4pt=$200, risk 2pt=$100; costs 2x$2.50 + 2 ticks x $12.50 = $30
        ("ES", 5000.0, 4998.0, 5004.0, 30.0, round(170 / 130, 2)),
        # MES $5/pt: reward $20, risk $10; costs 2x$0.60 + 2 x $1.25 = $3.70
        ("MES", 5000.0, 4998.0, 5004.0, 3.7, round(16.3 / 13.7, 2)),
        # NQ $20/pt: reward 10pt=$200, risk 5pt=$100; costs $5 + 2 x $5 = $15
        ("NQ", 21000.0, 20995.0, 21010.0, 15.0, round(185 / 115, 2)),
        # MNQ $2/pt: reward $20, risk $10; costs $1.20 + 2 x $0.50 = $2.20
        ("MNQ", 21000.0, 20995.0, 21010.0, 2.2, round(17.8 / 12.2, 2)),
    ],
)
def test_reward_risk_math_net_of_costs(
    symbol: str, entry: float, stop: float, target: float, costs: float, ratio: float
) -> None:
    rr = reward_risk(symbol, "long", entry, stop, target, ChecklistConfig())
    assert rr["valid"] and rr["costs"] == costs and rr["ratio"] == ratio
    assert reward_risk(symbol, "long", entry, entry + 1, target, ChecklistConfig())["valid"] is False
    assert reward_risk(symbol, "short", entry, entry + 5, entry + 10, ChecklistConfig())["valid"] is False


def test_missing_data_gives_unknowns_and_no_trade() -> None:
    now = et(2026, 11, 17, 10, 0)
    data = data_status(None, None, now, True)
    assert data["status"] == "UNAVAILABLE"
    res = evaluate("ES", now, [], ChecklistConfig(), None, data)
    by = {i["key"]: i["status"] for i in res["items"]}
    assert by["volume"] == by["not_choppy"] == by["vwap_crosses"] == by["reward_risk"] == by["setup"] == "UNKNOWN"
    assert res["status"] == "NO TRADE" and any("UNAVAILABLE" in c for c in res["critical"])
    stale = data_status("live", now - timedelta(minutes=20), now, True)
    assert stale["status"] == "STALE"
    assert data_status("live", now - timedelta(hours=40), et(2026, 11, 21, 10, 0), False)["status"] == "LIVE"


def test_setup_alignment_conflict_and_review_status() -> None:
    bars, now = _oscillating(0)
    setup = {"strategy_id": "orb", "direction": "long", "entry": 5006.0, "stop": 5003.0, "targets": [5012.0]}
    res = evaluate("ES", now, bars, ChecklistConfig(), setup)
    by = {i["key"]: i for i in res["items"]}
    assert by["setup"]["status"] == "PASS" and by["reward_risk"]["status"] == "PASS"
    assert by["alignment"]["evidence"]["trends"].keys() == {"1m", "5m", "15m", "1h"}
    assert res["status"] in ("REVIEW SETUP", "WAIT") and res["passed"] == sum(
        1 for i in res["items"] if i["status"] == "PASS"
    )
    bad = evaluate("ES", now, bars, ChecklistConfig(), {**setup, "targets": [5004.0]})
    assert next(i for i in bad["items"] if i["key"] == "reward_risk")["status"] == "FAIL"


def test_no_lookahead_future_bars_do_not_change_the_result() -> None:
    bars, now = _oscillating(3)
    future = [bar(now + timedelta(minutes=i), 4900 + i, 99999) for i in range(1, 60)]
    a = evaluate("ES", now, bars, ChecklistConfig())
    b = evaluate("ES", now, bars + future, ChecklistConfig())
    assert [(i["key"], i["status"], i["reason"]) for i in a["items"]] == [
        (i["key"], i["status"], i["reason"]) for i in b["items"]
    ]


def test_kill_zone_and_lunch_by_style() -> None:
    bars, _ = _oscillating(0)
    lunch = evaluate("ES", et(2026, 11, 17, 12, 30), bars, ChecklistConfig())
    by = {i["key"]: i["status"] for i in lunch["items"]}
    assert by["kill_zone"] == "FAIL" and by["outside_lunch"] == "FAIL"
    soon = evaluate("ES", et(2026, 11, 17, 13, 10), bars, ChecklistConfig())
    assert next(i for i in soon["items"] if i["key"] == "kill_zone")["status"] == "WAIT"
    pm = evaluate("ES", et(2026, 11, 17, 14, 0), bars, ChecklistConfig(style="afternoon"))
    assert next(i for i in pm["items"] if i["key"] == "kill_zone")["status"] == "PASS"
    closed = evaluate("ES", et(2026, 11, 21, 10, 0), bars, ChecklistConfig())
    assert closed["status"] == "NO TRADE" and closed["items"][0]["status"] == "FAIL"


async def test_readiness_api(admin_client: httpx.AsyncClient) -> None:
    from app.core.config import get_settings

    get_settings().market_data_provider = "simulated"
    r = await admin_client.get("/api/session/readiness", params={"symbol": "MNQ", "style": "scalping"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["checklist"]["items"]) == 8 and body["checklist"]["style"] == "scalping"
    assert {i["status"] for i in body["checklist"]["items"]} <= {"PASS", "FAIL", "WAIT", "UNKNOWN"}
    assert body["data"]["status"] == "UNAVAILABLE" and "depth-of-market" in body["liquidity_note"]
    assert (await admin_client.get("/api/session/readiness", params={"symbol": "ZZ"})).status_code == 404
    assert (await admin_client.get("/api/session/readiness", params={"style": "yolo"})).status_code == 422
    ctx = (await admin_client.get("/api/session/context", params={"symbol": "ES"})).json()
    assert ctx["window"]["key"] and ctx["market"]["calendar"] == "CME_Equity"
