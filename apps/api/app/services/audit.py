from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_var
from app.models.audit import AuditLog

_SENSITIVE = {"password", "token", "secret", "api_key", "authorization"}


def _scrub(details: dict[str, Any]) -> dict[str, Any]:
    return {k: ("[REDACTED]" if k.lower() in _SENSITIVE else v) for k, v in details.items()}


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    outcome: str,
    actor_id: uuid.UUID | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            action=action,
            outcome=outcome,
            actor_id=actor_id,
            ip=ip,
            request_id=request_id_var.get(),
            details=_scrub(details or {}),
        )
    )
    await session.flush()
