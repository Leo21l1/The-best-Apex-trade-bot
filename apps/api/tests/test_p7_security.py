from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.security import hash_password
from app.main import create_app
from app.models.user import User
from tests.conftest import ADMIN_EMAIL, CSRF, STRONG_PASSWORD

VIEWER = ("viewer@example.com", "Viewer-Pass-42!")


async def as_viewer(c: httpx.AsyncClient) -> None:
    async with get_sessionmaker()() as s:
        s.add(User(email=VIEWER[0], password_hash=hash_password(VIEWER[1]), role="viewer"))
        await s.commit()
    c.cookies.clear()
    r = await c.post("/api/auth/login", json={"email": VIEWER[0], "password": VIEWER[1]})
    assert r.status_code == 200


WRITES = [
    ("POST", "/api/account/snapshots", {"balance": "50000"}),
    ("POST", "/api/account/import-csv", {"csv": "Date,Balance\n2026-11-10,50000\n"}),
    (
        "POST",
        "/api/backtests",
        {"strategy_id": "orb", "symbol": "ES", "start": "2026-11-01T00:00:00Z", "end": "2026-11-10T00:00:00Z"},
    ),
    ("POST", "/api/paper/orders", {"symbol": "ES", "side": "buy", "type": "market", "qty": 1}),
    ("POST", "/api/paper/orders/1/cancel", None),
    ("POST", "/api/paper/flatten", None),
    ("POST", "/api/paper/reset", None),
    ("POST", "/api/journal/import", {"csv": "a,b\n1,2\n"}),
    ("PATCH", "/api/journal/trades/1", {"notes": "x"}),
    ("PUT", "/api/rules/max_drawdown", {"value": "1"}),
    ("POST", "/api/calendar/events", {"ts": "2026-12-01T13:30:00Z", "title": "CPI"}),
    ("POST", "/api/users", {"email": "x@example.com", "password": STRONG_PASSWORD}),
]
READS = [
    "/api/account/state",
    "/api/rules",
    "/api/opportunities",
    "/api/strategies",
    "/api/journal/trades",
    "/api/analytics",
    "/api/backtests",
    "/api/calendar",
    "/api/briefing",
    "/api/copilot/status",
    "/api/paper",
]


async def test_viewers_are_read_only(admin_client: httpx.AsyncClient) -> None:
    await as_viewer(admin_client)
    for method, path, body in WRITES:
        r = await admin_client.request(method, path, json=body, headers=CSRF)
        assert r.status_code == 403, (method, path, r.status_code)
    for path in READS:
        assert (await admin_client.get(path)).status_code == 200, path
    risk = await admin_client.post(
        "/api/risk/check", json={"symbol": "ES", "direction": "long", "entry": "5000", "stop": "4996"}, headers=CSRF
    )
    assert risk.status_code == 200  # a read-only calculation


async def test_password_change_revokes_other_sessions(admin_client: httpx.AsyncClient) -> None:
    old = admin_client.cookies.get(get_settings().cookie_name)
    assert (
        await admin_client.post(
            "/api/auth/password", json={"current_password": "nope", "new_password": "New-Password-99!"}, headers=CSRF
        )
    ).status_code == 403
    assert (
        await admin_client.post(
            "/api/auth/password",
            json={"current_password": STRONG_PASSWORD, "new_password": "weakpassword"},
            headers=CSRF,
        )
    ).status_code == 422
    assert (
        await admin_client.post(
            "/api/auth/password",
            json={"current_password": STRONG_PASSWORD, "new_password": STRONG_PASSWORD},
            headers=CSRF,
        )
    ).status_code == 422
    r = await admin_client.post(
        "/api/auth/password",
        json={"current_password": STRONG_PASSWORD, "new_password": "New-Password-99!"},
        headers=CSRF,
    )
    assert r.status_code == 200
    assert (await admin_client.get("/api/auth/me")).status_code == 200  # fresh cookie works
    stale = await admin_client.get("/api/auth/me", headers={"Authorization": f"Bearer {old}"})
    assert stale.status_code == 401
    admin_client.cookies.clear()
    assert (
        await admin_client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": STRONG_PASSWORD})
    ).status_code == 401
    assert (
        await admin_client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": "New-Password-99!"})
    ).status_code == 200


