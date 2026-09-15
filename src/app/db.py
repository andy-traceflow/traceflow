"""asyncpg connection pool.

Single-tenant: one deployment, one database, one Postgres role. There is
no Row Level Security and no per-request tenant context — every row in
this database belongs to the one client this service is deployed for.

`get_connection()` hands out a pooled connection wrapped in a transaction
so each acquire/release cycle returns the connection to the pool in a
clean state.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.config import get_settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


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


@asynccontextmanager
async def get_connection() -> AsyncIterator[asyncpg.Connection]:
    """Acquire a pooled connection inside a transaction.

    The transaction commits when the caller's `async with` block exits
    cleanly and rolls back on exception, so partial writes never leak
    into the pool's next user.
    """
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool() first")

    async with _pool.acquire() as conn:
        async with conn.transaction():
            yield conn
