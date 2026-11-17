from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Environment, Settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    h = hash_password("Correct-Horse-42!")
    assert h.startswith("$argon2id$")
    assert verify_password("Correct-Horse-42!", h)
    assert not verify_password("correct-horse-42!", h)
    assert not verify_password("x", "not-a-hash")


def test_token_roundtrip_and_claims() -> None:
    token, _ = create_access_token("abc", 3)
    claims = decode_access_token(token)
    assert claims is not None
    assert claims["sub"] == "abc" and claims["ver"] == 3
    assert decode_access_token(token + "x") is None
    assert decode_access_token("") is None


def test_production_rejects_insecure_defaults() -> None:
    with pytest.raises(ValidationError):
        Settings(environment=Environment.PRODUCTION)
    ok = Settings(
        environment=Environment.PRODUCTION,
        secret_key=SecretStr("x" * 48),
        bootstrap_token=SecretStr("y" * 32),
        cookie_secure=True,
    )
    assert ok.environment == Environment.PRODUCTION
