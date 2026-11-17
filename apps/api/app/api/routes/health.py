from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser
from app.schemas.system import ComponentState, Liveness, Readiness, SystemStatus
from app.services.health import check_database, check_redis, system_status

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=Liveness)
async def live() -> Liveness:
    return Liveness(status="ok", server_time=datetime.now(UTC))


@router.get("/health/ready", response_model=Readiness)
async def ready(response: Response) -> Readiness:
    db = await check_database()
    redis = await check_redis()
    checks = {"database": db.state, "redis": redis.state}
    ok = all(v == ComponentState.OK for v in checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status="ok" if ok else "unavailable", checks=checks)


@router.get("/system/status", response_model=SystemStatus)
async def status_detail(_: CurrentUser) -> SystemStatus:
    return await system_status()
