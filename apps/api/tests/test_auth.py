from __future__ import annotations

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.security import create_access_token
from app.models.audit import AuditLog
from tests.conftest import ADMIN_EMAIL, CSRF, STRONG_PASSWORD


async def test_bootstrap_status_then_first_admin(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/auth/bootstrap-status")
    assert r.json() == {"needs_bootstrap": True, "bootstrap_token_required": False}
    r = await client.post("/api/auth/bootstrap", json={"email": ADMIN_EMAIL, "password": STRONG_PASSWORD})
    assert r.status_code == 201
    assert r.json()["user"]["role"] == "admin"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert (await client.get("/api/auth/bootstrap-status")).json()["needs_bootstrap"] is False


async def test_bootstrap_only_once(admin_client: httpx.AsyncClient) -> None:
    r = await admin_client.post(
        "/api/auth/bootstrap", json={"email": "other@example.com", "password": STRONG_PASSWORD}
    )
    assert r.status_code == 409


async def test_bootstrap_rejects_weak_password_without_echo(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/auth/bootstrap", json={"email": ADMIN_EMAIL, "password": "alllowercase123"})
    assert r.status_code == 422
    assert "alllowercase123" not in r.text


async def test_bootstrap_token_enforced_when_configured(client: httpx.AsyncClient) -> None:
    from pydantic import SecretStr

    get_settings().bootstrap_token = SecretStr("s3cret-bootstrap-token")
    body = {"email": ADMIN_EMAIL, "password": STRONG_PASSWORD}
    assert (await client.post("/api/auth/bootstrap", json=body)).status_code == 403
    bad = {**body, "bootstrap_token": "wrong"}
    assert (await client.post("/api/auth/bootstrap", json=bad)).status_code == 403
    good = {**body, "bootstrap_token": "s3cret-bootstrap-token"}
    assert (await client.post("/api/auth/bootstrap", json=good)).status_code == 201


async def test_login_me_logout_revokes_token(admin_client: httpx.AsyncClient) -> None:
    admin_client.cookies.clear()
    assert (await admin_client.get("/api/auth/me")).status_code == 401
    r = await admin_client.post(
        "/api/auth/login", json={"email": ADMIN_EMAIL.upper(), "password": STRONG_PASSWORD}
    )
    assert r.status_code == 200
    old_cookie = admin_client.cookies.get(get_settings().cookie_name)
    assert (await admin_client.get("/api/auth/me")).json()["email"] == ADMIN_EMAIL

    # Cookie-authenticated POST without CSRF header is refused.
    assert (await admin_client.post("/api/auth/logout")).status_code == 403
    assert (await admin_client.post("/api/auth/logout", headers=CSRF)).status_code == 204

    # The old token is revoked server-side even if replayed.
    r = await admin_client.get("/api/auth/me", headers={"Authorization": f"Bearer {old_cookie}"})
    assert r.status_code == 401


async def test_bad_password_and_unknown_user_same_response(
    admin_client: httpx.AsyncClient,
) -> None:
    a = await admin_client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password"})
    b = await admin_client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"}
    )
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


async def test_lockout_after_repeated_failures(admin_client: httpx.AsyncClient) -> None:
    for _ in range(get_settings().login_max_failures):
        r = await admin_client.post(
            "/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password"}
        )
        assert r.status_code == 401
    r = await admin_client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": STRONG_PASSWORD})
    assert r.status_code == 423


async def test_auth_rate_limit(client: httpx.AsyncClient) -> None:
    get_settings().auth_rate_limit_per_minute = 3
    codes = [
        (
            await client.post("/api/auth/login", json={"email": "x@example.com", "password": "nope"})
        ).status_code
        for _ in range(5)
    ]
    assert codes[:3] == [401, 401, 401]
    assert codes[3:] == [429, 429]


async def test_tampered_and_foreign_tokens_rejected(admin_client: httpx.AsyncClient) -> None:
    me = (await admin_client.get("/api/auth/me")).json()
    token, _ = create_access_token(me["id"], 0)
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    admin_client.cookies.clear()
    ok = await admin_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    bad = await admin_client.get("/api/auth/me", headers={"Authorization": f"Bearer {tampered}"})
    assert bad.status_code == 401
    import jwt

    claims = {
        "sub": me["id"],
        "ver": 0,
        "exp": 9999999999,
        "iat": 0,
        "aud": "apex-web",
        "iss": "apex-api",
    }
    forged = jwt.encode(claims, "an-attacker-controlled-key-of-sufficient-length", "HS256")
    r = await admin_client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


async def test_audit_log_records_auth_events_without_secrets(
    admin_client: httpx.AsyncClient,
) -> None:
    await admin_client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password"})
    async with get_sessionmaker()() as s:
        rows = (await s.scalars(select(AuditLog).order_by(AuditLog.id))).all()
    actions = [(r.action, r.outcome) for r in rows]
    assert ("auth.bootstrap", "success") in actions
    assert ("auth.login", "failure") in actions
    assert all("wrong-password" not in str(r.details) for r in rows)
