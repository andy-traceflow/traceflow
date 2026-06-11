"""Unit tests for the admin auth core (services/admin_auth.py).

Pure-unit: settings and the DB are patched at the module's own import
sites, so no network, no database, no FastAPI app. The require_admin_user
gate is exercised by calling the dependency directly with constructed
HTTPAuthorizationCredentials — same style the old verify_admin_token tests
used.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.services.admin_auth import (
    JWT_ALGORITHM,
    LoginRateLimiter,
    hash_password,
    mint_admin_token,
    require_admin_user,
    verify_password,
)

# 32+ bytes — PyJWT warns below RFC 7518's minimum for HS256.
SECRET = "test-admin-secret-0123456789abcdef"


def _settings(secret: str = SECRET) -> SimpleNamespace:
    return SimpleNamespace(admin_jwt_secret=secret)


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _admin_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": uuid4(),
        "email": "andy@traceflow.app",
        "name": "Andy",
        "role": "owner",
        "is_active": True,
        "last_login_at": None,
    }
    base.update(overrides)
    return base


def _service_conn(row: dict[str, Any] | None):
    conn = AsyncMock()
    conn.fetchrow.return_value = row

    @asynccontextmanager
    async def _ctx():
        yield conn

    return _ctx


# ===========================================================================
# Password hashing
# ===========================================================================


def test_hash_verify_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong password", hashed) is False


def test_hashes_are_salted():
    assert hash_password("same input") != hash_password("same input")


def test_malformed_stored_hash_fails_closed():
    assert verify_password("anything", "not-a-bcrypt-hash") is False
    assert verify_password("anything", "") is False


# ===========================================================================
# Token mint + decode
# ===========================================================================


def test_mint_roundtrip_claims():
    admin_id = uuid4()
    with patch("app.services.admin_auth.get_settings", return_value=_settings()):
        token, expires_at = mint_admin_token(admin_id, "andy@traceflow.app", "owner")
    payload = jwt.decode(token, SECRET, algorithms=[JWT_ALGORITHM])
    assert payload["sub"] == str(admin_id)
    assert payload["email"] == "andy@traceflow.app"
    assert payload["role"] == "owner"
    assert payload["exp"] - payload["iat"] == int(timedelta(hours=12).total_seconds())
    assert expires_at > datetime.now(UTC)


def test_mint_requires_secret():
    with patch("app.services.admin_auth.get_settings", return_value=_settings("")):
        with pytest.raises(RuntimeError, match="ADMIN_JWT_SECRET"):
            mint_admin_token(uuid4(), "a@b.c", "owner")


def _token(
    *,
    sub: str | None = None,
    secret: str = SECRET,
    expires_in: timedelta = timedelta(hours=1),
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {"iat": now, "exp": now + expires_in}
    if sub is not None:
        payload["sub"] = sub
    payload.update(extra or {})
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


# ===========================================================================
# require_admin_user — the gate
# ===========================================================================


async def _gate(token: str, row: dict[str, Any] | None, secret: str = SECRET):
    with (
        patch("app.services.admin_auth.get_settings", return_value=_settings(secret)),
        patch("app.services.admin_auth.get_service_connection", new=_service_conn(row)),
    ):
        return await require_admin_user(_creds(token))


@pytest.mark.asyncio
async def test_gate_accepts_valid_token_and_active_admin():
    row = _admin_row()
    admin = await _gate(_token(sub=str(row["id"])), row)
    assert admin.id == row["id"]
    assert admin.email == "andy@traceflow.app"
    assert admin.role == "owner"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    [
        "garbage",
        _token(sub=str(uuid4()), secret="wrong-secret-0123456789abcdefghij"),
        _token(sub=str(uuid4()), expires_in=timedelta(seconds=-10)),  # expired
        _token(sub=None),  # missing required sub claim
        _token(sub="not-a-uuid"),
    ],
)
async def test_gate_rejects_bad_tokens_with_401(token: str):
    with pytest.raises(HTTPException) as exc:
        await _gate(token, _admin_row())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_gate_unknown_admin_is_401():
    with pytest.raises(HTTPException) as exc:
        await _gate(_token(sub=str(uuid4())), row=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_gate_inactive_admin_is_403():
    row = _admin_row(is_active=False)
    with pytest.raises(HTTPException) as exc:
        await _gate(_token(sub=str(row["id"])), row)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_gate_unconfigured_secret_is_503():
    with pytest.raises(HTTPException) as exc:
        await _gate(_token(sub=str(uuid4())), _admin_row(), secret="")
    assert exc.value.status_code == 503


# ===========================================================================
# LoginRateLimiter
# ===========================================================================


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limiter_locks_after_max_failures():
    clock = _Clock()
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=900, clock=clock)
    for _ in range(5):
        limiter.check("1.2.3.4")
        limiter.record_failure("1.2.3.4")
    with pytest.raises(HTTPException) as exc:
        limiter.check("1.2.3.4")
    assert exc.value.status_code == 429
    retry_after = int(exc.value.headers["Retry-After"])
    assert 1 <= retry_after <= 901


def test_limiter_window_slides_open():
    clock = _Clock()
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=900, clock=clock)
    limiter.record_failure("ip")
    clock.now += 500
    limiter.record_failure("ip")
    with pytest.raises(HTTPException):
        limiter.check("ip")
    clock.now += 401  # first failure now outside the 900s window
    limiter.check("ip")  # one slot free again


def test_limiter_success_resets_and_ips_are_independent():
    clock = _Clock()
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=900, clock=clock)
    limiter.record_failure("a")
    limiter.record_failure("b")
    with pytest.raises(HTTPException):
        limiter.check("a")
    limiter.reset("a")
    limiter.check("a")  # cleared
    with pytest.raises(HTTPException):
        limiter.check("b")  # untouched
