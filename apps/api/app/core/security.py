"""Password hashing (Argon2id) and session tokens (HS256 JWT in an httpOnly cookie)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_hasher = PasswordHasher()
JWT_ALGORITHM = "HS256"
JWT_AUDIENCE = "apex-web"
JWT_ISSUER = "apex-api"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_access_token(subject: str, token_version: int) -> tuple[str, datetime]:
    settings = get_settings()
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {
        "sub": subject,
        "ver": token_version,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
        "aud": JWT_AUDIENCE,
        "iss": JWT_ISSUER,
    }
    token = jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=JWT_ALGORITHM)
    return token, expires


def decode_access_token(token: str) -> dict[str, object] | None:
    try:
        return jwt.decode(
            token,
            get_settings().secret_key.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["sub", "exp", "iat", "ver"]},
        )
    except jwt.PyJWTError:
        return None
