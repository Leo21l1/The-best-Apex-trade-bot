"""Launch readiness: instrument defaults, price change, risk warnings, preflight analysis checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import Settings, get_settings
from app.core.db import get_sessionmaker
from app.marketdata.store import upsert_candles
from app.research.bars import Bar
from app.services import analyzer, setups
from app.services.preflight import run_preflight
from tests.test_p29_setups import CFG, scenario, signal

NOW = datetime(2026, 11, 19, 14, 0, tzinfo=UTC)  # Thursday 09:00 ET


def test_default_subscriptions_are_es_nq_mnq_only() -> None:
    assert Settings().market_data_symbols == ["ES", "NQ", "MNQ"]
    assert Settings().analyzer_symbols == ["ES", "NQ", "MNQ"]


def test_risk_warning_flags_without_moving_the_structural_stop() -> None:
    small = setups.evaluate(scenario(), signal(), None, CFG)["levels"]
    assert small["risk_usd_1c"] == 135.0 and small["risk_warning"] is None
    tight = setups.SetupConfig(costs=CFG.costs, risk_warning_usd=100.0)
    lv = setups.evaluate(scenario(), signal(), None, tight)["levels"]
    assert lv["stop"] == small["stop"] == 20994.0  # never moved to fit the limit
    assert lv["risk_warning"] and "$135.00" in lv["risk_warning"] and "$100" in lv["risk_warning"]


def test_price_change_uses_the_previous_completed_session() -> None:
    m1 = [Bar(NOW - timedelta(minutes=10 - i), 5010, 5011, 5009, 5010 + i, 10) for i in range(10)]
    d1 = [
        Bar(datetime(2026, 11, 17, tzinfo=UTC), 4990, 5005, 4980, 4995, 1),
        Bar(datetime(2026, 11, 18, tzinfo=UTC), 4995, 5002, 4990, 5000, 1),
        Bar(datetime(2026, 11, 19, tzinfo=UTC), 5000, 5020, 4999, 5019, 1),
    ]  # today: still forming, ignored
    doc = analyzer.build("ES", m1, d1, NOW, {"quality": "simulated"}, "simulated", None)
    d = doc["data"]
    assert d["prev_close"] == 5000 and d["prev_session"] == "2026-11-18"
    assert d["change"] == m1[-1].close - 5000 and d["change_pct"] == round((m1[-1].close - 5000) / 50, 3)


async def test_preflight_reports_migrations_analysis_journal_and_signal_window() -> None:
    get_settings().market_data_provider = "simulated"
    async with get_sessionmaker()() as s:
        c = await SimulatedProvider(clock=lambda: NOW).fetch_candles("ES", NOW - timedelta(days=3), NOW)
        await upsert_candles(s, c)
        await s.commit()
        rep = await run_preflight(s, ["ES", "MNQ"], NOW)
    b = {c["check"]: c for c in rep["checks"]}
    assert b["Migrations"]["result"] == "PASS"
    assert b["ES analysis"]["result"] in ("PASS", "WARN") and "SMC active on" in b["ES analysis"]["detail"]
    assert "setup" in b["ES analysis"]["detail"] and "/5" in b["ES analysis"]["detail"]
    assert b["MNQ analysis"]["result"] == "FAIL" and "DATA UNAVAILABLE" in b["MNQ analysis"]["detail"]
    assert b["Trading journal"]["result"] == "PASS"
    assert "09:35-15:30 ET" in b["Signal window"]["detail"] and b["Signal window"]["result"] == "INFO"
    assert rep["ok"] is False  # failures are never turned into a pass