async def test_user_management(admin_client: httpx.AsyncClient) -> None:
    r = await admin_client.post(
        "/api/users", json={"email": "Ops@Example.com", "password": "Ops-Password-42!"}, headers=CSRF
    )
    assert r.status_code == 201 and r.json()["role"] == "viewer" and r.json()["email"] == "ops@example.com"
    uid = r.json()["id"]
    assert (
        await admin_client.post(
            "/api/users", json={"email": "ops@example.com", "password": "Ops-Password-42!"}, headers=CSRF
        )
    ).status_code == 409
    assert (
        await admin_client.post("/api/users", json={"email": "w@example.com", "password": "short"}, headers=CSRF)
    ).status_code == 422
    users = (await admin_client.get("/api/users")).json()
    assert [u["email"] for u in users] == [ADMIN_EMAIL, "ops@example.com"]
    me = next(u for u in users if u["email"] == ADMIN_EMAIL)
    assert (
        await admin_client.patch(f"/api/users/{me['id']}", json={"is_active": False}, headers=CSRF)
    ).status_code == 409
    assert (
        await admin_client.patch(f"/api/users/{me['id']}", json={"role": "viewer"}, headers=CSRF)
    ).status_code == 409
    other = httpx.AsyncClient(transport=admin_client._transport, base_url="http://testserver")
    assert (
        await other.post("/api/auth/login", json={"email": "ops@example.com", "password": "Ops-Password-42!"})
    ).status_code == 200
    assert (await admin_client.patch(f"/api/users/{uid}", json={"is_active": False}, headers=CSRF)).status_code == 200
    assert (await other.get("/api/auth/me")).status_code == 401  # deactivation revokes the session at once
    assert (
        await other.post("/api/auth/login", json={"email": "ops@example.com", "password": "Ops-Password-42!"})
    ).status_code == 401
    await other.aclose()


async def test_concurrent_bootstrap_creates_exactly_one_admin(client: httpx.AsyncClient) -> None:
    other = httpx.AsyncClient(transport=client._transport, base_url="http://testserver")
    a, b = await asyncio.gather(
        client.post("/api/auth/bootstrap", json={"email": "a@example.com", "password": STRONG_PASSWORD}),
        other.post("/api/auth/bootstrap", json={"email": "b@example.com", "password": STRONG_PASSWORD}),
    )
    assert sorted([a.status_code, b.status_code]) == [201, 409]
    await other.aclose()


async def test_security_headers(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/health/live")
    assert r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert r.headers["x-frame-options"] == "DENY" and r.headers["referrer-policy"] == "no-referrer"
    assert "strict-transport-security" not in r.headers
    get_settings().cookie_secure = True
    r = await client.get("/api/health/live")
    assert r.headers["strict-transport-security"].startswith("max-age=31536000")


@pytest.mark.usefixtures("release_global_clients")
def test_websocket_closes_after_logout(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.routes import ws as ws_module

    monkeypatch.setattr(ws_module, "STATUS_INTERVAL_S", 0.2)
    with TestClient(create_app()) as tc:
        tc.post("/api/auth/bootstrap", json={"email": ADMIN_EMAIL, "password": STRONG_PASSWORD})
        with tc.websocket_connect("/api/ws") as ws:
            assert ws.receive_json()["topic"] == "system.status"
            assert tc.post("/api/auth/logout", headers=CSRF).status_code == 204
            deadline = time.time() + 5
            with pytest.raises(WebSocketDisconnect):
                while time.time() < deadline:
                    ws.receive_json()
