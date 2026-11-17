"""SMC analysis, confluence, liquidity map and scanner endpoints on stored (simulated) bars."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.marketdata.calendar import is_market_open
from app.marketdata.types import Candle, DataQuality, Timeframe


async def seed_walk(symbol: str = "ES", days: int = 3) -> None:
    from app.marketdata.store import upsert_candles

    get_settings().market_data_provider = "simulated"
    rnd = random.Random(7)  # noqa: S311 - deterministic test data
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    t, p, out = now - timedelta(days=days), 5000.0, []
    while t < now:
        if is_market_open(t):
            o = p
            c = round((o + rnd.gauss(0, 1.0)) * 4) / 4
            h = round((max(o, c) + rnd.random()) * 4) / 4
            low = round((min(o, c) - rnd.random()) * 4) / 4
            out.append(
                Candle(
                    symbol,
                    Timeframe.M1,
                    t,
                    *(Decimal(str(x)) for x in (o, h, low, c)),
                    500,
                    "simulated",
                    DataQuality.SIMULATED,
                )
            )
            p = c
        t += timedelta(minutes=1)
    async with get_sessionmaker()() as s:
        await upsert_candles(s, out)
        await s.commit()


async def test_analysis_returns_explained_patterns(admin_client: httpx.AsyncClient) -> None:
    await seed_walk()
    r = await admin_client.get("/api/smc/analysis", params={"symbol": "ES", "timeframe": "5m"})
    assert r.status_code == 200
    a = r.json()
    assert a["symbol"] == "ES" and a["timeframe"] == "5m" and a["bars"] > 50
    assert a["patterns"], "a random walk always produces some swings/structure"
    for p in a["patterns"]:
        for k in (
            "id",
            "type",
            "direction",
            "formed_at",
            "confirmed_at",
            "top",
            "bottom",
            "status",
            "invalidation",
            "evidence",
        ):
            assert k in p
    assert a["disclaimer"].startswith("Patterns are rule-based")
    hq = await admin_client.get("/api/smc/analysis", params={"symbol": "ES", "timeframe": "5m", "min_quality": 70})
    assert all(p["quality"] >= 70 for p in hq.json()["patterns"])
    assert (await admin_client.get("/api/smc/analysis", params={"symbol": "ZZ"})).status_code == 404
    assert (await admin_client.get("/api/smc/analysis", params={"symbol": "ES", "timeframe": "1w"})).status_code == 422


async def test_confluence_liquidity_and_scan(admin_client: httpx.AsyncClient) -> None:
    await seed_walk()
    c = (await admin_client.get("/api/smc/confluence", params={"symbol": "ES"})).json()
    assert c["outcome"] in ("BUY BIAS", "SHORT BIAS", "WAIT", "NO TRADE")
    assert [f["family"] for f in c["families"]] == ["context", "location", "trigger"]
    assert c["validation"] == "RESEARCH ONLY"
    assert set(c["timeframes"]) == {"1m", "5m", "15m", "1h"}
    if c["plan"]:
        assert c["plan"]["stop"] and c["plan"]["entry_zone"] and "invalidation" in c["plan"]
    liq = (await admin_client.get("/api/smc/liquidity", params={"symbol": "ES"})).json()
    assert "not verified resting liquidity" in liq["label"]
    assert all(x["price"] > liq["price"] for x in liq["nearest_above"])
    assert all(x["price"] < liq["price"] for x in liq["nearest_below"])
    scan = (await admin_client.get("/api/smc/scan", params={"refresh": True})).json()
    # Simulated data is never eligible for ranking.
    assert scan["ranked"] == []
    es = next(x for x in scan["watch"] if x["symbol"] == "ES")
    assert any("not eligible for ranking" in r for r in es["not_ranked_because"])


async def test_no_data_gives_no_trade(admin_client: httpx.AsyncClient) -> None:
    get_settings().market_data_provider = "simulated"
    c = (await admin_client.get("/api/smc/confluence", params={"symbol": "NQ"})).json()
    assert c["outcome"] == "NO TRADE" and "No current market data." in c["gates"]


async def test_fresh_high_impact_news_forces_no_trade(admin_client: httpx.AsyncClient) -> None:
    from app.models.calendar import NewsItem

    await seed_walk()
    now = datetime.now(UTC)
    async with get_sessionmaker()() as s:
        s.add(
            NewsItem(
                uid="fed:x",
                source="fed",
                source_name="FRB",
                credibility="official",
                title="FOMC statement",
                link=None,
                summary=None,
                categories=[],
                published_at=now,
                detected_at=now - timedelta(minutes=2),
                impact="CRITICAL",
                impact_reason="FOMC policy decision",
                instruments=["ES"],
                cluster="fed:x",
            )
        )
        await s.commit()
    c = (await admin_client.get("/api/smc/confluence", params={"symbol": "ES"})).json()
    assert c["outcome"] == "NO TRADE" and any("Fresh published news" in g for g in c["gates"])
