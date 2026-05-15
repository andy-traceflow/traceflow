"""asyncpg connection pool with per-request tenant context.

The pool itself uses a single Postgres role (the Supabase service role).
Tenant isolation is enforced by Row Level Security policies that read
`app.current_client_id` — a Postgres session variable we set at the
start of every connection acquisition based on the request's
ContextVar.

For background jobs and admin code, use `set_tenant_context()` to
explicitly scope a block of work to one tenant.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator
from uuid import UUID

import asyncpg

from app.config import get_settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None
_current_tenant: ContextVar[UUID | None] = ContextVar("_current_tenant", default=None)


async def init_pool() -> None:
    """Create the global connection pool. Called once at app startup."""
    global _pool
    settings = get_settings()
    if not settings.supabase_db_url:
        logger.warning("SUPABASE_DB_URL not set — DB pool not initialized")
        return
    _pool = await asyncpg.create_pool(
        dsn=settings.supabase_db_url,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    logger.info("Database pool initialized")


async def close_pool() -> None:
    """Tear down the pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")


def set_current_tenant(client_id: UUID | None) -> None:
    """Set the current request's tenant context (used by middleware)."""
    _current_tenant.set(client_id)


def get_current_tenant() -> UUID | None:
    return _current_tenant.get()


@asynccontextmanager
async def get_connection() -> AsyncIterator[asyncpg.Connection]:
    """Acquire a connection scoped to the current tenant context.

    Sets `app.current_client_id` as a session variable so RLS policies
    can filter rows. When no tenant is set (e.g. admin operations),
    the session variable is left unset and RLS denies tenant-scoped
    reads — admin code must use the service role explicitly.
    """
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool() first")

    async with _pool.acquire() as conn:
        client_id = _current_tenant.get()
        if client_id is not None:
            await conn.execute(
                "SELECT set_config('app.current_client_id', $1, true)",
                str(client_id),
            )
        yield conn


@asynccontextmanager
async def set_tenant_context(client_id: UUID) -> AsyncIterator[asyncpg.Connection]:
    """Explicitly scope a block of code to one tenant.

    Used by background jobs, admin operations, and tests that need to
    iterate over tenants without going through the request middleware.
    """
    token = _current_tenant.set(client_id)
    try:
        async with get_connection() as conn:
            yield conn
    finally:
        _current_tenant.reset(token)
