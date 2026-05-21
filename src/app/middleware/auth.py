"""JWT verification for admin and client-portal endpoints.

Supabase signs auth tokens with asymmetric keys (ES256 by default,
sometimes RS256). The public keys are published at the project's JWKS
endpoint; PyJWKClient caches them for an hour.

For platform-internal admin endpoints (founder-only), use an HS256 token
signed with ADMIN_JWT_SECRET — separate code path, separate auth scope.

All permission-checking dependencies are async so the underlying DB
lookups don't need to bridge sync→async at every call site.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import get_settings

bearer_scheme = HTTPBearer(auto_error=True)


@dataclass
class AuthUser:
    user_id: str
    email: str | None
    role: str


@lru_cache
def _jwks_client() -> PyJWKClient:
    settings = get_settings()
    if not settings.supabase_url:
        raise RuntimeError("SUPABASE_URL not set; cannot build JWKS URL")
    return PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json")


def verify_jwt(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> AuthUser:
    """Verify a Supabase-issued JWT against the project's published JWKS.

    Returns the authenticated user. Raises 401 on invalid/expired tokens.
    """
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(creds.credentials)
        payload = jwt.decode(
            creds.credentials,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status_code=401, detail="Token expired") from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}") from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"JWT verification error: {e}",
        ) from e

    return AuthUser(
        user_id=payload.get("sub", ""),
        email=payload.get("email"),
        role=payload.get("role", "authenticated"),
    )


def require_permission(perm_name: str) -> Callable[..., Awaitable[AuthUser]]:
    """Dependency factory: verifies JWT, then checks a specific permission flag.

    Permissions are looked up per (client_id, user_id) from user_permissions.
    The middleware-set tenant context determines which client_id is checked.
    Raises 403 if the flag is missing or false.

    Usage:
        @router.delete("/...", dependencies=[Depends(require_permission("can_delete_kb"))])
    """
    from app.services.permissions import get_user_permissions  # local import to avoid cycle

    async def _check(user: AuthUser = Depends(verify_jwt)) -> AuthUser:
        perms = await get_user_permissions(user.user_id)
        if not getattr(perms, perm_name, False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {perm_name}",
            )
        return user

    return _check


async def require_admin(user: AuthUser = Depends(verify_jwt)) -> AuthUser:
    """Shortcut for require_permission('is_admin')."""
    from app.services.permissions import get_user_permissions

    perms = await get_user_permissions(user.user_id)
    if not perms.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
