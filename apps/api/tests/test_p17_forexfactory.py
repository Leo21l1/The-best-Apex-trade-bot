"""ForexFactory calendar export: parsing, impact mapping, idempotent upsert, news exposure, API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select

from app.core.db import get_sessionmaker
from app.models.calendar import EconomicEvent
from app.services.calendar import news_exposure, parse_forexfactory, upsert_ff_events
from tests.conftest import CSRF

# Test fixture in the export's shape (not real calendar data).
SAMPLE = [
    {
        "title": "CPI m/m",
        "country": "USD",
        "date": "2026-11-18T08:30:00-05:00",
        "impact": "High",
        "forecast": "0.3%",
        "previous": "0.2%",
    },
    {
        "title": "Crude Oil Inventories",
        "country": "USD",
        "date": "2026-11-18T10:30:00-05:00",
        "impact": "Medium",
        "forecast": "",
        "previous": "-1.2M",
    },
    {"title": "Some Minor Index", "country": "USD", "date": "2026-11-18T11:00:00-05:00", "impact": "Low"},
    {"title": "Thanksgiving Day", "country": "USD", "date": "2026-11-26T00:00:00-05:00", "impact": "Holiday"},
    {"title": "ECB Press Conference", "country": "EUR", "date": "2026-11-18T08:45:00-05:00", "impact": "High"},
    {"title": "Broken", "country": "USD", "date": "not a date", "impact": "High"},
]


def test_parse_keeps_usd_impact_events_and_reports_bad_items() -> None:
    ev, errors = parse_forexfactory(SAMPLE)
    assert [e.title for e in ev] == ["CPI m/m", "Crude Oil Inventories", "Some Minor Index"]
    assert ev[0].impact == "high" and ev[0].ts == datetime(2026, 11, 18, 13, 30, tzinfo=UTC)
    assert ev[0].forecast == "0.3%" and ev[1].forecast is None
    assert len(errors) == 1 and "bad date" in errors[0]
    both, _ = parse_forexfactory(SAMPLE, ["USD", "EUR"])
    assert any(e.currency == "EUR" for e in both)
    assert parse_forexfactory({"x": 1})[1]


async def test_upsert_is_idempotent_and_drives_news_exposure() -> None:
    ev, _ = parse_forexfactory(SAMPLE, ["USD", "EUR"])
    async with get_sessionmaker()() as s:
        await upsert_ff_events(s, ev, "test")
        await upsert_ff_events(s, ev, "test")  # second sync of the same week: no duplicates
        await s.commit()
        assert await s.scalar(select(func.count()).select_from(EconomicEvent)) == 4
        cpi = await s.scalar(select(EconomicEvent).where(EconomicEvent.title == "CPI m/m"))
        assert cpi and cpi.importance == "high" and "ES" in cpi.affected and cpi.source == "forexfactory"
        oil = await s.scalar(select(EconomicEvent).where(EconomicEvent.title == "Crude Oil Inventories"))
        assert oil and oil.affected == ["CL", "MCL"]
        ecb = await s.scalar(select(EconomicEvent).where(EconomicEvent.country == "EUR"))
        assert ecb and ecb.title.startswith("EUR ") and "ES" not in ecb.affected
        at = datetime(2026, 11, 18, 13, 25, tzinfo=UTC)  # 08:25 ET, 5 min before CPI
        hit = await news_exposure(s, "ES", at, 15, 15)
        assert hit["status"] == "inside_window" and "CPI" in hit["summary"]
        clear = await news_exposure(s, "ES", at + timedelta(hours=5), 15, 15)
        assert clear["status"] == "clear"


async def test_api_upload_requires_admin(admin_client: httpx.AsyncClient, client: httpx.AsyncClient) -> None:
    r = await admin_client.post("/api/calendar/sync-forexfactory", json={"events": SAMPLE}, headers=CSRF)
    assert r.status_code == 200 and r.json()["events"] == 3 and r.json()["high"] == 1
    cal = (await admin_client.get("/api/calendar", params={"days_ahead": 14})).json()
    assert cal["empty_calendar"] is False


async def test_forecast_and_previous_published_later_reach_stored_events() -> None:
    """Regression: an event first synced before ForexFactory filled in forecast/previous kept them blank."""
    early = [{**SAMPLE[0], "forecast": "", "previous": ""}]
    ev, _ = parse_forexfactory(early)
    async with get_sessionmaker()() as s:
        await upsert_ff_events(s, ev, "test")
        await s.commit()
        cpi = await s.scalar(select(EconomicEvent).where(EconomicEvent.title == "CPI m/m"))
        assert cpi and cpi.forecast is None and cpi.previous is None
        later, _ = parse_forexfactory(SAMPLE[:1])
        await upsert_ff_events(s, later, "test")
        await s.commit()
        await s.refresh(cpi)
        assert cpi.forecast == "0.3%" and cpi.previous == "0.2%"
        assert await s.scalar(select(func.count()).select_from(EconomicEvent)) == 1
