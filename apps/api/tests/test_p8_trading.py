"""Trade tracking, review, attribution, coaching, scenarios, replay, plans and the terminal API."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.marketdata.feed import LATEST_KEY, STATUS_KEY
from app.marketdata.types import Candle, DataQuality, Timeframe
from app.models.calendar import EconomicEvent
from app.models.research import JournalTrade, PaperAccount, TradePlan
from app.services.paper import process
from app.services.trade_assistant import assess
from app.services.trade_review import review_trade
from tests.conftest import CSRF

# Wed 2026-11-11; RTH 14:30-21:00 UTC. History starts days earlier so indicators have data.
T_ENTRY = datetime(2026, 11, 11, 16, 0, tzinfo=UTC)  # 11:00 ET


def flat_history(start: datetime, end: datetime, price: float = 5000.0) -> list[Candle]:
    from app.marketdata.calendar import is_market_open

    out, t = [], start
    while t < end:
        if is_market_open(t):
            w = 0.5 if t.minute % 2 else -0.5
            out.append(_c(t, price, price + 1 + w, price - 1 + w, price + w))
        t += timedelta(minutes=1)
    return out


def _c(t: datetime, o: float, h: float, low: float, c: float, v: int = 500) -> Candle:
    return Candle(
        "ES",
        Timeframe.M1,
        t,
        Decimal(str(o)),
        Decimal(str(h)),
        Decimal(str(low)),
        Decimal(str(c)),
        v,
        "simulated",
        DataQuality.SIMULATED,
    )


async def seed(bars: list[Candle]) -> None:
    from app.marketdata.store import upsert_candles

    get_settings().market_data_provider = "simulated"
    async with get_sessionmaker()() as s:
        await upsert_candles(s, bars)
        await s.commit()


def trade_path() -> list[Candle]:
    """Flat history, then from 16:00: dip to 4997 (MAE 3pts), rally to 5010 (MFE 10pts), exit 5008 at 16:20."""
    hist = flat_history(T_ENTRY - timedelta(days=4), T_ENTRY)
    path = []
    prices = [
        5000,
        4998,
        4997.5,
        4999,
        5002,
        5004,
        5006,
        5008,
        5009,
        5010,
        5009,
        5008.5,
        5008,
        5008,
        5008,
        5008,
        5008,
        5008,
        5008,
        5008,
    ]
    for i, px in enumerate(prices):
        lo = 4997.0 if i == 2 else min(px, prices[i - 1] if i else px) - 0.25
        hi = 5010.0 if i == 9 else max(px, prices[i - 1] if i else px) + 0.25
        path.append(_c(T_ENTRY + timedelta(minutes=i), prices[i - 1] if i else px, hi, lo, px))
    after = flat_history(T_ENTRY + timedelta(minutes=20), T_ENTRY + timedelta(hours=5), 5008.0)
    return hist + path + after


async def add_trade(**kw: object) -> int:
    base = dict(
        source_key=f"k{kw.get('source_key', 1)}",
        source="csv",
        trading_date=T_ENTRY.date(),
        symbol="ES",
        contract="ESZ6",
        direction="long",
        quantity=2,
        entry_time=T_ENTRY,
        exit_time=T_ENTRY + timedelta(minutes=20),
        entry_price=5000.0,
        exit_price=5008.0,
        gross_pnl=800.0,
        fees=10.0,
        net_pnl=790.0,
        mistakes=[],
        planned_stop=4996.0,
        planned_target=5012.0,
    )
    base.update(kw)
    async with get_sessionmaker()() as s:
        t = JournalTrade(**base)
        s.add(t)
        await s.commit()
        return t.id


async def reviewed(tid: int) -> JournalTrade:
    async with get_sessionmaker()() as s:
        t = await s.get(JournalTrade, tid)
        assert t is not None
        await review_trade(s, t, get_settings())
        await s.commit()
        return t


async def test_excursions_context_journal_and_scenarios() -> None:
    await seed(trade_path())
    t = await reviewed(await add_trade())
    # MAE: low 4997 vs entry 5000 = 3 pts x $50 x 2; MFE: highest high 5010.25 = 10.25 pts x $50 x 2
    assert t.mae == 300.0 and t.mfe == 1025.0
    ctx = t.context
    assert ctx["bars_during_trade"] == 20 and ctx["r_multiple"] == 2.0  # (5008-5000)/(5000-4996)
    assert ctx["market"]["available"] and ctx["market"]["trend"]["5m"] in ("up", "down", "flat", "unknown")
    j = t.review["journal"]
    for k in (
        "trade_summary",
        "entry_analysis",
        "market_conditions",
        "news_context",
        "risk_analysis",
        "execution_review",
        "outcome",
        "improvement_opportunities",
        "lesson",
    ):
        assert j[k], k
    assert "Long 2 ES (imported broker record)" in j["trade_summary"] and "+$790.00" in j["outcome"]
    assert "$400.00" in j["risk_analysis"]  # 4 pts x $50 x 2
    sc = {s["name"]: s for s in t.review["scenarios"]}
    assert all(s["hypothetical"] for s in sc.values())
    assert sc["Smaller position"]["pnl"] == round(8 * 50 * 1 - 5.0, 2)  # 1 contract, $2.50/side
    plan = sc["Followed the original plan"]
    # Target 5012 never traded through, stop 4996 never hit -> held to 15:55 ET flat at 5008 - 1 tick.
    assert plan["pnl"] == round((5007.75 - 5000) * 50 * 2 - 10.0, 2) and "flat by 15:55" in plan["detail"]
    assert plan["difference_vs_actual"] == round(plan["pnl"] - 790.0, 2)
    coach = t.review["coaching"]
    assert coach["did_well"] and "Outcome alone does not judge" in coach["outcome_note"]
    assert any("A stop was defined" in x for x in coach["did_well"])
    tl = [x["kind"] for x in t.review["timeline"]]
    assert tl.index("entry") < tl.index("exit")


async def test_news_attribution_uses_careful_language_and_measures_reaction() -> None:
    bars = trade_path()
    await seed(bars)
    async with get_sessionmaker()() as s:
        s.add(
            EconomicEvent(
                uid="cpi",
                ts=T_ENTRY + timedelta(minutes=4),
                title="Consumer Price Index",
                category="cpi",
                importance="high",
                affected=["ES", "NQ"],
                source="manual",
                created_at=T_ENTRY - timedelta(days=2),
            )
        )
        await s.commit()
    t = await reviewed(await add_trade())
    news = t.context["news"]
    ev = news["events"][0]
    assert ev["phase"] == "while_open" and ev["known_before_entry"] is True
    assert ev["reaction"]["direction"] == "up" and ev["direction_aligned"] is True
    assert ev["reaction"]["volatility_increased"] is True  # rally vs a flat prior hour
    assert "consistent with this observed reaction" in ev["description"]
    assert "not evidence of causation" in news["note"] and "cannot be assessed" in ev["forecast"]
    codes = {f["code"] for f in t.review["coaching"]["supported_mistakes"]}
    assert "before_news" in codes  # open through a high-impact event
    assert any(
        s["name"] == "Avoided the trade before a high-impact event" and s["pnl"] == 0.0 for s in t.review["scenarios"]
    )
    assert all(
        "caus" not in json.dumps(f).lower() or "not" in json.dumps(f).lower()
        for f in t.review["coaching"]["supported_mistakes"]
    )


async def test_coach_findings_are_evidence_based() -> None:
    await seed(trade_path())
    # Oversized, tight stop, poor R:R, exit beyond stop, quick re-entry after a loss with larger size.
    await add_trade(
        source_key="loss",
        entry_time=T_ENTRY - timedelta(minutes=40),
        exit_time=T_ENTRY - timedelta(minutes=5),
        net_pnl=-200.0,
        gross_pnl=-190.0,
        quantity=1,
    )
    # Risk to stop 4 pts x $50 x 6 = $1,200 (> half of $1,000); reward:risk 2/4 = 0.5; exit 4995 < stop 4996.
    tid = await add_trade(
        source_key="bad",
        quantity=6,
        planned_stop=4996.0,
        planned_target=5002.0,
        exit_price=4995.0,
        gross_pnl=-1500.0,
        net_pnl=-1530.0,
    )
    t = await reviewed(tid)
    f = {x["code"]: x for x in t.review["coaching"]["supported_mistakes"]}
    assert {"oversized", "poor_rr", "beyond_stop", "rapid_reentry"} <= set(f)
    assert "$1,200.00" in f["oversized"]["evidence"] and "0.50" in f["poor_rr"]["evidence"]
    tight = await reviewed(await add_trade(source_key="tight", planned_stop=4999.75, planned_target=5001.0))
    assert "tight_stop" in {x["code"] for x in tight.review["coaching"]["supported_mistakes"]}
    assert "cannot show intent" in f["rapid_reentry"]["evidence"]
    assert all(x["basis"].startswith("observed") for x in f.values())
    text = json.dumps(t.review).lower()
    import re

    for word in ("angry", "fear", "greed", "emotional", "frustrated"):
        assert not re.search(rf"\b{word}\b", text), word
    assert t.review["coaching"]["next_objective"].startswith("Exit at or before the planned stop")


async def test_missing_data_is_reported_not_invented() -> None:
    get_settings().market_data_provider = "simulated"  # no bars stored
    t = await reviewed(await add_trade(planned_stop=None, planned_target=None))
    assert t.mfe is None and t.mae is None
    assert t.context["market"]["available"] is False
    assert any(f["code"] == "no_stop" for f in t.review["coaching"]["supported_mistakes"])
    assert t.review["coaching"]["after_entry"] == "No stored bars cover the holding period."


def test_trade_assistant_assessment() -> None:
    plan = TradePlan(symbol="ES", direction="long", stop=4990.0, target=5020.0, quantity=2, status="executed_paper")
    pos = {"qty": 2, "avg_price": 5000.0, "opened_at": "x"}
    now = datetime(2026, 11, 11, 16, 0, tzinfo=UTC)
    ev = EconomicEvent(
        id=1,
        uid="e",
        ts=now + timedelta(minutes=10),
        title="FOMC",
        category="fomc",
        importance="high",
        affected=["ES"],
        source="manual",
    )
    msgs = {m["code"]: m for m in assess(pos, "ES", 4992.0, plan, None, None, [ev], now, 500.0)}
    assert "approaching" in msgs["news:1"]["text"] and "invalidation" in msgs["near_stop"]["text"]
    assert "risk" not in msgs  # (4992 - 4990) x $50 x 2 = $200, within the $500 budget
    msgs2 = {m["code"]: m for m in assess(pos, "ES", 5018.0, plan, None, None, [], now, 50.0)}
    assert "near_target" in msgs2 and msgs2["risk"]["severity"] == "critical"  # $2,800 to stop > $50
    assert all("nothing" not in m["text"] for m in msgs2.values())


async def test_plan_scenario_stops_at_end_of_stored_data() -> None:
    await seed(trade_path()[:-200])  # data ends well before 15:55 ET
    t = await reviewed(await add_trade())
    plan = next(s for s in t.review["scenarios"] if s["name"] == "Followed the original plan")
    assert "end of stored data" in plan["detail"]


# --- API -------------------------------------------------------------------------------------


async def test_live_trading_is_disabled_and_no_live_route_exists(admin_client: httpx.AsyncClient) -> None:
    st = (await admin_client.get("/api/execution/live-status")).json()
    assert st["live_trading_enabled"] is False and "not been verified" in st["reason"]
    assert any("Tradovate" in step for step in st["manual_workflow"])
    from app.main import create_app

    paths = set(create_app().openapi()["paths"])
    assert not any("live" in p and "order" in p for p in paths)
    assert not any(p.startswith("/api/broker") or "tradovate" in p for p in paths)


async def test_plan_to_paper_to_journal_to_review_lifecycle(admin_client: httpx.AsyncClient) -> None:
    from app.marketdata.calendar import is_market_open

    now = datetime.now(UTC)
    if not is_market_open(now):
        pytest.skip("market closed: the simulator rejects orders")
    await seed(flat_history(now - timedelta(days=4), now - timedelta(minutes=1)))
    await get_redis().set(
        STATUS_KEY,
        json.dumps(
            {
                "provider": "simulated",
                "quality": "simulated",
                "state": "streaming",
                "symbols": ["ES"],
                "last_event": {},
                "updated_at": now.timestamp(),
            }
        ),
    )
    r = await admin_client.post(
        "/api/plans",
        json={
            "symbol": "es",
            "direction": "long",
            "stop": 4990,
            "target": 5020,
            "quantity": 2,
            "strategy_id": "orb",
            "thesis": "Test thesis",
        },
        headers=CSRF,
    )
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["status"] == "draft" and plan["signal_snapshot"]["label"] in ("BUY", "SHORT", "WAIT", "NO TRADE")
    ex = await admin_client.post(f"/api/plans/{plan['id']}/execute-paper", headers=CSRF)
    assert ex.status_code == 200 and ex.json()["order"]["plan_id"] == plan["id"]
    assert (await admin_client.post(f"/api/plans/{plan['id']}/execute-paper", headers=CSRF)).status_code == 409
    # The next bar starts: market order fills at its open via the latest-update key.
    nxt = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    await get_redis().set(
        f"{LATEST_KEY}ES",
        json.dumps(
            {
                "symbol": "ES",
                "t": int(nxt.timestamp()),
                "o": 5000.0,
                "h": 5000.0,
                "l": 5000.0,
                "c": 5000.0,
                "v": 1,
                "provider": "simulated",
                "closed": False,
            }
        ),
    )
    async with get_sessionmaker()() as s:
        acct = await s.scalar(select(PaperAccount))
        await process(s, acct, nxt + timedelta(seconds=5))
        await s.commit()
        assert acct.state["positions"]["ES"]["qty"] == 2 and acct.state["positions"]["ES"]["plan_id"] == plan["id"]
    flat = (await admin_client.post("/api/paper/flatten", headers=CSRF)).json()
    assert flat["account"]["closed"][0]["plan_id"] == plan["id"]
    trades = (await admin_client.get("/api/journal/trades?source=paper")).json()["trades"]
    assert len(trades) == 1 and trades[0]["plan_id"] == plan["id"] and trades[0]["simulated"] is True
    detail = (await admin_client.get(f"/api/journal/trades/{trades[0]['id']}")).json()
    assert detail["review"]["journal"]["entry_analysis"] == "Test thesis"
    assert detail["planned_stop"] == 4990 and detail["strategy_id"] == "orb"
    assert detail["context"]["plan"]["id"] == plan["id"]
    assert trades[0]["slippage"] == 12.5 * 2  # 1 tick x $12.50 x 2 on the market entry; flatten adds none


async def test_manual_trade_entry_dedupe_validation_and_review(admin_client: httpx.AsyncClient) -> None:
    await seed(trade_path())
    body = {
        "symbol": "ES",
        "direction": "long",
        "quantity": 2,
        "entry_time": T_ENTRY.isoformat(),
        "exit_time": (T_ENTRY + timedelta(minutes=20)).isoformat(),
        "entry_price": 5000,
        "exit_price": 5008,
        "planned_stop": 4996,
        "planned_target": 5012,
    }
    r = await admin_client.post("/api/journal/trades", json=body, headers=CSRF)
    assert r.status_code == 201
    t = r.json()
    assert t["gross_pnl"] == 800 and t["fees"] == 10 and t["net_pnl"] == 790 and t["source"] == "manual"
    assert t["mfe"] == 1025 and t["review"]["journal"]["outcome"].startswith("Net +$790.00")
    assert (await admin_client.post("/api/journal/trades", json=body, headers=CSRF)).status_code == 409
    for bad in (
        {**body, "entry_price": 5000.1},
        {**body, "exit_time": (T_ENTRY - timedelta(minutes=1)).isoformat()},
        {**body, "entry_time": "2026-11-11T11:00:00"},
        {**body, "symbol": "ZZZ"},
    ):
        assert (await admin_client.post("/api/journal/trades", json=bad, headers=CSRF)).status_code == 422


async def test_replay_never_leaks_future_information(admin_client: httpx.AsyncClient) -> None:
    await seed(trade_path())
    async with get_sessionmaker()() as s:
        s.add(
            EconomicEvent(
                uid="late",
                ts=T_ENTRY + timedelta(minutes=10),
                title="Surprise statement",
                category="other",
                importance="high",
                affected=["ES"],
                source="manual",
                created_at=T_ENTRY + timedelta(minutes=9),
            )
        )
        s.add(
            EconomicEvent(
                uid="sched",
                ts=T_ENTRY + timedelta(minutes=5),
                title="CPI",
                category="cpi",
                importance="high",
                affected=["ES"],
                source="manual",
                created_at=T_ENTRY - timedelta(days=3),
            )
        )
        await s.commit()
    tid = await add_trade()
    rp = (await admin_client.get(f"/api/journal/trades/{tid}/replay")).json()
    times = [b["t"] for b in rp["bars"]]
    assert times == sorted(times) and times[0] >= int((T_ENTRY - timedelta(minutes=90)).timestamp())
    assert rp["entry"]["t"] == int(T_ENTRY.timestamp()) and rp["stop"] == 4996 and rp["target"] == 5012
    by = {e["title"]: e for e in rp["events"]}
    # A scheduled event is known before it happens; an unscheduled one only from when it was recorded.
    assert by["CPI"]["known_from"] < rp["entry"]["t"] and by["Surprise statement"]["known_from"] > rp["entry"]["t"]
    assert rp["hindsight"] is not None and rp["scenarios"] is not None  # separate fields for the client


async def test_overlays_and_label(admin_client: httpx.AsyncClient) -> None:
    empty = (await admin_client.get("/api/market/overlays?symbol=ES&timeframe=5m")).json()
    assert empty["label"]["label"] == "NO TRADE" and empty["ema20"] == []
    now = datetime.now(UTC)
    await seed(flat_history(now - timedelta(days=4), now - timedelta(minutes=1)))
    await get_redis().set(
        "apex:scan:latest",
        json.dumps(
            {
                "quality": "simulated",
                "symbols": [
                    {
                        "symbol": "ES",
                        "regime": "range",
                        "regime_definition": "flat",
                        "levels": {"prior_day_high": 5001.0, "opening_range_complete": True},
                    }
                ],
            }
        ),
    )
    async with get_sessionmaker()() as s:
        s.add(
            EconomicEvent(
                uid="cpi2",
                ts=now - timedelta(hours=2),
                title="CPI",
                category="cpi",
                importance="high",
                affected=["ES"],
                source="manual",
                source_ref="bls.ics",
            )
        )
        await s.commit()
    o = (await admin_client.get("/api/market/overlays?symbol=ES&timeframe=5m")).json()
    assert o["ema20"] and o["ema50"] and o["label"]["label"] == "WAIT"
    assert o["levels"] == {"prior_day_high": 5001.0}  # booleans are not price levels
    ev = o["events"][0]
    bar_times = {p["t"] for p in o["ema20"]}
    assert ev["t"] in bar_times or ev["t"] <= max(bar_times)
    assert "no directional forecast" in ev["bias"] and ev["source_ref"] == "bls.ics" and ev["detected_at"]
    assert (await admin_client.get("/api/market/overlays?symbol=ZZZ")).status_code == 404


async def test_ai_review_is_grounded_in_the_record(admin_client: httpx.AsyncClient) -> None:
    from app.api.routes.copilot import get_copilot_client
    from app.main import create_app

    await seed(trade_path())
    tid = await add_trade()
    seen: list[dict] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(req.content))
        return httpx2.Response(
            200,
            json={
                "id": "m",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": "**What went well**\n- Stop defined."}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    app = create_app()
    get_settings().anthropic_api_key = SecretStr("k")
    app.dependency_overrides[get_copilot_client] = lambda: AsyncAnthropic(
        api_key="k", http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    )
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    name = get_settings().cookie_name
    c.cookies.set(name, admin_client.cookies.get(name))
    r = await c.post(f"/api/journal/trades/{tid}/ai-review", headers=CSRF)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["ai"]["text"].startswith("**What went well**")
    req = seen[0]
    record = json.loads(req["messages"][0]["content"])
    assert record["trade"]["entry_price"] == 5000.0 and record["findings"] is not None
    assert "tools" not in req and "Do not diagnose emotions" in req["system"][0]["text"]
    await c.aclose()


async def test_viewers_cannot_plan_or_enter_trades(admin_client: httpx.AsyncClient) -> None:
    from app.core.security import hash_password
    from app.models.user import User

    async with get_sessionmaker()() as s:
        s.add(User(email="v8@example.com", password_hash=hash_password("Viewer-Pass-42!"), role="viewer"))
        await s.commit()
    admin_client.cookies.clear()
    await admin_client.post("/api/auth/login", json={"email": "v8@example.com", "password": "Viewer-Pass-42!"})
    assert (
        await admin_client.post(
            "/api/plans", json={"symbol": "ES", "direction": "long", "stop": 1, "quantity": 1}, headers=CSRF
        )
    ).status_code == 403
    assert (await admin_client.post("/api/journal/trades", json={}, headers=CSRF)).status_code == 403
    assert (await admin_client.get("/api/execution/live-status")).status_code == 200


async def test_analytics_news_adherence_and_sample_size(admin_client: httpx.AsyncClient) -> None:
    await seed(trade_path())
    for i in range(3):
        await reviewed(
            await add_trade(
                source_key=f"a{i}", entry_time=T_ENTRY + timedelta(seconds=i), exit_price=4995.0 if i == 0 else 5008.0
            )
        )
    a = (await admin_client.get("/api/analytics?start=2026-11-01&end=2026-11-30")).json()
    assert a["overall"]["trades"] == 3 and a["overall"]["low_sample"] is True
    assert "highly uncertain" in a["sample_size_note"]
    assert a["average_planned_rr"] == {"value": 3.0, "trades_with_plan": 3}  # (5012-5000)/(5000-4996)
    assert a["rule_adherence"]["exits_beyond_stop"] == 1 and a["rule_adherence"]["stop_respected_rate"] == round(
        2 / 3, 3
    )
    assert a["by_news"][0]["key"] == "no recorded event"
    assert any(f["code"] == "beyond_stop" for f in a["common_findings"])
    out = (await admin_client.get("/api/analytics?start=2026-12-01")).json()
    assert out["overall"]["trades"] == 0
