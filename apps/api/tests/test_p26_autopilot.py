"""Autopilot (autonomous paper), broker gate, reconciliation, kill switch and phone notifications."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import Settings, get_settings
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.execution import broker
from app.marketdata.store import upsert_candles
from app.models.calendar import EconomicEvent
from app.models.research import PaperAccount
from app.models.signal import SignalRow
from app.services import autopilot, notify
from tests.conftest import CSRF

NOW = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)  # Wednesday 10:00 ET, market open


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []

    async def capture(title: str, body: str, severity: str = "info", client: Any = None) -> bool:
        out.append((title, body, severity))
        return True

    monkeypatch.setattr(notify, "send", capture)
    monkeypatch.setattr(notify, "enabled", lambda: True)
    return out


async def setup(mode: str = "AUTONOMOUS_PAPER", calendar: bool = True) -> None:
    s = get_settings()
    s.market_data_provider = "simulated"
    s.automation_mode = mode  # type: ignore[assignment]
    s.autopilot_strategies = ["ES:vwap_trend:t1_all", "NQ:vwap_trend:t1_all"]
    s.autopilot_max_risk_per_trade = 1000.0
    async with get_sessionmaker()() as session:
        for sym in ("ES", "NQ"):
            c = await SimulatedProvider(clock=lambda: NOW).fetch_candles(sym, NOW - timedelta(hours=4), NOW)
            await upsert_candles(session, c)
        if calendar:
            session.add(
                EconomicEvent(
                    uid="t1",
                    ts=NOW + timedelta(days=2),
                    title="CPI",
                    category="cpi",
                    importance="high",
                    affected=["ES"],
                    country="US",
                    source="manual",
                )
            )
        await session.commit()


_N = {"n": 0}


async def signal(sym: str = "ES", direction: str = "long", stop_pts: float = 5.0, age_min: int = 2) -> int:
    _N["n"] += 1
    async with get_sessionmaker()() as session:
        px = 5000.0 if sym == "ES" else 18000.0
        s = 1 if direction == "long" else -1
        row = SignalRow(
            provider="simulated",
            quality="simulated",
            strategy_id="vwap_trend",
            symbol=sym,
            direction=direction,
            bar_ts=NOW - timedelta(minutes=7, seconds=_N["n"]),
            entry=px,
            entry_low=px - 1,
            entry_high=px + 1,
            stop=px - s * stop_pts,
            targets=[px + s * 2 * stop_pts, px + s * 3 * stop_pts],
            score=1,
            detail={},
            created_at=NOW - timedelta(minutes=age_min),
        )
        session.add(row)
        await session.commit()
        return row.id


async def run_cycle() -> dict:
    async with get_sessionmaker()() as session:
        st = await autopilot.cycle(session, NOW)
        await session.commit()
        return st


async def test_manual_mode_never_creates_orders(sent: list) -> None:
    await setup("APEX_MANUAL")
    await signal()
    st = await run_cycle()
    assert st["orders"] == [] and "no orders" in st["note"]
    async with get_sessionmaker()() as session:
        assert (await session.scalars(select(PaperAccount))).all() == []


async def test_autonomous_paper_trades_once_respects_correlation_and_labels_simulated(sent: list) -> None:
    await setup()
    sid = await signal("ES")
    st = await run_cycle()
    assert len(st["orders"]) == 1 and st["orders"][0]["status"] == "working"
    assert any(t.startswith("[SIMULATED] Order working") for t, _, _ in sent)
    again = await run_cycle()  # same signal is never traded twice
    assert again["orders"] == [] and again["rejected"] == []
    await signal("NQ")  # same correlation group as the open ES entry
    st3 = await run_cycle()
    assert st3["orders"] == [] and "group" in st3["rejected"][0]["reason"]
    async with get_sessionmaker()() as session:
        accts = (await session.scalars(select(PaperAccount))).all()
        assert [a.name for a in accts] == [autopilot.AUTO_ACCOUNT]
        assert accts[0].state["auto_log"][0]["signal"] == sid
        from app.services.paper import get_account

        manual = await get_account(session, get_settings())  # the manual paper account stays separate
        assert manual.name != autopilot.AUTO_ACCOUNT


async def test_risk_news_and_staleness_block_entries(sent: list) -> None:
    await setup(calendar=False)
    await signal("ES")
    st = await run_cycle()
    assert st["orders"] == [] and "news status unknown" in st["rejected"][0]["reason"]
    await setup()
    get_settings().autopilot_max_risk_per_trade = 200.0
    await signal("ES", stop_pts=20)  # $1,000+ at stop on one ES contract
    st = await run_cycle()
    assert "per-trade limit" in st["rejected"][-1]["reason"]
    get_settings().autopilot_max_risk_per_trade = 1000.0
    later = NOW + timedelta(minutes=30)  # bars stop at NOW: stale while the market is open
    async with get_sessionmaker()() as session:
        await signal("ES")
        st = await autopilot.cycle(session, later)
    assert st["halted"].get("ES") == "market data stale or missing"


async def test_kill_switch_cancels_and_flattens(sent: list) -> None:
    await setup()
    await signal("ES")
    await run_cycle()
    await autopilot.kill(True, "test")
    st = await run_cycle()
    assert st["halted"] == {"*": "kill switch"} and any("kill switch" in t for t, _, _ in sent)
    async with get_sessionmaker()() as session:
        acct = await session.scalar(select(PaperAccount))
        assert all(o["status"] not in ("working", "pending") for o in acct.state["orders"])
    await autopilot.kill(False, "test")
    assert await autopilot.killed() is None


def test_broker_gate_refuses_by_default_and_always_for_apex(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Settings(
        automation_mode="AUTHORIZED_BROKER",
        broker_account_type="Apex 50K EOD evaluation",
        broker_adapter="x",
        broker_live_enabled=True,
    )
    reasons = broker.broker_gate(s, "apex-50k-eod-evaluation", [50.0] * 40)
    assert any("Apex" in r for r in reasons) and any("adapter" in r for r in reasons)
    monkeypatch.setitem(broker.ADAPTERS, "dummy", lambda _s: None)  # type: ignore[arg-type,return-value]
    ok = Settings(
        automation_mode="AUTHORIZED_BROKER",
        broker_account_type="personal margin account (algo permitted)",
        broker_adapter="dummy",
        broker_live_enabled=True,
    )
    assert broker.broker_gate(ok, "apex-50k-eod-evaluation", [50.0, 60.0, -20.0] * 12) == []
    assert any("prospective evidence" in r for r in broker.broker_gate(ok, "x", [10.0] * 5))
    assert any(
        "live-enablement" in r
        for r in broker.broker_gate(Settings(**{**ok.model_dump(), "broker_live_enabled": False}), "x", [50.0] * 40)
    )
    assert broker.ADAPTERS.get("tradovate") is None


async def test_authorized_broker_mode_places_nothing_and_alerts(sent: list) -> None:
    await setup("AUTHORIZED_BROKER")
    await signal("ES")
    st = await run_cycle()
    assert st["orders"] == [] and st["broker_gate"] and any("REFUSED" in t for t, _, _ in sent)
    async with get_sessionmaker()() as session:
        assert (await session.scalars(select(PaperAccount))).all() == []


def test_reconciliation_reports_every_mismatch() -> None:
    issues = broker.reconcile(
        {"ES": 1, "NQ": 0},
        {"ES": 2, "MNQ": -1},
        [{"client_id": "a", "qty": 1}, {"client_id": "b", "qty": 1}],
        [{"client_id": "b", "qty": 2}, {"client_id": "c", "qty": 1}],
    )
    assert len(issues) == 5 and any("unknown to the broker" in i for i in issues)
    assert broker.reconcile({"ES": 1}, {"ES": 1}, [], []) == []


async def test_notifications_ntfy_and_failures_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    s.notify_provider = "ntfy"  # type: ignore[assignment]
    s.notify_ntfy_url = "https://ntfy.example/apex-secret-topic"
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        assert await notify.send("[SIMULATED] Fill", "ES long 1", "warning", client=c)
    assert seen[0].headers["Priority"] == "high" and seen[0].content == b"ES long 1"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as c:
        assert await notify.send("x", "y", client=c) is False
    s.notify_min_severity = "critical"  # type: ignore[assignment]
    assert await notify.send("x", "y", "info") is False
    s.notify_provider = "none"  # type: ignore[assignment]


async def test_api_status_and_kill_switch(admin_client: httpx.AsyncClient) -> None:
    get_settings().market_data_provider = "simulated"
    st = (await admin_client.get("/api/autopilot")).json()
    assert st["label"].startswith("SIMULATED") and st["prospective_evidence"]["sufficient"] is False
    assert (await admin_client.post("/api/autopilot/kill", headers=CSRF)).json()["kill_switch"]["by"]
    assert (await admin_client.get("/api/autopilot")).json()["kill_switch"] is not None
    assert (await admin_client.post("/api/autopilot/resume", headers=CSRF)).json()["kill_switch"] is None
    await get_redis().delete(autopilot.KILL_KEY)
