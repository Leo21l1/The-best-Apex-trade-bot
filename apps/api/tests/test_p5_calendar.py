from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.models.calendar import EconomicEvent
from app.models.signal import AlertRow
from app.research.scanner import ScannerService
from app.services.calendar import classify, exposure, news_exposure, parse_ics, upsert_events
from tests.conftest import CSRF

ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//BLS//Release Schedule//EN\r\n"
    "BEGIN:VEVENT\r\nUID:cpi-2026-12@bls.gov\r\nDTSTART;TZID=America/New_York:20261210T083000\r\n"
    "SUMMARY:Consumer Price Index for Novem\r\n ber 2026\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:nfp-2026-12@bls.gov\r\nDTSTART:20261204T133000Z\r\n"
    "SUMMARY:The Employment Situation\\, November 2026\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:allday@bls.gov\r\nDTSTART;VALUE=DATE:20261211\r\n"
    "SUMMARY:Producer Price Index\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:broken@bls.gov\r\nSUMMARY:No start time\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nDTSTART:20261216T190000Z\r\nSUMMARY:FOMC Statement and Rate Decision\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_parse_ics_handles_folding_timezones_escapes_and_errors() -> None:
    events, errors = parse_ics(ICS)
    by = {e.title.split(" (")[0]: e for e in events}
    assert len(events) == 4 and len(errors) == 1 and "missing SUMMARY or DTSTART" in errors[0]
    cpi = by["Consumer Price Index for November 2026"]  # folded line rejoined
    assert cpi.ts == datetime(2026, 12, 10, 13, 30, tzinfo=UTC)  # 08:30 EST
    assert by["The Employment Situation, November 2026"].ts == datetime(2026, 12, 4, 13, 30, tzinfo=UTC)
    ppi = next(e for e in events if e.title.startswith("Producer Price Index"))
    assert "08:30 ET assumed" in ppi.title and ppi.ts == datetime(2026, 12, 11, 13, 30, tzinfo=UTC)
    fomc = next(e for e in events if e.title.startswith("FOMC"))
    assert len(fomc.uid) == 64  # generated stable id when UID is missing
    assert parse_ics("hello")[1] == ["Not an iCalendar file (missing BEGIN:VCALENDAR)."]
    bad_tz = ICS.replace("TZID=America/New_York", "TZID=Mars/Olympus")
    assert any("unknown time zone" in e for e in parse_ics(bad_tz)[1])


@pytest.mark.parametrize(
    ("title", "category", "importance", "affects_cl"),
    [
        ("Consumer Price Index for November 2026", "cpi", "high", True),
        ("The Employment Situation, November 2026", "employment", "high", True),
        ("FOMC Statement and Rate Decision", "fomc", "high", True),
        ("Gross Domestic Product, 3rd estimate", "gdp", "high", True),
        ("Retail Sales", "retail_sales", "medium", False),
        ("Weekly Petroleum Status Report", "energy_inventories", "medium", True),
        ("County Employment and Wages", "other", "low", False),  # not the jobs report
        ("Some local holiday", "other", "low", False),
    ],
)
def test_classification(title: str, category: str, importance: str, affects_cl: bool) -> None:
    c, imp, affected = classify(title)
    assert (c, imp, "CL" in affected) == (category, importance, affects_cl)


def test_exposure_window() -> None:
    t = datetime(2026, 12, 10, 13, 30, tzinfo=UTC)
    ev = EconomicEvent(
        uid="x", ts=t, title="CPI", category="cpi", importance="high", affected=["ES", "CL"], source="manual"
    )
    low = EconomicEvent(
        uid="y", ts=t, title="Other", category="other", importance="low", affected=["ES"], source="manual"
    )
    assert exposure([ev, low], "ES", t - timedelta(minutes=15), 15, 15) == [ev]
    assert exposure([ev], "ES", t - timedelta(minutes=16), 15, 15) == []
    assert exposure([ev], "ES", t + timedelta(minutes=15), 15, 15) == [ev]
    assert exposure([ev], "GC", t, 15, 15) == []


async def test_upsert_is_idempotent_and_news_exposure() -> None:
    events, _ = parse_ics(ICS)
    async with get_sessionmaker()() as s:
        assert (await news_exposure(s, "ES", datetime.now(UTC), 15, 15))["status"] == "unknown"
        await upsert_events(s, events, "ics_file", "test")
        await upsert_events(s, events, "ics_file", "test")
        await s.commit()
        rows = (await s.scalars(select(EconomicEvent))).all()
        assert len(rows) == 4
        cpi_time = datetime(2026, 12, 10, 13, 25, tzinfo=UTC)
        hit = await news_exposure(s, "ES", cpi_time, 15, 15)
        assert hit["status"] == "inside_window" and "Consumer Price Index" in hit["summary"]
        assert (await news_exposure(s, "ES", cpi_time - timedelta(hours=2), 15, 15))["status"] == "clear"


