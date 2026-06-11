"""Admin session endpoints: POST /api/admin/login, GET /api/admin/me.

/login is the only public admin route — rate-limited per IP over failures,
timing-equalized for unknown emails, and deliberately generic in its 401s
(wrong password, unknown email, and disabled account all return the same
body, so the endpoint never confirms an email exists). Everything else in
the admin surface hangs off require_admin_user.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

import app.services.admin_auth as admin_auth
from app.config import get_settings
from app.db import get_service_connection
from app.services.admin_auth import AdminInfo, require_admin_user
from app.services.audit import record_audit_event

from .schemas import AdminLoginIn, AdminLoginOut, AdminMeOut

logger = logging.getLogger(__name__)

router = APIRouter()

_INVALID_CREDENTIALS = "Invalid email or password"


@router.post("/login", response_model=AdminLoginOut)
async def login(body: AdminLoginIn, request: Request) -> AdminLoginOut:
    settings = get_settings()
    if not settings.admin_jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin auth not configured (ADMIN_JWT_SECRET unset)",
        )

    ip = admin_auth.client_ip(request)
    # Referenced via the module attribute so tests can swap the limiter.
    admin_auth.login_limiter.check(ip)

    email = body.email.strip().lower()
    async with get_service_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, email, name, role, is_active, password_hash, last_login_at
            FROM admin_users
            WHERE email = $1
            """,
            email,
        )

    if row is None:
        admin_auth.equalize_timing(body.password)
        admin_auth.login_limiter.record_failure(ip)
        raise HTTPException(status_code=401, detail=_INVALID_CREDENTIALS)

    if not admin_auth.verify_password(body.password, row["password_hash"]):
        admin_auth.login_limiter.record_failure(ip)
        raise HTTPException(status_code=401, detail=_INVALID_CREDENTIALS)

    if not row["is_active"]:
        # Same generic 401 as a bad password — login never confirms accounts.
        admin_auth.login_limiter.record_failure(ip)
        raise HTTPException(status_code=401, detail=_INVALID_CREDENTIALS)

    admin_auth.login_limiter.reset(ip)
    token, expires_at = admin_auth.mint_admin_token(row["id"], row["email"], row["role"])

    async with get_service_connection() as conn:
        await conn.execute(
            "UPDATE admin_users SET last_login_at = NOW() WHERE id = $1", row["id"]
        )

    await record_audit_event(
        client_id=None,
        operation="login",
        actor=row["email"],
        actor_user_id=row["id"],
        target_table="admin_users",
        target_id=str(row["id"]),
    )
    logger.info("admin login", extra={"admin_id": str(row["id"])})

    return AdminLoginOut(
        access_token=token,
        expires_at=expires_at,
        admin=AdminMeOut(
            id=row["id"],
            email=row["email"],
            name=row["name"],
            role=row["role"],
            last_login_at=row["last_login_at"],
        ),
    )


@router.get("/me", response_model=AdminMeOut)
async def me(admin: AdminInfo = Depends(require_admin_user)) -> AdminMeOut:
    """Session probe — the SPA calls this on load to validate a stored token."""
    return AdminMeOut(
        id=admin.id,
        email=admin.email,
        name=admin.name,
        role=admin.role,
        last_login_at=admin.last_login_at,
    )
