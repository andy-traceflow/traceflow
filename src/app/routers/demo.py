"""Public demo bootstrap — POST /api/demo-login.

Registered OUTSIDE the /api/admin prefix on purpose: the admin gate-sweep test
asserts every /api/admin route except /login is token-gated. demo-login is an
unauthenticated token vendor (like /login), not a gated admin route, so it
lives here at /api/demo-login.

Only live when DEMO_MODE is on; returns 404 otherwise, so a non-demo deployment
exposes nothing. Mints a read-only demo-role token for a fixed synthetic
identity — no DB lookup, no password, no audit write. require_admin_user
confines that token to the in-memory FakeConn (it never loads a real
admin_users row), and forbid_demo_writes blocks every mutating verb.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

import app.services.admin_auth as admin_auth
from app.config import get_settings
from app.demo import DEMO_ADMIN_ID, DEMO_ADMIN_NAME, DEMO_EMAIL
from app.routers.admin.schemas import AdminLoginOut, AdminMeOut

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/demo-login", response_model=AdminLoginOut)
async def demo_login() -> AdminLoginOut:
    """Hand out a read-only demo session. No credentials required."""
    settings = get_settings()
    if not settings.demo_mode:
        # Don't even confirm the endpoint exists when demo mode is off.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not settings.admin_jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin auth not configured (ADMIN_JWT_SECRET unset)",
        )

    # Referenced via the module so tests can swap the secret source.
    token, expires_at = admin_auth.mint_admin_token(DEMO_ADMIN_ID, DEMO_EMAIL, "demo")
    logger.info("demo session issued")
    return AdminLoginOut(
        access_token=token,
        expires_at=expires_at,
        admin=AdminMeOut(
            id=DEMO_ADMIN_ID, email=DEMO_EMAIL, name=DEMO_ADMIN_NAME, role="demo"
        ),
    )
