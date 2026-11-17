from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, text

from app.api.deps import AdminUser, CurrentUser, SessionDep, auth_rate_limit, client_ip
from app.core.config import Environment, get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import Role, User
from app.schemas.auth import (
    BootstrapRequest,
    BootstrapStatus,
    Credentials,
    PasswordChange,
    SessionOut,
    UserAdminOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["auth"])

# Pre-computed hash so failed lookups take comparable time to real verifications.
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


def _set_session_cookie(response: Response, token: str, expires: datetime) -> None:
    s = get_settings()
    response.set_cookie(
        s.cookie_name,
        token,
        httponly=True,
        secure=s.cookie_secure,
        samesite="strict",
        expires=expires,
        path="/",
    )


async def _user_count(session: SessionDep) -> int:
    return int(await session.scalar(select(func.count()).select_from(User)) or 0)


def _bootstrap_token_required() -> bool:
    s = get_settings()
    return s.bootstrap_token is not None or s.environment == Environment.PRODUCTION


@router.get("/bootstrap-status", response_model=BootstrapStatus)
async def bootstrap_status(session: SessionDep) -> BootstrapStatus:
    return BootstrapStatus(
        needs_bootstrap=await _user_count(session) == 0,
        bootstrap_token_required=_bootstrap_token_required(),
    )


@router.post(
    "/bootstrap",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(auth_rate_limit)],
)
async def bootstrap(body: BootstrapRequest, request: Request, response: Response, session: SessionDep) -> SessionOut:
    """Create the first administrator. Disabled permanently once any user exists."""
    s = get_settings()
    # Serialise concurrent bootstrap attempts so at most one first admin can ever be created.
    await session.execute(text("SELECT pg_advisory_xact_lock(724001)"))
    if await _user_count(session) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already initialised")
    if _bootstrap_token_required():
        expected = s.bootstrap_token.get_secret_value() if s.bootstrap_token else ""
        if not expected or not hmac.compare_digest(expected, body.bootstrap_token or ""):
            await record_audit(session, action="auth.bootstrap", outcome="denied", ip=client_ip(request))
            await session.commit()
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid bootstrap token")
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        role=Role.ADMIN.value,
        last_login_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    await record_audit(session, action="auth.bootstrap", outcome="success", actor_id=user.id, ip=client_ip(request))
    await session.commit()
    token, expires = create_access_token(str(user.id), user.token_version)
    _set_session_cookie(response, token, expires)
    return SessionOut(user=UserOut.model_validate(user), expires_at=expires)


@router.post("/login", response_model=SessionOut, dependencies=[Depends(auth_rate_limit)])
async def login(body: Credentials, request: Request, response: Response, session: SessionDep) -> SessionOut:
    s = get_settings()
    ip = client_ip(request)
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    now = datetime.now(UTC)
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    if user is None:
        verify_password(body.password, _DUMMY_HASH)
        await record_audit(
            session,
            action="auth.login",
            outcome="failure",
            ip=ip,
            details={"reason": "unknown_user"},
        )
        await session.commit()
        raise generic
    if user.locked_until and user.locked_until > now:
        await record_audit(
            session,
            action="auth.login",
            outcome="denied",
            actor_id=user.id,
            ip=ip,
            details={"reason": "locked"},
        )
        await session.commit()
        raise HTTPException(status.HTTP_423_LOCKED, "Account temporarily locked")
    if not user.is_active or not verify_password(body.password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= s.login_max_failures:
            user.locked_until = now + timedelta(minutes=s.login_lockout_minutes)
            user.failed_login_count = 0
        await record_audit(
            session,
            action="auth.login",
            outcome="failure",
            actor_id=user.id,
            ip=ip,
            details={"reason": "bad_password_or_inactive"},
        )
        await session.commit()
        raise generic

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    await record_audit(session, action="auth.login", outcome="success", actor_id=user.id, ip=ip)
    await session.commit()
    token, expires = create_access_token(str(user.id), user.token_version)
    _set_session_cookie(response, token, expires)
    return SessionOut(user=UserOut.model_validate(user), expires_at=expires)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: CurrentUser, request: Request, response: Response, session: SessionDep) -> Response:
    # Revoke every outstanding token for this user.
    user.token_version += 1
    await record_audit(session, action="auth.logout", outcome="success", actor_id=user.id, ip=client_ip(request))
    await session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    response.delete_cookie(get_settings().cookie_name, path="/")
    return response


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/password", dependencies=[Depends(auth_rate_limit)])
async def change_password(
    body: PasswordChange, user: CurrentUser, request: Request, response: Response, session: SessionDep
) -> SessionOut:
    """Change your password. Every other session is signed out; this one gets a fresh cookie."""
    if not verify_password(body.current_password, user.password_hash):
        await record_audit(
            session, action="auth.password_change", outcome="failure", actor_id=user.id, ip=client_ip(request)
        )
        await session.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "New password must differ from the current one")
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    await record_audit(
        session, action="auth.password_change", outcome="success", actor_id=user.id, ip=client_ip(request)
    )
    await session.commit()
    token, expires = create_access_token(str(user.id), user.token_version)
    _set_session_cookie(response, token, expires)
    return SessionOut(user=UserOut.model_validate(user), expires_at=expires)


# --- user management (admin) -----------------------------------------------------------------

users_router = APIRouter(prefix="/users", tags=["users"])


@users_router.get("", response_model=list[UserAdminOut])
async def list_users(_: AdminUser, session: SessionDep) -> list[UserAdminOut]:
    rows = (await session.scalars(select(User).order_by(User.created_at))).all()
    return [UserAdminOut.model_validate(u) for u in rows]


@users_router.post("", response_model=UserAdminOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, admin: AdminUser, request: Request, session: SessionDep) -> UserAdminOut:
    email = body.email.lower()
    if await session.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email exists")
    user = User(email=email, password_hash=hash_password(body.password), role=body.role)
    session.add(user)
    await session.flush()
    await record_audit(
        session,
        action="users.create",
        outcome="success",
        actor_id=admin.id,
        ip=client_ip(request),
        details={"user_id": str(user.id), "role": body.role},
    )
    await session.commit()
    await session.refresh(user)
    return UserAdminOut.model_validate(user)


@users_router.patch("/{user_id}", response_model=UserAdminOut)
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, admin: AdminUser, request: Request, session: SessionDep
) -> UserAdminOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id == admin.id and (body.is_active is False or body.role == Role.VIEWER.value):
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot deactivate or demote yourself")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(user, k, v)
    if changes:
        user.token_version += 1  # role or status changes take effect immediately
    await record_audit(
        session,
        action="users.update",
        outcome="success",
        actor_id=admin.id,
        ip=client_ip(request),
        details={"user_id": str(user_id), **{k: str(v) for k, v in changes.items()}},
    )
    await session.commit()
    await session.refresh(user)
    return UserAdminOut.model_validate(user)
