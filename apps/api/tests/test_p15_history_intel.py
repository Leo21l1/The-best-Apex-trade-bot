"""Historical coverage, approved/cost-gated backfill, volatility and regime intelligence."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.marketdata.store import upsert_candles
from app.models.candle import CandleRow as Candle
from app.research.bars import Bar
from app.services import history
from app.services.market_intel import regime_profile, volatility_profile
from tests.conftest import CSRF

NOW = datetime(2026, 11, 13, 16, 0, tzinfo=UTC)  # Friday 11:00 ET


async def seed(days: int) -> None:
    get_settings().market_data_provider = "simulated"
    p = SimulatedProvider(clock=lambda: NOW)
    async with get_sessionmaker()() as s:
        await upsert_candles(s, await p.fetch_candles("ES", NOW - timedelta(days=days), NOW))
        await s.commit()


def test_expected_sessions_skip_weekends_and_holidays() -> None:
    d = history.expected_sessions("ES", date(2026, 11, 30), 5)
    assert len(d) == 5 and all(x.weekday() < 5 for x in d)
    assert date(2026, 12, 25) not in history.expected_sessions("ES", date(2026, 12, 31), 8)  # Christmas


async def test_coverage_counts_sessions_and_readiness() -> None:
    await seed(9)
    async with get_sessionmaker()() as s:
        cov = await history.coverage(s, "ES", now=NOW, target=25)
    assert 5 <= cov["sessions_stored"] < 20
    assert cov["relative_volume_readiness"] == "preliminary" and cov["missing_sessions"]
    assert cov["earliest_bar"] and cov["latest_bar"] and cov["bars_total"] > 1000


async def test_coverage_without_provider_is_unavailable() -> None:
    get_settings().market_data_provider = "none"
    async with get_sessionmaker()() as s:
        cov = await history.coverage(s, "ES", now=NOW)
    assert cov["status"] == "UNAVAILABLE"


class FakeProvider:
    def __init__(self) -> None:
        self.inner = SimulatedProvider(clock=lambda: NOW)
        self.calls: list[tuple[datetime, datetime]] = []

    async def fetch_candles(self, symbol: str, start: datetime, end: datetime) -> Any:
        self.calls.append((start, end))
        return await self.inner.fetch_candles(symbol, start, min(end, NOW))


async def test_backfill_step_fetches_only_missing_and_never_duplicates() -> None:
    await seed(4)
    await history.start_job(["ES"], 8, 0.0, "admin")
    prov = FakeProvider()
    async with get_sessionmaker()() as s:
        before = (await history.coverage(s, "ES", now=NOW, target=8))["sessions_stored"]
        assert await history.run_job_step(s, prov, max_sessions=2)
    assert len(prov.calls) == 2
    job = await history.get_job()
    assert job and job["progress"]["ES"]["fetched_sessions"] == 2
    async with get_sessionmaker()() as s:
        while await history.run_job_step(s, prov, max_sessions=3):
            pass
        n1 = await s.scalar(select(func.count()).select_from(Candle))
        job = await history.get_job()
        assert job and job["status"] == "done"
        stored = {d for d in history.expected_sessions("ES", date(2026, 11, 16), 8)} - {
            date.fromisoformat(x) for x in (await history.coverage(s, "ES", target=8))["missing_sessions"]
        }
        # a new job re-requests only sessions still missing (here: after the provider's data end), never stored ones
        await history.start_job(["ES"], 8, 0.0, "admin")
        calls = len(prov.calls)
        await history.run_job_step(s, prov)
        n2 = await s.scalar(select(func.count()).select_from(Candle))
    fetched = {st.astimezone(UTC).date() + timedelta(days=1) for st, _ in prov.calls[calls:]}
    assert n1 == n2 and not (fetched & stored)
    assert job["progress"]["ES"]["sessions_stored"] > before


async def test_backfill_api_requires_admin_and_cost_approval(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(3)

    async def priced(session: Any, symbols: list[str], target: int = 25) -> dict[str, Any]:
        return {"provider": "databento", "items": [], "estimated_cost_usd": 1.25}

    monkeypatch.setattr(history, "estimate", priced)
    body = {"symbols": ["ES"], "target_sessions": 10}
    r = await admin_client.post("/api/data/backfill", json=body, headers=CSRF)
    assert r.status_code == 412 and "1.25" in r.json()["detail"]
    r = await admin_client.post("/api/data/backfill", json={**body, "approved_cost_usd": 1.0}, headers=CSRF)
    assert r.status_code == 412
    assert await history.get_job() is None  # nothing started without approval
    r = await admin_client.post("/api/data/backfill", json={**body, "approved_cost_usd": 1.25}, headers=CSRF)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    again = await admin_client.post("/api/data/backfill", json={**body, "approved_cost_usd": 2}, headers=CSRF)
    assert again.status_code == 409

    async def unknown(session: Any, symbols: list[str], target: int = 25) -> dict[str, Any]:
        return {"provider": "databento", "items": [], "estimated_cost_usd": None}

    monkeypatch.setattr(history, "estimate", unknown)
    await history.start_job(["ES"], 10, 0, "x")
    from app.core.redis import get_redis

    await get_redis().delete(history.JOB_KEY)
    r = await admin_client.post("/api/data/backfill", json=body, headers=CSRF)
    assert r.status_code == 412 and "acknowledge" in r.json()["detail"]
    bad = await admin_client.post("/api/data/backfill", json={"symbols": ["ZZZ"]}, headers=CSRF)
    assert bad.status_code == 422


async def test_readiness_endpoint(admin_client: httpx.AsyncClient) -> None:
    await seed(6)
    r = (await admin_client.get("/api/data/readiness")).json()
    es = next(x for x in r["symbols"] if x["symbol"] == "ES")
    assert es["sessions_stored"] >= 1 and "missing_sessions" in es
    assert r["live_feed"]["class"] in ("UNAVAILABLE", "SIMULATED", "LIVE", "DELAYED", "HISTORICAL")
    assert "HISTORICAL" in r["labels"]


async def test_estimate_simulated_is_free_and_downloads_nothing() -> None:
    await seed(3)
    async with get_sessionmaker()() as s:
        before = await s.scalar(select(func.count()).select_from(Candle))
        est = await history.estimate(s, ["ES"], 10)
        after = await s.scalar(select(func.count()).select_from(Candle))
    assert est["estimated_cost_usd"] == 0 and before == after and est["items"][0]["missing_sessions"]


def _bars(days: int, rng_today: float, rng_prior: float) -> list[Bar]:
    out: list[Bar] = []
    d0 = datetime(2026, 11, 13, tzinfo=UTC)
    k = 0
    for back in range(days, -1, -1):
        day = d0 - timedelta(days=back)
        if day.weekday() >= 5:
            continue
        r = rng_today if back == 0 else rng_prior
        start = day.replace(hour=14, minute=0)  # 09:00 ET
        for i in range(120):
            ts = start + timedelta(minutes=i)
            if ts >= NOW:
                break
            p = 5000 + (i % 7) * 0.25
            out.append(Bar(ts, p, p + r, p - r, p, 100))
            k += 1
    return out


def test_volatility_profile_flags_unusual_expansion() -> None:
    v = volatility_profile(_bars(30, 4.0, 1.0), NOW)
    assert v["available"] and v["sessions"] >= 20 and v["data_quality"] == "robust"
    assert v["session_range_percentile"] == 100 and v["level"] in ("unusually high", "high")
    assert v["opening_range"] and v["typical_opening_range"]


def test_volatility_profile_insufficient_history() -> None:
    v = volatility_profile(_bars(3, 1.0, 1.0), NOW)
    assert v["data_quality"] == "insufficient" and v["atr_percentile_same_time"] is None and "note" in v


def test_regime_trend_vs_chop() -> None:
    t0 = NOW - timedelta(hours=4)
    trend = [Bar(t0 + timedelta(minutes=i), 5000 + i, 5000.5 + i, 4999.5 + i, 5000 + i, 100) for i in range(240)]
    r = regime_profile(trend, NOW, structure_trend="bullish")
    assert "bullish trend" in r["labels"] and r["evidence"]["efficiency_ratio_2h"] > 0.9
    chop = [
        Bar(t0 + timedelta(minutes=i), p, p + 0.5, p - 0.5, p, 100)
        for i, p in ((i, 5000 + (1 if (i // 5) % 2 else -1)) for i in range(240))
    ]
    c = regime_profile(chop, NOW, vwap_crosses_today=6)
    assert "choppy" in c["labels"] and "bullish trend" not in c["labels"]
    assert regime_profile(trend[:20], NOW)["labels"] == ["unknown"]


def test_volatility_in_the_evening_compares_the_same_session_time_not_the_previous_close() -> None:
    """Regression: at 18:40 ET (start of a CME trading day) prior sessions were compared at 16:59,
    so 'typical range by now' was a whole day's range and the ATR reference was the prior close."""
    out: list[Bar] = []
    for back in range(8, -1, -1):
        day = datetime(2026, 11, 13, tzinfo=UTC) - timedelta(days=back)  # evening of `day` starts a session
        if day.weekday() >= 5 or day.weekday() == 4:
            continue  # no Friday-evening or weekend sessions
        ev = day.replace(hour=23, minute=0)  # 18:00 ET (EST)
        for i in range(60):  # quiet evening: range 1.0
            out.append(Bar(ev + timedelta(minutes=i), 5000, 5000.5, 4999.5, 5000, 50))
        if back:
            nxt = day + timedelta(days=1)
            for i in range(400):  # busy next morning/day: range 10
                ts = nxt.replace(hour=14, minute=0) + timedelta(minutes=i)
                out.append(Bar(ts, 5000, 5005, 4995, 5000, 500))
    now = datetime(2026, 11, 12, 23, 41, tzinfo=UTC)  # Thursday 18:41 ET
    v = volatility_profile([b for b in out if b.ts < now], now, min_sessions=2)
    assert v["typical_range_by_now"] == 1.0  # the evening range, not the 10-point day range
    assert v["session_range_so_far"] == 1.0
