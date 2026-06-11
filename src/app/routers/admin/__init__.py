"""The self-hosted admin surface — /api/admin/* (ADR-0004, replaces Retool).

Auth model: POST /login (the only ungated route) issues a 12h HS256 JWT for
an admin_users account; every other route is gated by require_admin_user,
declared as a ROUTER-LEVEL dependency on each submodule so a forgotten
per-handler dependency can't open a hole. Handlers that audit also take the
AdminInfo parameter — FastAPI caches the dependency per request, so the
gate still runs exactly once.

Tenant isolation here is NOT RLS (admin work is cross-tenant by design and
uses the service-role connection): it is the explicit client_id filter in
every SQL statement, scoped from the URL path. See each submodule's
"ISOLATION INVARIANT" docstring.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import activity, auth, clients, leads, mappings

router = APIRouter(prefix="/api/admin", tags=["admin"])
router.include_router(auth.router)
router.include_router(clients.router)
router.include_router(leads.router)
router.include_router(activity.router)
router.include_router(mappings.router)
