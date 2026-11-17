"""Live Trade Entry Confirmation: deterministic states over fixed test inputs (test fixtures, not market data)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.marketdata.store import upsert_candles
from app.models.research import TradePlan
from app.models.signal import SignalRow
from app.research.bars import Bar
from app.services.confirmation import (
    DATA_UNAVAILABLE,
    LONG_READY,
    NO_TRADE,
    SHORT_READY,
    WAIT_STATE,
    decide,
    mandatory_for,
    next_eligible_window,
)
from tests.conftest import CSRF

NOW = datetime(2026, 11, 13, 15, 31, tzinfo=UTC)  # Friday 10:31 ET
START = datetime(2026, 11, 13, 14, 30, tzinfo=UTC)  # 09:30 ET


def bars(direction: str = "long") -> list[Bar]:
    s = 1 if direction == "long" else -1
    out = []
    for i in range(61):
        p = 5000 + s * i * 0.25
        out.append(Bar(START + timedelta(minutes=i), p, p + 0.5, p - 0.5, p, 100))
    return out


def last_price(direction: str = "long") -> float:
    return bars(direction)[-1].close


def readiness(trend: str = "bullish", **kw: Any) -> dict[str, Any]:
    r = {
        "data": {"status": "LIVE", "age_seconds": 20},
        "news": {"status": "clear", "summary": "No high-impact event."},
        "risk": {"known": True},
        "checklist": {
            "market": {"open": True, "reason": None},
            "direction": trend,
            "window": {"key": "cash_open"},
            "items": [
                {
                    "key": "alignment",
                    "status": "PASS",
                    "evidence": {"trends": {"1m": trend, "5m": trend, "15m": trend, "1h": trend}},
                },
                {"key": "volume", "status": "PASS", "reason": "Relative volume 1.4.", "evidence": {"ratio": 1.4}},
                {"key": "kill_zone", "status": "PASS", "reason": "10:31 ET inside 09:30-11:30."},
                {"key": "outside_lunch", "status": "PASS", "reason": "Outside lunch."},
                {"key": "vwap_crosses", "status": "PASS", "evidence": {"count": 1}},
            ],
        },
    }
    for k, v in kw.items():
        r[k] = v
    return r


def signal(direction: str = "long", strategy: str = "vwap_trend", rr_t1: float = 2.0) -> dict[str, Any]:
    s = 1 if direction == "long" else -1
    p = last_price(direction)
    return {
        "strategy_id": strategy,
        "direction": direction,
        "entry": p,
        "entry_low": p - 0.75,
        "entry_high": p + 0.75,
        "stop": p - s * 5,
        "targets": [p + s * 5 * rr_t1, p + s * 15],
        "detected_at": (NOW - timedelta(minutes=6)).isoformat(),
        "detected_at_et": "10:25 ET",
    }


RISK = {
    "known": True,
    "stale": False,
    "rules_verified": True,
    "verdict": "no_violation_found",
    "max_size": 3,
    "may_be_called_compliant": True,
    "violations": [],
    "warnings": [],
}
VOL = {"available": True, "level": "normal", "atr_percentile_same_time": 50, "state": "normal"}


def run(direction: str = "long", **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "bars": bars(direction),
        "readiness": readiness("bullish" if direction == "long" else "bearish"),
        "signal": signal(direction),
        "risk": copy.deepcopy(RISK),
        "volatility": VOL,
        "regime": {"labels": ["bullish trend" if direction == "long" else "bearish trend"]},
    }
    args.update(kw)
    return decide(
        "ES",
        kw.get("now", NOW),
        args["bars"],
        args["readiness"],
        args["signal"],
        args["risk"],
        volatility=args["volatility"],
        regime=args["regime"],
    )


def cond(out: dict[str, Any], key: str) -> dict[str, Any]:
    return next(c for c in out["conditions"] if c["key"] == key)


def test_valid_long_is_ready_with_tick_rounded_levels() -> None:
    out = run()
    assert out["state"] == LONG_READY, out["missing"]
    assert len(out["conditions"]) == 15 and not out["missing"]
    lv = out["levels"]
    assert all(round(x * 4) == x * 4 for x in (lv["entry"], lv["stop"], lv["t1"], lv["t2"], lv["entry_with_slippage"]))
    assert lv["stop_ticks"] == 20 and lv["stop_points"] == 5 and lv["entry_with_slippage"] == lv["entry"] + 0.25
    assert lv["loss_per_contract"] > 250 and lv["reward_per_contract_t1"] < 500  # costs included
    assert out["max_size"] == 3 and out["expires_at"] and out["apex_compliance"].startswith("NO VIOLATION")
    assert cond(out, "history")["status"] == "UNKNOWN" and not cond(out, "history")["mandatory"]


def test_valid_short_is_ready() -> None:
    out = run("short")
    assert out["state"] == SHORT_READY, out["missing"]
    assert out["levels"]["stop"] > out["levels"]["entry"] > out["levels"]["t1"]


def test_missing_trigger_waits_without_levels() -> None:
    out = run(signal=None)
    assert out["state"] == WAIT_STATE and out["levels"] is None
    assert any(m["key"] == "trigger" and m["would_confirm"] for m in out["missing"])
    assert cond(out, "reward_risk")["status"] == "UNKNOWN"  # levels are never invented


def test_conflicting_timeframes_is_no_trade() -> None:
    rd = readiness()
    rd["checklist"]["items"][0]["evidence"]["trends"].update({"15m": "bullish", "1h": "bearish"})
    out = run(readiness=rd)
    assert out["state"] == NO_TRADE and cond(out, "htf_alignment")["status"] == "FAIL"


def test_choppy_regime_waits() -> None:
    out = run(regime={"labels": ["choppy"], "evidence": {"efficiency_ratio_2h": 0.08, "vwap_crosses_today": 5}})
    assert out["state"] == WAIT_STATE and cond(out, "structure")["status"] == "WAIT"
    rd = readiness()
    rd["checklist"]["items"][4]["evidence"]["count"] = 5
    out2 = run(readiness=rd)
    assert cond(out2, "vwap")["status"] == "FAIL" and out2["state"] != LONG_READY  # vwap is mandatory for vwap_trend


def test_insufficient_history_is_never_a_pass() -> None:
    assert run(bars=[], readiness=readiness(data={"status": "UNAVAILABLE"}))["state"] == DATA_UNAVAILABLE
    out = run(volatility={"available": True, "level": "unknown", "note": "Only 2 prior sessions"})
    assert cond(out, "volatility")["status"] == "UNKNOWN"
    rd = readiness()
    rd["checklist"]["items"][1] = {"key": "volume", "status": "UNKNOWN", "reason": "need at least 5 sessions"}
    assert cond(run(readiness=rd), "rvol")["status"] == "UNKNOWN"


def test_stale_data_is_no_trade() -> None:
    out = run(readiness=readiness(data={"status": "STALE", "reason": "Newest bar ended 9 min ago"}))
    assert out["state"] == NO_TRADE and "9 min" in out["summary"]
    hist = run(readiness=readiness(data={"status": "HISTORICAL", "age_seconds": 30}))
    assert hist["state"] == NO_TRADE and hist["data_class"] == "HISTORICAL"  # never treated as live


def test_news_unknown_and_inside_window() -> None:
    out = run(readiness=readiness(news={"status": "unknown", "summary": "no calendar imported"}))
    assert out["state"] == WAIT_STATE and out["news_status"] == "NEWS STATUS UNKNOWN"
    hit = run(readiness=readiness(news={"status": "inside_window", "summary": "CPI 08:30"}))
    assert hit["state"] == NO_TRADE and "CPI" in hit["summary"]


def test_outside_window_waits_with_next_eligible_window() -> None:
    rd = readiness()
    rd["checklist"]["items"][2] = {"key": "kill_zone", "status": "FAIL", "reason": "12:05 ET is outside"}
    out = run(readiness=rd)
    assert out["state"] == WAIT_STATE and cond(out, "session")["status"] == "WAIT"
    nxt = next_eligible_window("ES", datetime(2026, 11, 13, 17, 5, tzinfo=UTC))  # Fri 12:05 ET
    assert nxt and nxt["start_et"] == "13:30" and nxt["in_minutes"] == 85
    weekend = next_eligible_window("ES", datetime(2026, 11, 14, 17, 0, tzinfo=UTC))  # Saturday
    assert weekend and weekend["date"] == "2026-11-16" and weekend["start_et"] == "09:30"


def test_invalid_reward_risk_is_no_trade() -> None:
    out = run(signal=signal(rr_t1=1.0))
    assert out["state"] == NO_TRADE and cond(out, "reward_risk")["status"] == "FAIL"


def test_missing_or_stale_balance_is_unknown_not_ready() -> None:
    out = run(risk={"known": False, "stale": False, "max_size": None})
    assert out["state"] == WAIT_STATE and cond(out, "risk")["status"] == "UNKNOWN"
    stale = run(risk={**RISK, "stale": True})
    assert stale["state"] == WAIT_STATE and "stale" in cond(stale, "risk")["reason"]


def test_unverified_rules_never_claim_compliance() -> None:
    out = run(risk={**RISK, "rules_verified": False, "may_be_called_compliant": False})
    assert "UNVERIFIED" in cond(out, "risk")["reason"]
    assert out["apex_compliance"] == "NOT CONFIRMED - rule profile unverified"


def test_position_size_violation_is_no_trade() -> None:
    assert run(risk={**RISK, "max_size": 0})["state"] == NO_TRADE
    v = run(risk={**RISK, "verdict": "violation", "violations": ["Daily loss limit already reached."]})
    assert v["state"] == NO_TRADE and "Daily loss" in v["summary"]


def test_invalidated_or_expired_or_closed() -> None:
    b = bars()
    b[-1] = Bar(b[-1].ts, b[-1].open, b[-1].high, b[-1].close - 10, b[-1].close, 100)  # trades through the stop
    assert run(bars=b)["state"] == NO_TRADE
    late = NOW + timedelta(minutes=30)
    out = run(now=late)
    assert cond(out, "trigger")["status"] == "WAIT" and out["state"] != LONG_READY
    rd = readiness()
    rd["checklist"]["market"] = {"open": False, "reason": "weekend"}
    assert run(readiness=rd)["state"] == NO_TRADE


def test_strategy_rules() -> None:
    assert "ob_fvg" in mandatory_for("smc_ob_retest") and "ob_fvg" not in mandatory_for("vwap_trend")
    assert "htf_alignment" not in mandatory_for("mean_reversion") and "risk" in mandatory_for("mean_reversion")
    out = run(signal=signal(strategy="smc_ob_retest"))
    assert out["state"] == WAIT_STATE and cond(out, "ob_fvg")["status"] == "WAIT"  # no zone overlaps


async def test_api_confirmation_and_save_plan(admin_client: httpx.AsyncClient) -> None:
    get_settings().market_data_provider = "simulated"
    now = datetime.now(UTC)
    p = SimulatedProvider(clock=lambda: now)
    async with get_sessionmaker()() as s:
        await upsert_candles(s, await p.fetch_candles("ES", now - timedelta(days=3), now))
        await s.commit()
    r = (await admin_client.get("/api/confirmation", params={"symbol": "ES"})).json()
    assert r["state"] in (WAIT_STATE, NO_TRADE) and len(r["conditions"]) == 15
    assert r["data_class"] in ("SIMULATED", "STALE") and r["disclaimer"].startswith("Analysis only")
    none = await admin_client.post("/api/confirmation/plan", json={"symbol": "ES"}, headers=CSRF)
    assert none.status_code == 409
    price = (await p.fetch_candles("ES", now - timedelta(minutes=10), now))[-1].close
    px = float(price)
    async with get_sessionmaker()() as s:
        s.add(
            SignalRow(
                provider="simulated",
                quality="simulated",
                strategy_id="vwap_trend",
                symbol="ES",
                direction="long",
                bar_ts=now - timedelta(minutes=6),
                entry=px,
                entry_low=px - 1,
                entry_high=px + 1,
                stop=px - 5,
                targets=[px + 10, px + 15],
                score=1,
                detail={},
            )
        )
        await s.commit()
    r2 = (await admin_client.get("/api/confirmation", params={"symbol": "ES"})).json()
    assert r2["levels"] and r2["strategy_id"] == "vwap_trend" and r2["state"] != DATA_UNAVAILABLE
    saved = await admin_client.post("/api/confirmation/plan", json={"symbol": "ES", "quantity": 1}, headers=CSRF)
    assert saved.status_code == 201, saved.text
    body = saved.json()
    assert body["order_sent"] is False and body["terminal_url"] == "/terminal"
    async with get_sessionmaker()() as s:
        plan = await s.scalar(select(TradePlan))
        assert plan and plan.status == "draft" and plan.signal_snapshot["confirmation"]["conditions"]
    assert (await admin_client.get("/api/confirmation", params={"symbol": "NOPE"})).status_code == 404


async def test_copilot_tools_and_statistics_endpoint(admin_client: httpx.AsyncClient) -> None:
    from app.ai.tools import TOOLS, ToolContext, run_tool
    from app.models.research import BacktestRun

    assert list(TOOLS)[-3:] == ["get_entry_confirmation", "get_data_readiness", "get_strategy_statistics"]
    get_settings().market_data_provider = "simulated"
    async with get_sessionmaker()() as s:
        await upsert_candles(
            s, await SimulatedProvider(clock=lambda: NOW).fetch_candles("ES", NOW - timedelta(days=2), NOW)
        )
        for prov in ("simulated", "databento"):
            s.add(
                BacktestRun(
                    status="done",
                    strategy_id="orb",
                    symbol="ES",
                    provider=prov,
                    start=NOW - timedelta(days=9),
                    end=NOW,
                    finished_at=NOW,
                    params={},
                    result={
                        "data_class": "simulated" if prov == "simulated" else "historical",
                        "in_sample": {"trades": 40},
                        "out_of_sample": {"trades": 12, "expectancy": 3.2},
                        "segments": {"in_sample": {}, "out_of_sample": {}},
                    },
                )
            )
        await s.commit()
        ctx = ToolContext(s, get_settings(), NOW)
        c, err = await run_tool(ctx, "get_entry_confirmation", {"symbol": "ES"})
        assert not err and c["state"] in (WAIT_STATE, NO_TRADE, DATA_UNAVAILABLE) and len(c["conditions"]) == 15
        d, err = await run_tool(ctx, "get_data_readiness", {"symbols": ["ES"]})
        assert not err and d["symbols"][0]["symbol"] == "ES"
        st, err = await run_tool(ctx, "get_strategy_statistics", {"symbol": "ES"})
        assert not err and [r["data_class"] for r in st["rows"]] == ["HISTORICAL"]
    api = (await admin_client.get("/api/data/strategy-statistics", params={"include_simulated": True})).json()
    assert {r["data_class"] for r in api["rows"]} == {"SIMULATED", "HISTORICAL"} or len(api["rows"]) == 1
