"""Integration connection tests report honest states and never claim an unconfigured service works."""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from tests.conftest import CSRF


async def test_unconfigured_integrations_report_not_configured(admin_client: httpx.AsyncClient) -> None:
    get_settings().anthropic_api_key = None
    get_settings().databento_api_key = None
    for target in ("anthropic", "databento"):
        r = await admin_client.post("/api/integrations/test", json={"target": target}, headers=CSRF)
        assert r.status_code == 200 and r.json()["state"] == "not_configured", r.text
    bad = await admin_client.post("/api/integrations/test", json={"target": "tradovate"}, headers=CSRF)
    assert bad.status_code == 422


async def test_integration_tests_require_admin(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/integrations/test", json={"target": "anthropic"}, headers=CSRF)
    assert r.status_code in (401, 403)
