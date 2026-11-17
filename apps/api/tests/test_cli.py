from __future__ import annotations

from sqlalchemy import func, select

from app.cli import create_admin, reset_db
from app.core.db import get_sessionmaker
from app.models.user import User


async def test_create_admin_and_reset() -> None:
    assert await create_admin("ops@example.com", "weak") == 2
    assert await create_admin("Ops@Example.com", "Correct-Horse-42!") == 0
    assert await create_admin("ops@example.com", "Correct-Horse-42!") == 1
    async with get_sessionmaker()() as s:
        user = await s.scalar(select(User))
        assert user is not None and user.email == "ops@example.com" and user.role == "admin"
    assert await reset_db() == 0
    async with get_sessionmaker()() as s:
        assert await s.scalar(select(func.count()).select_from(User)) == 0