async def test_calendar_api(admin_client: httpx.AsyncClient) -> None:
    empty = (await admin_client.get("/api/calendar")).json()
    assert empty["empty_calendar"] is True and "Unverified" in empty["apex_restrictions"]
    dry = (await admin_client.post("/api/calendar/import-ics", json={"ics": ICS, "dry_run": True}, headers=CSRF)).json()
    assert dry["parsed"] == 4 and dry["written"] == 0 and len(dry["errors"]) == 1
    assert {p["category"] for p in dry["preview"]} >= {"cpi", "employment", "fomc"}
    done = (
        await admin_client.post("/api/calendar/import-ics", json={"ics": ICS, "source_name": "bls.ics"}, headers=CSRF)
    ).json()
    assert done["written"] == 4
    soon = datetime.now(UTC) + timedelta(minutes=10)
    r = await admin_client.post(
        "/api/calendar/events", json={"ts": soon.isoformat(), "title": "CPI (manual)"}, headers=CSRF
    )
    assert r.status_code == 201
    ev = r.json()
    assert ev["category"] == "cpi" and ev["in_window"] is True and ev["source"] == "manual"
    custom = await admin_client.post(
        "/api/calendar/events",
        json={"ts": soon.isoformat(), "title": "Company X earnings", "importance": "high", "affected": ["nq"]},
        headers=CSRF,
    )
    assert custom.json()["importance"] == "high" and custom.json()["affected"] == ["NQ"]
    for bad in (
        {"ts": "2026-12-01T10:00:00", "title": "naive"},
        {"ts": soon.isoformat(), "title": "x"},
        {"ts": soon.isoformat(), "title": "ok title", "affected": ["ZZZ"]},
    ):
        assert (await admin_client.post("/api/calendar/events", json=bad, headers=CSRF)).status_code == 422
    listed = (await admin_client.get("/api/calendar?days=60")).json()
    assert listed["empty_calendar"] is False and len(listed["events"]) >= 2
    assert (await admin_client.delete(f"/api/calendar/events/{ev['id']}", headers=CSRF)).status_code == 204
    assert (await admin_client.delete(f"/api/calendar/events/{ev['id']}", headers=CSRF)).status_code == 404


async def test_scanner_alerts_once_when_event_enters_window(admin_client: httpx.AsyncClient) -> None:
    now = datetime.now(UTC)
    async with get_sessionmaker()() as s:
        s.add(
            EconomicEvent(
                uid="soon",
                ts=now + timedelta(minutes=10),
                title="Consumer Price Index",
                category="cpi",
                importance="high",
                affected=["ES", "NQ"],
                source="manual",
            )
        )
        s.add(
            EconomicEvent(
                uid="later",
                ts=now + timedelta(hours=3),
                title="FOMC",
                category="fomc",
                importance="high",
                affected=["ES"],
                source="manual",
            )
        )
        await s.commit()
    scanner = ScannerService(get_settings(), get_sessionmaker(), get_redis(), clock=lambda: now)
    await scanner.scan_once()
    await scanner.scan_once()
    async with get_sessionmaker()() as s:
        alerts = (await s.scalars(select(AlertRow).where(AlertRow.kind == "economic_event"))).all()
    assert len(alerts) == 1 and alerts[0].title == "Consumer Price Index in 10 min"
    assert alerts[0].severity == "warning" and "unverified" in alerts[0].body


async def test_briefing(admin_client: httpx.AsyncClient) -> None:
    b = (await admin_client.get("/api/briefing")).json()
    assert b["phase"] in {"premarket", "live", "postmarket", "closed"}
    statuses = {c["text"].split(";")[0]: c["status"] for c in b["checklist"]}
    assert any(s == "critical" for s in statuses.values())  # no market data
    assert b["account"]["known"] is False and b["ranked_opportunities"] == 0
    assert any("UNVERIFIED" in c["text"] for c in b["checklist"])


async def test_calendar_admin_only(admin_client: httpx.AsyncClient) -> None:
    from app.core.security import hash_password
    from app.models.user import User

    async with get_sessionmaker()() as s:
        s.add(User(email="v@example.com", password_hash=hash_password("Viewer-Pass-42!"), role="viewer"))
        await s.commit()
    admin_client.cookies.clear()
    await admin_client.post("/api/auth/login", json={"email": "v@example.com", "password": "Viewer-Pass-42!"})
    assert (await admin_client.get("/api/calendar")).status_code == 200
    assert (await admin_client.post("/api/calendar/import-ics", json={"ics": ICS}, headers=CSRF)).status_code == 403
