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

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from uuid import UUID

import asyncpg

from app.config import get_settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None
_current_tenant: ContextVar[UUID | None] = ContextVar("_current_tenant", default=None)


async def _register_codecs(conn: asyncpg.Connection) -> None:
    """Register JSON/JSONB codecs so reads return dicts/lists and writes
    accept dicts/lists directly. Without this, asyncpg passes JSONB through
    as raw JSON text and callers have to manually `json.loads`/`json.dumps`
    on both ends — easy to forget on the read side (causing
    `AttributeError: 'str' object has no attribute 'items'` deep in handler
    code that assumes a parsed dict)."""
    for typename in ("jsonb", "json"):
        await conn.set_type_codec(
            typename,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )


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
        init=_register_codecs,
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

    Wraps the connection use in a transaction so the role switch +
    tenant variable are bounded to this request and revert cleanly on
    exit (preventing cross-request leakage when pool connections are
    reused).

    Why the `SET ROLE authenticated`: Supabase's `postgres` role has
    `bypassrls=true` (see `pg_roles`), which means even `FORCE ROW
    LEVEL SECURITY` on a table does nothing for queries this connection
    runs. `authenticated` does NOT have `bypassrls`, so switching into
    it makes RLS policies actually filter. `authenticated` is granted
    full DML on every tenant-scoped table by Supabase's default GRANTs.

    Why `SET LOCAL` semantics: both the role switch and the tenant
    setting are transaction-scoped. When the caller's `async with` block
    exits, the transaction ends and both revert. Pool connection returns
    to its baseline (`postgres` role, no tenant setting) — no state can
    leak into the next request that acquires this same connection.
    """
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool() first")

    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Inside a transaction, SET ROLE behaves as SET LOCAL ROLE
            # (reverts on commit/rollback). Same for set_config(..., true).
            await conn.execute("SET ROLE authenticated")
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


@asynccontextmanager
async def get_service_connection() -> AsyncIterator[asyncpg.Connection]:
    """Acquire a service-role connection that BYPASSES RLS.

    For admin operations that cross tenants or need to read/write
    audit_log (which has no tenant policy by design). Stays on the
    default `postgres` role — we do not `SET ROLE authenticated`. Use
    sparingly: bypassing RLS is the most dangerous tool we have.
    """
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool() first")
    async with _pool.acquire() as conn:
        async with conn.transaction():
            yield conn
