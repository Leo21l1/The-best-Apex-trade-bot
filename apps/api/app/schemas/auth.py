from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class BootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    bootstrap_token: str | None = Field(default=None, max_length=256)

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        classes = sum(
            [
                any(c.islower() for c in v),
                any(c.isupper() for c in v),
                any(c.isdigit() for c in v),
                any(not c.isalnum() for c in v),
            ]
        )
        if classes < 3:
            raise ValueError(
                "Password must contain at least 3 of: lowercase, uppercase, digit, symbol"
            )
        return v


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
