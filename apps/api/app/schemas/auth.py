from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


def check_strength(v: str) -> str:
    classes = sum(
        [
            any(c.islower() for c in v),
            any(c.isupper() for c in v),
            any(c.isdigit() for c in v),
            any(not c.isalnum() for c in v),
        ]
    )
    if len(v) < 12 or classes < 3:
        raise ValueError("Password must be at least 12 characters with 3 of: lowercase, uppercase, digit, symbol")
    return v


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)

    @field_validator("new_password")
    @classmethod
    def _strength(cls, v: str) -> str:
        return check_strength(v)


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    role: str = Field(default="viewer", pattern="^(admin|viewer)$")

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        return check_strength(v)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str | None = Field(default=None, pattern="^(admin|viewer)$")
    is_active: bool | None = None


class UserAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class BootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    bootstrap_token: str | None = Field(default=None, max_length=256)

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        return check_strength(v)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    role: str
    created_at: datetime
    last_login_at: datetime | None


class SessionOut(BaseModel):
    user: UserOut
    expires_at: datetime


class BootstrapStatus(BaseModel):
    needs_bootstrap: bool
    bootstrap_token_required: bool
