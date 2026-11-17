from __future__ import annotations

import httpx
from starlette.requests import Request

from app.api.deps import client_ip
from app.core.config import get_settings


def req(peer: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


def test_forwarded_for_is_trusted_only_from_configured_proxies() -> None:
    assert client_ip(req("203.0.113.9", "1.2.3.4")) == "203.0.113.9"  # untrusted peer: header ignored
    assert client_ip(req("127.0.0.1", "198.51.100.7")) == "198.51.100.7"
    # A client-forged left-most entry is ignored; the right-most untrusted address wins.
    assert client_ip(req("127.0.0.1", "6.6.6.6, 198.51.100.7")) == "198.51.100.7"
    assert client_ip(req("127.0.0.1", "198.51.100.7, 127.0.0.1")) == "198.51.100.7"
    assert client_ip(req("127.0.0.1")) == "127.0.0.1"
    assert client_ip(req("::1", "2001:db8::1")) == "2001:db8::1"


async def test_signed_in_traffic_is_limited_per_user_not_per_shared_ip(
    admin_client: httpx.AsyncClient,
) -> None:
    s = get_settings()
    s.rate_limit_per_minute = 3
    s.user_rate_limit_per_minute = 50
    # The signed-in user is not throttled by the low per-IP limit...
    codes = [(await admin_client.get("/api/auth/me")).status_code for _ in range(10)]
    assert codes == [200] * 10
    # ...while anonymous requests from one IP are.
    anon = httpx.AsyncClient(transport=admin_client._transport, base_url="http://testserver")
    hdr = {"x-forwarded-for": "198.51.100.20"}
    codes = [(await anon.get("/api/market/instruments", headers=hdr)).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
    # A different client IP behind the same proxy has its own budget.
    other = await anon.get("/api/market/instruments", headers={"x-forwarded-for": "198.51.100.21"})
    assert other.status_code == 401
    # A forged token does not get the per-user budget.
    forged = {"authorization": "Bearer not-a-token", **hdr}
    assert (await anon.get("/api/auth/me", headers=forged)).status_code == 429
    await anon.aclose()


async def test_user_limit_applies(admin_client: httpx.AsyncClient) -> None:
    get_settings().user_rate_limit_per_minute = 4
    codes = [(await admin_client.get("/api/auth/me")).status_code for _ in range(6)]
    assert codes == [200, 200, 200, 200, 429, 429]
