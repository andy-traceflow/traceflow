"""Application-level audit logging helper.

Database triggers (migrations/006) cover row-level INSERT/UPDATE/DELETE
audit on tenant tables automatically. This helper exists for
application-level events that don't correspond to a single row change:
sync runs, exports, logins, integration credential rotations.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.db import get_connection

logger = logging.getLogger(__name__)


async def record_audit_event(
    *,
    client_id: UUID | None,
    operation: str,
    actor: str = "system",
    actor_user_id: UUID | None = None,
    target_table: str | None = None,
    target_id: str | None = None,
    snapshot: dict[str, Any] | None = None,
) -> None:
    """Insert an audit_log row. Best-effort — failures are logged but never raise."""
    try:
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log
                    (client_id, actor, actor_user_id, operation, target_table, target_id, snapshot)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                """,
                client_id,
                actor,
                actor_user_id,
                operation,
                target_table,
                target_id,
                snapshot,
            )
    except Exception as e:
        logger.exception("audit_log insert failed", exc_info=e)
