from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

# Configure the environment BEFORE the app is imported.
os.environ.setdefault(
    "APEX_DATABASE_URL", "postgresql+asyncpg://apex:apex_dev_password@localhost:5432/apex_test"
)
os.environ.setdefault("APEX_REDIS_URL", "redis://localhost:6379/15")
os.environ["APEX_ENVIRONMENT"] = "test"
os.environ["APEX_LOG_JSON"] = "false"
os.environ["APEX_LOG_LEVEL"] = "WARNING"

import httpx
import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import dispose_engine, get_engine
from app.core.ratelimit import rate_limiter
from app.core.redis import close_redis, get_redis
from app.main import create_app

API_DIR = Path(__file__).resolve().parents[1]
STRONG_PASSWORD = "Correct-Horse-42!"
ADMIN_EMAIL = "trader@example.com"
CSRF = {"X-Requested-With": "apex"}


@pytest.fixture(scope="session", autouse=True)
def _migrate() -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_DIR,
        check=True,
        env={**os.environ},
    )


@pytest.fixture(autouse=True)
async def _clean_state() -> AsyncIterator[None]:
    settings = get_settings()
    original = settings.model_copy()
    settings.auth_rate_limit_per_minute = 1000
    settings.rate_limit_per_minute = 10_000
    rate_limiter.reset_local()
    yield
    for field in type(settings).model_fields:
        setattr(settings, field, getattr(original, field))
    async with get_engine().begin() as conn:
        await conn.execute(
            text("TRUNCATE alerts, signals, candles, audit_logs, users RESTART IDENTITY CASCADE")
        )
    await get_redis().flushdb()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def admin_client(client: httpx.AsyncClient) -> httpx.AsyncClient:
    r = await client.post("/api/auth/bootstrap", json={"email": ADMIN_EMAIL, "password": STRONG_PASSWORD})
    assert r.status_code == 201, r.text
    return client


@pytest.fixture
async def release_global_clients() -> None:
    """Dispose loop-bound clients so a sync TestClient can create its own."""
    await dispose_engine()
    await close_redis()
