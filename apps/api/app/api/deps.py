from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.ratelimit import rate_limiter
from app.core.security import decode_access_token
from app.models.user import Role, User

SessionDep = Annotated[AsyncSession, Depends(get_session)]

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "apex"


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def extract_token(request: Request) -> tuple[str | None, bool]:
    """Return (token, from_cookie)."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    cookie = request.cookies.get(get_settings().cookie_name)
    return cookie, cookie is not None


async def resolve_user(session: AsyncSession, token: str | None) -> User | None:
    if not token:
        return None
    claims = decode_access_token(token)
    if not claims:
        return None
    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except (ValueError, KeyError):
        return None
    user = await session.get(User, user_id)
    if user is None or not user.is_active or user.token_version != claims.get("ver"):
        return None
    return user


async def get_current_user(request: Request, session: SessionDep) -> User:
    token, from_cookie = extract_token(request)
    user = await resolve_user(session, token)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    # CSRF defence for cookie-authenticated state changes (cookie is also SameSite=Strict).
    if from_cookie and request.method in UNSAFE_METHODS:
        if request.headers.get(CSRF_HEADER, "").lower() != CSRF_VALUE:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != Role.ADMIN.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


async def auth_rate_limit(request: Request) -> None:
    allowed = await rate_limiter.hit(
        "auth", client_ip(request), get_settings().auth_rate_limit_per_minute
    )
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many authentication attempts")
