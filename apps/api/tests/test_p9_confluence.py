"""Confluence rules: independent families, hard gates, plan construction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.research.confluence import confluence
from app.research.smc import Analysis, Pattern

NOW = datetime(2026, 11, 11, 16, 0, tzinfo=UTC)
DATA = {"available": True, "stale": False, "provider": "databento", "quality": "live", "price": 5000.0}
CLEAR = {"status": "clear", "summary": "clear", "events": []}
RISK = {"known": True, "risk_budget": 800.0, "rules_verified": False, "stale": False}


def an(tf: str, trend: str, patterns: list[Pattern] | None = None, liq: list | None = None) -> Analysis:
    return Analysis("NQ", tf, NOW, 400, 4.0, trend, patterns or [], [], {}, None, liq or [])  # type: ignore[arg-type]


def pat(
    typ: str, direction: str, top: float, bottom: float, status: str, inv: float | None, mins_ago: int = 5, **extra
) -> Pattern:
    t = NOW - timedelta(minutes=mins_ago)
    return Pattern(typ, typ, direction, t, t, top, bottom, status, "", inv, [], 60, extra)  # type: ignore[arg-type]


SWEEP = pat("buy_side_sweep", "bearish", 5004, 5001, "confirmed_rejection", 5004, level=5001.0)
OB = pat("bearish_order_block", "bearish", 5002, 4999, "active", 5002)
LIQ_BELOW = [{"name": "swing low", "price": 4980.0, "side": "below"}]


def run(analyses: dict[str, Analysis], news: dict = CLEAR, risk: dict = RISK, data: dict = DATA) -> dict:
    return confluence("NQ", analyses, data["price"], 0.25, news, risk, data)


def test_aligned_context_location_trigger_gives_short_bias_with_plan() -> None:
    c = run({"1h": an("1h", "bearish"), "15m": an("15m", "bearish"), "5m": an("5m", "bearish", [SWEEP, OB], LIQ_BELOW)})
    assert c["outcome"] == "SHORT BIAS" and c["confirmation_required"] is False
    assert [f["vote"] for f in c["families"]] == [-1, -1, -1]
    p = c["plan"]
    assert p["direction"] == "short" and p["entry_zone"] == [4999, 5002]
    assert p["stop"] == 5004.25 and p["targets"][0]["price"] == 4980
    assert p["reward_risk"] == round((5000.5 - 4980) / (5004.25 - 5000.5), 2)


def test_trigger_missing_means_confirmation_required() -> None:
    c = run({"1h": an("1h", "bearish"), "15m": an("15m", "bearish"), "5m": an("5m", "bearish", [OB])})
    assert c["headline"] == "SHORT BIAS — CONFIRMATION REQUIRED"


def test_overlapping_patterns_without_context_never_create_a_bias() -> None:
    c = run({"1h": an("1h", "neutral"), "15m": an("15m", "neutral"), "5m": an("5m", "bearish", [SWEEP, OB])})
    assert c["outcome"] == "WAIT" and c["plan"] is None


def test_conflicting_higher_timeframes_cast_no_context_vote() -> None:
    c = run({"1h": an("1h", "bullish"), "15m": an("15m", "bearish"), "5m": an("5m", "bearish", [SWEEP, OB])})
    assert c["families"][0]["vote"] == 0 and c["outcome"] == "WAIT"


def test_hard_gates_force_no_trade() -> None:
    base = {"1h": an("1h", "bearish"), "15m": an("15m", "bearish"), "5m": an("5m", "bearish", [SWEEP, OB])}
    news = {"status": "inside_window", "summary": "Inside event window: CPI (08:30 ET)", "events": []}
    assert run(base, news=news)["outcome"] == "NO TRADE"
    assert run(base, risk={**RISK, "risk_budget": 0})["outcome"] == "NO TRADE"
    assert run(base, data={**DATA, "stale": True})["outcome"] == "NO TRADE"


def test_old_trigger_is_ignored_and_tight_stops_are_widened() -> None:
    old = pat("buy_side_sweep", "bearish", 5004, 5001, "confirmed_rejection", 5004, mins_ago=120, level=5001.0)
    c = run({"1h": an("1h", "bearish"), "15m": an("15m", "bearish"), "5m": an("5m", "bearish", [old])})
    assert c["families"][2]["vote"] == 0
    tight = pat("bearish_fvg", "bearish", 5000.5, 5000.0, "open", 5000.5)
    mss = pat("MSS", "bearish", 5001, 5001, "confirmed", None)
    c = run({"1h": an("1h", "bearish"), "15m": an("15m", "bearish"), "5m": an("5m", "bearish", [mss, tight])})
    p = c["plan"]
    assert (p["stop"] - p["entry"]) >= 0.5 * 4.0 and "widened" in p["stop_basis"]
