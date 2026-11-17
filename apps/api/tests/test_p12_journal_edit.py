"""Editable journal: corrections with provenance, recalculation, validation, notes, screenshots,
audit trail, confirmed deletion and restore."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.marketdata.calendar import trading_day
from app.models.research import JournalEdit, JournalTrade
from tests.conftest import CSRF

T0 = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def add(source: str = "csv") -> int:
    async with get_sessionmaker()() as s:
        t = JournalTrade(
            source_key=f"{source}:x",
            source=source,
            trading_date=trading_day(T0),
            symbol="MES",
            contract="MESZ6",
            direction="long",
            quantity=2,
            entry_time=T0,
            exit_time=T0 + timedelta(minutes=5),
            entry_price=5000.0,
            exit_price=5004.0,
            gross_pnl=40.0,
            fees=2.4,
            net_pnl=37.6,
            mistakes=[],
        )
        s.add(t)
        await s.commit()
        return t.id


async def patch(c: httpx.AsyncClient, tid: int, **body: object) -> httpx.Response:
    return await c.patch(f"/api/journal/trades/{tid}", json=body, headers=CSRF)


async def test_annotation_edit_persists_and_is_audited(admin_client: httpx.AsyncClient) -> None:
    tid = await add()
    r = await patch(
        admin_client,
        tid,
        setup="ORB retest",
        thesis="Held above OR high",
        psychology="calm",
        emotion_tags=["patient", "patient", "confident"],
        lessons="Wait for the retest",
        tags=["A+"],
        chart_links=["https://example.com/chart/1"],
        strategy_id="orb",
    )
    assert r.status_code == 200, r.text
    again = (await admin_client.get(f"/api/journal/trades/{tid}")).json()  # a fresh read from the database
    assert again["setup"] == "ORB retest" and again["emotion_tags"] == ["confident", "patient"]
    assert again["chart_links"] == ["https://example.com/chart/1"] and again["strategy_id"] == "orb"
    assert again["corrected_fields"] == [] and again["original"] is None  # annotations are not corrections
    hist = (await admin_client.get(f"/api/journal/trades/{tid}/history")).json()
    assert {h["field"] for h in hist} >= {"setup", "thesis", "psychology", "emotion_tags", "lessons", "tags"}
    assert all(h["user"] and h["action"] == "edit" for h in hist)
    # Unchanged values record nothing.
    assert (await patch(admin_client, tid, setup="ORB retest")).json()["changes"] == []


async def test_execution_correction_keeps_original_and_recomputes(admin_client: httpx.AsyncClient) -> None:
    get_settings().market_data_provider = "databento"
    tid = await add("csv")
    await admin_client.get(f"/api/journal/trades/{tid}")  # generates a review
    r = (await patch(admin_client, tid, quantity=3, exit_price=5006.0)).json()
    # MES $5/pt: (5006-5000) x 5 x 3 = 90 gross; net = 90 - 2.4
    assert r["gross_pnl"] == 90.0 and r["net_pnl"] == 87.6
    assert r["original"]["quantity"] == 2 and r["original"]["exit_price"] == 5004.0 and r["original"]["net_pnl"] == 37.6
    assert set(r["corrected_fields"]) == {"quantity", "exit_price", "gross_pnl", "net_pnl"}
    assert r["corrections"]["quantity"]["provenance"] == "manual correction of broker record"
    assert r["corrections"]["gross_pnl"]["derived"] is True and r["corrections"]["quantity"]["derived"] is False
    assert "with manual corrections" in r["record_provenance"]
    assert r["review"] is None  # cleared so it regenerates from the corrected record
    # A second correction never alters the stored original.
    r2 = (await patch(admin_client, tid, fees=3.0)).json()
    assert r2["original"]["quantity"] == 2 and r2["net_pnl"] == 87.0
    # Analytics use the corrected values.
    a = (await admin_client.get("/api/analytics?start=2026-11-01&end=2026-11-30")).json()
    assert a["overall"]["net_pnl"] == 87.0
    # Journal, review and history stay in sync after a refresh.
    detail = (await admin_client.get(f"/api/journal/trades/{tid}")).json()
    assert detail["review"] is not None and "3 MES" in detail["review"]["journal"]["trade_summary"]


async def test_explicit_realised_pnl_and_time_corrections(admin_client: httpx.AsyncClient) -> None:
    tid = await add("manual")
    r = (await patch(admin_client, tid, net_pnl=50.0)).json()
    assert r["net_pnl"] == 50.0 and r["gross_pnl"] == 52.4
    assert r["corrections"]["net_pnl"]["provenance"] == "manual correction"
    later = T0 + timedelta(days=1, hours=8)  # next day 18:00 ET boundary handled by trading_day
    r = (
        await patch(
            admin_client, tid, entry_time=later.isoformat(), exit_time=(later + timedelta(minutes=3)).isoformat()
        )
    ).json()
    assert r["trading_date"] == trading_day(later + timedelta(minutes=3)).isoformat()


async def test_invalid_edits_are_rejected_without_changes(admin_client: httpx.AsyncClient) -> None:
    tid = await add()
    bad = [
        {"entry_price": 5000.1},  # off the 0.25 tick grid
        {"exit_time": (T0 - timedelta(minutes=1)).isoformat()},
        {"symbol": "ZZ"},
        {"quantity": None},
        {"entry_time": "2026-11-11T10:00:00"},  # no time zone
        {"chart_links": ["javascript:alert(1)"]},
        {"mistakes": ["not_a_tag"]},
        {"unknown_field": 1},
    ]
    for body in bad:
        r = await patch(admin_client, tid, **body)
        assert r.status_code == 422, (body, r.text)
    t = (await admin_client.get(f"/api/journal/trades/{tid}")).json()
    assert t["entry_price"] == 5000.0 and t["quantity"] == 2 and t["original"] is None
    assert (await patch(admin_client, 999999, setup="x")).status_code == 404


async def test_notes_crud_is_audited(admin_client: httpx.AsyncClient) -> None:
    tid = await add()
    n = (await admin_client.post(f"/api/journal/trades/{tid}/notes", json={"body": "First note"}, headers=CSRF)).json()
    e = (await admin_client.patch(f"/api/journal/notes/{n['id']}", json={"body": "Edited"}, headers=CSRF)).json()
    assert e["body"] == "Edited" and e["updated_at"]
    assert [x["body"] for x in (await admin_client.get(f"/api/journal/trades/{tid}/notes")).json()] == ["Edited"]
    assert (await admin_client.delete(f"/api/journal/notes/{n['id']}", headers=CSRF)).status_code == 204
    assert (await admin_client.get(f"/api/journal/trades/{tid}/notes")).json() == []
    acts = [h["action"] for h in (await admin_client.get(f"/api/journal/trades/{tid}/history")).json()]
    assert acts == ["note_delete", "note_edit", "note_add"]
    assert (
        await admin_client.post(f"/api/journal/trades/{tid}/notes", json={"body": ""}, headers=CSRF)
    ).status_code == 422


async def test_screenshot_upload_is_validated_and_served_safely(admin_client: httpx.AsyncClient) -> None:
    tid = await add()
    body = {"filename": "entry<1>.png", "content_type": "image/png", "data_base64": base64.b64encode(PNG).decode()}
    a = (await admin_client.post(f"/api/journal/trades/{tid}/attachments", json=body, headers=CSRF)).json()
    assert a["size"] == len(PNG) and a["filename"] == "entry_1_.png"
    got = await admin_client.get(a["url"])
    assert got.content == PNG and got.headers["content-type"] == "image/png"
    assert got.headers["x-content-type-options"] == "nosniff"
    fake = {**body, "data_base64": base64.b64encode(b"<html>not an image</html>").decode()}
    assert (
        await admin_client.post(f"/api/journal/trades/{tid}/attachments", json=fake, headers=CSRF)
    ).status_code == 422
    svg = {**body, "content_type": "image/svg+xml"}
    assert (
        await admin_client.post(f"/api/journal/trades/{tid}/attachments", json=svg, headers=CSRF)
    ).status_code == 422
    assert (await admin_client.delete(a["url"], headers=CSRF)).status_code == 204


async def test_delete_requires_confirmation_and_can_be_restored(admin_client: httpx.AsyncClient) -> None:
    tid = await add()
    await patch(admin_client, tid, thesis="keep me", quantity=1)
    await admin_client.post(f"/api/journal/trades/{tid}/notes", json={"body": "a note"}, headers=CSRF)
    assert (await admin_client.delete(f"/api/journal/trades/{tid}", headers=CSRF)).status_code == 428
    assert (await admin_client.delete(f"/api/journal/trades/{tid}?confirm={tid + 1}", headers=CSRF)).status_code == 428
    assert (await admin_client.delete(f"/api/journal/trades/{tid}?confirm={tid}", headers=CSRF)).status_code == 204
    assert (await admin_client.get(f"/api/journal/trades/{tid}")).status_code == 404
    gone = (await admin_client.get("/api/journal/deleted")).json()
    assert gone[0]["trade_id"] == tid and gone[0]["symbol"] == "MES"
    r = await admin_client.post(f"/api/journal/deleted/{gone[0]['edit_id']}/restore", headers=CSRF)
    assert r.status_code == 200, r.text
    back = r.json()
    assert back["id"] == tid and back["thesis"] == "keep me" and back["quantity"] == 1
    assert back["original"]["quantity"] == 2  # provenance survives the round trip
    assert [n["body"] for n in (await admin_client.get(f"/api/journal/trades/{tid}/notes")).json()] == ["a note"]
    assert (
        await admin_client.post(f"/api/journal/deleted/{gone[0]['edit_id']}/restore", headers=CSRF)
    ).status_code == 409
    async with get_sessionmaker()() as s:
        actions = (await s.scalars(select(JournalEdit.action).where(JournalEdit.trade_id == tid))).all()
    assert {"correct", "edit", "note_add", "delete", "restore"} <= set(actions)
