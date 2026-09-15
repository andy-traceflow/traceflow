"""Portal auth: resolve a logged-in Supabase user to their tenant + set RLS context.

This is the keystone the client portal (Phase 3+) hangs off. It bridges the two
gaps that keep the existing Supabase-Auth path (``middleware/auth.py``) dormant:

  1. Nothing derives ``client_id`` from an authenticated principal — the tenant
     resolver only reads it from webhook URLs. So RLS-scoped reads via
     ``db.get_connection()`` return zero rows for a logged-in user.
  2. Nothing sets the tenant context for non-webhook requests, so
     ``services.permissions.get_user_permissions`` always falls back to defaults.

World B in ADR-0004: Supabase Auth identity + ``user_permissions`` RBAC, kept
entirely separate from the platform ``admin_users`` world (``/api/admin``).

Flow (per request):
  1. ``verify_jwt`` validates the Supabase JWT → ``AuthUser`` (401 on bad token).
  2. Look up the user's tenant membership in ``user_permissions``. This lookup
     MUST bypass RLS — it is the query that tells us which ``client_id`` to
     scope to, so it can't itself be tenant-scoped (chicken-and-egg). It uses
     ``get_service_connection()`` and filters explicitly by ``user_id``.
  3. Resolve the ACTIVE tenant: a single membership resolves to that client; a
     user in multiple tenants must name one via the ``X-Client-Id`` header,
     validated against their membership set (403 otherwise).
  4. ``set_current_tenant(active)`` so every subsequent ``get_connection()`` in
     the request scopes via RLS, then clear it on the way out — mirroring the
     finally-reset in ``tenant_resolver_middleware`` so context never leaks
     onto the next request that reuses this task.

This module intentionally exposes only dependencies — no routes are wired here.
Standing up ``/api/portal/*`` and a client UI on top of it is deferred (the
"no client-facing UI until Client 8" rule); this is the plumbing that makes
that a UI-on-top exercise rather than a re-architecture.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status

from app.db import get_service_connection, set_current_tenant
from app.middleware.auth import AuthUser, verify_jwt
from app.services.permissions import UserPermissions

_PERM_COLUMNS = (
    "can_edit_kb",
    "can_delete_kb",
    "can_export",
    "can_view_leads",
    "can_edit_config",
    "is_admin",
)


@dataclass(frozen=True)
class PortalPrincipal:
    """A logged-in client-portal user, resolved to one active tenant.

    ``permissions`` are the user's flags FOR ``client_id`` — already loaded, so
    permission gates read them directly rather than re-querying.
    """

    user_id: str
    email: str | None
    client_id: UUID
    permissions: UserPermissions


async def _load_memberships(user_id: str) -> dict[UUID, UserPermissions]:
    """Every (client_id → permissions) pair this user belongs to.

    Service-role connection: this is the lookup that resolves tenant scope, so
    it cannot be RLS-scoped itself. Read-only, filtered to one ``user_id`` — it
    never crosses into another user's rows.
    """
    async with get_service_connection() as conn:
        rows = await conn.fetch(
            f"""
            SELECT client_id, {", ".join(_PERM_COLUMNS)}
            FROM user_permissions
            WHERE user_id = $1::uuid
            """,
            user_id,
        )
    return {
        row["client_id"]: UserPermissions(**{col: bool(row[col]) for col in _PERM_COLUMNS})
        for row in rows
    }


def _resolve_active_tenant(
    memberships: dict[UUID, UserPermissions], requested: str | None
) -> UUID:
    """Pick the tenant this request operates on, validated against membership."""
    if requested is not None:
        try:
            requested_id = UUID(requested)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-Client-Id header",
            ) from e
        if requested_id not in memberships:
            # Don't distinguish "no such tenant" from "not yours" — both are 403.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No access to the requested tenant",
            )
        return requested_id

    if len(memberships) == 1:
        return next(iter(memberships))

    # Zero handled by the caller (403); >1 needs an explicit choice.
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Multiple tenants available — specify the X-Client-Id header",
    )


async def portal_principal(
    user: AuthUser = Depends(verify_jwt),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> AsyncIterator[PortalPrincipal]:
    """Dependency: authenticate a portal user and scope the request to their tenant.

    Yields so tenant context is torn down after the response — the same
    set/finally-clear discipline the webhook middleware uses.

    Usage:
        @router.get("/api/portal/leads")
        async def leads(principal: PortalPrincipal = Depends(portal_principal)):
            async with get_connection() as conn:   # already RLS-scoped
                ...
    """
    memberships = await _load_memberships(user.user_id)
    if not memberships:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has no tenant access",
        )

    active = _resolve_active_tenant(memberships, x_client_id)

    set_current_tenant(active)
    try:
        yield PortalPrincipal(
            user_id=user.user_id,
            email=user.email,
            client_id=active,
            permissions=memberships[active],
        )
    finally:
        set_current_tenant(None)


def require_portal_permission(
    perm_name: str,
) -> Callable[..., Awaitable[PortalPrincipal]]:
    """Dependency factory: portal auth + a specific permission flag.

    Reads the flag off the already-resolved principal (no extra query).

    Usage:
        @router.delete("/...", dependencies=[Depends(require_portal_permission("can_delete_kb"))])
    """

    async def _check(
        principal: PortalPrincipal = Depends(portal_principal),
    ) -> PortalPrincipal:
        if not getattr(principal.permissions, perm_name, False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {perm_name}",
            )
        return principal

    return _check


async def require_portal_admin(
    principal: PortalPrincipal = Depends(portal_principal),
) -> PortalPrincipal:
    """Shortcut: the caller must be a tenant-admin of the active client."""
    if not principal.permissions.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant admin access required",
        )
    return principal
