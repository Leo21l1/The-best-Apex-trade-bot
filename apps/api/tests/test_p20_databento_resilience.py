"""Databento timeouts and retries, cost-estimate failures and backfill charge safety."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.adapters.marketdata.base import ProviderAuthError, ProviderError
from app.adapters.marketdata.databento import DatabentoProvider
from app.adapters.marketdata.simulated import SimulatedProvider
from app.core.config import Settings, get_settings
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.marketdata.factory import build_provider
from app.models.candle import CandleRow
from app.services import history
from tests.conftest import CSRF

KEY = "db-SECRET-key-123456789"
NOW = datetime(2026, 11, 11, 15, 30, tzinfo=UTC)


def provider(handler: Any, retries: int = 3) -> tuple[DatabentoProvider, list[float], dict[str, int]]:
    sleeps: list[float] = []
    calls: dict[str, int] = {}

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    async def route(req: httpx.Request) -> httpx.Response:
        calls[req.url.path] = calls.get(req.url.path, 0) + 1
        if req.url.path == "/v0/metadata.get_dataset_range":
            return httpx.Response(200, json={"start": "2010-01-01T00:00:00Z", "end": NOW.isoformat()})
        return handler(req, calls[req.url.path])

    client = httpx.AsyncClient(transport=httpx.MockTransport(route), base_url="https://hist.databento.test")
    p = DatabentoProvider(
        KEY, client=client, min_interval_s=0, sleep=fake_sleep, clock=lambda: NOW, max_retries=retries
    )
    return p, sleeps, calls


COST = "/v0/metadata.get_cost"
RANGE = "/v0/timeseries.get_range"
PARAMS = {"dataset": "GLBX.MDP3", "symbols": "ES.v.0"}


def _frozen(at: datetime) -> type[datetime]:
    class Frozen(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return at if tz is not None else at.replace(tzinfo=None)

    return Frozen


async def test_free_metadata_timeout_is_retried_with_backoff() -> None:
    def handler(req: httpx.Request, n: int) -> httpx.Response:
        if n < 3:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200, text="0.25")

    p, sleeps, calls = provider(handler)
    r = await p._get(COST, PARAMS)
    assert r.text == "0.25" and calls[COST] == 3 and sleeps == [1.0, 2.0]


async def test_retries_are_bounded_and_never_infinite(caplog: pytest.LogCaptureFixture) -> None:
    def always(req: httpx.Request, n: int) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    caplog.set_level(logging.WARNING)
    p, sleeps, calls = provider(always)
    with pytest.raises(ProviderError, match="failed after 4 attempts: ReadTimeout"):
        await p._get(COST, PARAMS)
    assert calls[COST] == 4 and sleeps == [1.0, 2.0, 4.0]
    p, _s, calls = provider(always, retries=99)  # capped
    with pytest.raises(ProviderError):
        await p._get(COST, PARAMS)
    assert calls[COST] == 7
    assert KEY not in caplog.text


async def test_billed_download_is_not_retried_after_it_reached_databento() -> None:
    def handler(req: httpx.Request, n: int) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    p, _s, calls = provider(handler)
    with pytest.raises(ProviderError, match="may be billed"):
        await p.fetch_candles("ES", NOW - timedelta(hours=1), NOW)
    assert calls[RANGE] == 1


async def test_billed_download_retried_only_when_never_sent() -> None:
    def handler(req: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            raise httpx.ConnectError("refused", request=req)
        return httpx.Response(200, text="")

    p, sleeps, calls = provider(handler)
    assert await p.fetch_candles("ES", NOW - timedelta(hours=1), NOW) == []
    assert calls[RANGE] == 2 and sleeps == [1.0]


async def test_server_errors_retried_for_metadata_and_auth_errors_never() -> None:
    def flaky(req: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(503) if n == 1 else httpx.Response(200, text="1.5")

    p, _s, calls = provider(flaky)
    assert (await p._get(COST, PARAMS)).text == "1.5" and calls[COST] == 2

    def denied(req: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(401, json={"detail": f"bad key {KEY}"})

    p, sleeps, calls = provider(denied)
    with pytest.raises(ProviderAuthError) as exc:
        await p._get(COST, PARAMS)
    assert calls[COST] == 1 and not sleeps and KEY not in str(exc.value)


def test_timeout_and_retries_are_configurable() -> None:
    s = Settings(
        market_data_provider="databento",
        databento_api_key="x" * 20,
        databento_timeout_seconds=120,
        databento_max_retries=2,
    )
    p = build_provider(s)
    assert p._timeout_s == 120 and p._max_retries == 2
    with pytest.raises(ValueError):
        Settings(databento_timeout_seconds=1)
    with pytest.raises(ValueError):
        Settings(databento_max_retries=50)


def test_cost_requests_are_split_into_contiguous_ranges() -> None:
    sessions = history.expected_sessions("ES", date(2026, 11, 13), 25)
    rs = history._ranges("ES", sessions)
    assert [len(r[0]) for r in rs] == [10, 10, 5] and sum(len(r[0]) for r in rs) == 25
    holes = sessions[:5] + sessions[8:12]  # stored sessions in between must not be estimated
    assert [len(r[0]) for r in history._ranges("ES", holes)] == [5, 4]


def _cost_provider(handler: Any) -> DatabentoProvider:
    p, _s, _c = provider(handler)
    return p


async def test_estimate_reports_progress_and_sums_ranges(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pin the service clock to the test's NOW (the fake provider's data ends there); otherwise the
    # expected sessions drift past the provider's data end as real time passes.
    monkeypatch.setattr(history, "datetime", _frozen(NOW))
    get_settings().market_data_provider = "databento"
    msgs: list[str] = []
    p = _cost_provider(lambda req, n: httpx.Response(200, text="0.05"))
    async with get_sessionmaker()() as s:
        est = await history.estimate(s, ["ES"], 25, progress=msgs.append, provider=p)
    it = est["items"][0]
    assert est["estimate_failed"] is False and it["requests"] == 3 and est["estimated_cost_usd"] == 0.15
    assert any("range 1/3" in m for m in msgs) and any("missing sessions" in m for m in msgs)


async def test_failed_estimate_is_never_zero_and_blocks_every_start(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import app.marketdata.factory as factory
    from app.cli import backfill_cmd

    def slow(req: httpx.Request, n: int) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    s = get_settings()
    s.market_data_provider = "databento"
    async with get_sessionmaker()() as session:
        est = await history.estimate(session, ["ES", "NQ"], 10, provider=_cost_provider(slow))
    assert est["estimate_failed"] is True and est["estimated_cost_usd"] is None
    assert all(i["estimated_cost_usd"] is None for i in est["items"]) and "ReadTimeout" in est["errors"][0]

    monkeypatch.setattr(factory, "build_provider", lambda _s: _cost_provider(slow))
    r = await admin_client.post(
        "/api/data/backfill",
        headers=CSRF,
        json={"symbols": ["ES"], "target_sessions": 10, "approved_cost_usd": 1000, "acknowledge_unknown_cost": True},
    )
    assert r.status_code == 503 and "nothing was started" in r.json()["detail"]
    assert await backfill_cmd(["ES"], 10, 1000.0, True) == 3
    out = capsys.readouterr().out
    assert "ESTIMATE FAILED" in out and "Traceback" not in out and KEY not in out
    assert await history.get_job() is None  # nothing queued by either path


class CountingProvider:
    """Simulated bars; counts downloads per session and can fail once to emulate an interruption."""

    def __init__(self, fail_on: int | None = None) -> None:
        self.inner = SimulatedProvider(clock=lambda: datetime(2026, 11, 14, tzinfo=UTC))
        self.calls: list[datetime] = []
        self.fail_on = fail_on

    async def fetch_candles(self, symbol: str, start: datetime, end: datetime) -> Any:
        self.calls.append(start)
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise ProviderError("Databento download failed: ReadTimeout (may be billed)")
        return await self.inner.fetch_candles(symbol, start, end)


async def test_backfill_is_resumable_limited_to_the_plan_and_never_repeats(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings().market_data_provider = "simulated"
    clock = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(history, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: clock)}))
    async with get_sessionmaker()() as s:
        cov = await history.coverage(s, "ES", now=clock, target=6)
    plan = {"ES": cov["missing_sessions"][:4]}  # approve 4 of the 6 missing sessions only
    await history.start_job(["ES"], 6, 0.0, "test", plan=plan)
    prov = CountingProvider(fail_on=2)
    async with get_sessionmaker()() as s:
        assert await history.run_job_step(s, prov, max_sessions=2)  # one stored, one fails (interruption)
        job = await history.get_job()
        assert job and len(job["progress"]["ES"]["attempted"]) == 2 and job["errors"]
        while await history.run_job_step(s, prov, max_sessions=2):  # resume
            pass
        n1 = await s.scalar(select(func.count()).select_from(CandleRow))
    job = await history.get_job()
    assert job and job["status"] == "done" and len(prov.calls) == 4  # each approved session requested once
    fetched = await get_redis().smembers(f"{history.FETCHED_KEY}simulated:ES")
    assert len(fetched) == 3  # the failed one is not marked as downloaded
    # A new job for the same target never re-downloads stored or already-downloaded sessions.
    await history.start_job(["ES"], 6, 0.0, "test", plan={"ES": cov["missing_sessions"]})
    before = len(prov.calls)
    async with get_sessionmaker()() as s:
        while await history.run_job_step(s, prov, max_sessions=5):
            pass
        n2 = await s.scalar(select(func.count()).select_from(CandleRow))
    downloaded = prov.calls[before:]
    from app.marketdata.calendar import trading_day

    names = {x.decode() if isinstance(x, bytes) else x for x in fetched}
    assert downloaded and all(trading_day(d).isoformat() not in names for d in downloaded)
    assert n2 >= n1  # upserts are idempotent: no duplicate bars
