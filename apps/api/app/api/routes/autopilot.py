"""Autopilot status, forward (prospective) simulated performance and the emergency kill switch."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.api.deps import AdminUser, CurrentUser, SessionDep, client_ip
from app.services import autopilot
from app.services.audit import record_audit

router = APIRouter(prefix="/autopilot", tags=["autopilot"])


@router.get("")
async def status(_: CurrentUser, session: SessionDep) -> dict[str, Any]:
    return await autopilot.report(session)


@router.post("/kill")
async def kill(user: AdminUser, request: Request, session: SessionDep) -> dict[str, Any]:
    """Emergency stop: no new entries; the next cycle cancels working orders and flattens (simulated)."""
    await autopilot.kill(True, user.email)
    await record_audit(session, action="autopilot.kill", outcome="success", actor_id=user.id, ip=client_ip(request))
    await session.commit()
    return {"kill_switch": await autopilot.killed()}


@router.post("/resume")
async def resume(user: AdminUser, request: Request, session: SessionDep) -> dict[str, Any]:
    await autopilot.kill(False, user.email)
    await record_audit(session, action="autopilot.resume", outcome="success", actor_id=user.id, ip=client_ip(request))
    await session.commit()
    return {"kill_switch": None}
