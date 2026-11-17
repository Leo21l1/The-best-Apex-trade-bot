"""Connection tests for external integrations. All tests here are free (no paid usage):

* official news feeds: one HTTP GET of the RSS document
* Anthropic: ``models.list`` (authenticates; consumes no tokens)
* Databento: ``metadata.list_datasets`` (authenticates; returns no market data)

Nothing here can place or modify orders, and no result is cached as "connected" beyond the call.
"""

from __future__ import annotations

import time
from typing import Any, Literal

import anthropic
import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from app.adapters.marketdata.databento import BASE_URL as DATABENTO_URL
from app.ai.copilot import build_client
from app.api.deps import AdminUser
from app.core.config import get_settings
from app.services.news import SOURCE_BY_KEY, fetch, parse_rss

router = APIRouter(prefix="/integrations", tags=["integrations"])


class TestIn(BaseModel):
    target: Literal["news:fed", "news:eia", "anthropic", "databento"]


def _result(target: str, state: str, detail: str, started: float, **extra: Any) -> dict[str, Any]:
    return {
        "target": target,
        "state": state,
        "detail": detail,
        "latency_ms": round((time.monotonic() - started) * 1000),
        **extra,
    }


@router.post("/test")
async def test_integration(body: TestIn, _: AdminUser) -> dict[str, Any]:
    t0 = time.monotonic()
    s = get_settings()
    if body.target.startswith("news:"):
        src = SOURCE_BY_KEY[body.target.split(":", 1)[1]]
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                items = parse_rss(src, await fetch(src, client))
        except (httpx.HTTPError, OSError, ValueError) as exc:
            return _result(body.target, "down", f"{type(exc).__name__}: {exc}"[:240], t0)
        newest = max((i["published_at"] for i in items), default=None)
        return _result(
            body.target,
            "ok",
            f"{len(items)} items; newest published {newest:%Y-%m-%d %H:%M} UTC"
            if newest
            else "Feed reachable but empty.",
            t0,
            items=len(items),
        )
    if body.target == "anthropic":
        if s.anthropic_api_key is None:
            return _result(body.target, "not_configured", "No API key: set APEX_ANTHROPIC_API_KEY on the server.", t0)
        try:
            client = build_client(s)
            page = await client.models.list(limit=5)
            ids = [m.id for m in page.data]
        except anthropic.AuthenticationError:
            return _result(body.target, "down", "Authentication failed: the API key was rejected.", t0)
        except anthropic.APIConnectionError as exc:
            return _result(body.target, "down", f"Connection failed: {type(exc).__name__}", t0)
        except anthropic.APIStatusError as exc:
            return _result(body.target, "degraded", f"API returned HTTP {exc.status_code}", t0)
        return _result(
            body.target,
            "ok",
            f"Authenticated (models list, no tokens used). Configured model: "
            f"{s.copilot_model}. Available: {', '.join(ids)}",
            t0,
            models=ids,
        )
    # databento
    if s.databento_api_key is None:
        return _result(body.target, "not_configured", "No API key: set APEX_DATABENTO_API_KEY (licensed CME data).", t0)
    try:
        async with httpx.AsyncClient(
            base_url=DATABENTO_URL, auth=(s.databento_api_key.get_secret_value(), ""), timeout=15
        ) as client:
            r = await client.get("/v0/metadata.list_datasets")
    except (httpx.HTTPError, OSError) as exc:
        return _result(body.target, "down", f"Connection failed: {type(exc).__name__}", t0)
    if r.status_code in (401, 403):
        return _result(body.target, "down", "Authentication failed: the API key was rejected.", t0)
    if r.status_code != 200:
        return _result(body.target, "degraded", f"HTTP {r.status_code}", t0)
    datasets = r.json() if isinstance(r.json(), list) else []
    return _result(
        body.target,
        "ok" if "GLBX.MDP3" in datasets else "degraded",
        "Authenticated; CME Globex dataset "
        + ("available." if "GLBX.MDP3" in datasets else "NOT in this key's datasets."),
        t0,
    )
