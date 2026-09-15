"""Event store — the durable queue between the webhook handler and the worker.

All SQL against the `events` table lives here. The handler and the worker
talk to the `EventStore` Protocol, which lets tests run against an
in-memory implementation (tests/fakes.py) while the Postgres one is
exercised by the DB-backed suite.

Dedupe is `INSERT ... ON CONFLICT (source, webhook_id) DO NOTHING
RETURNING id` — committed in the same transaction as receipt, so a
redelivery is only ever absorbed once the original row durably exists.
Delivery success is tracked separately (status), so a failed delivery
never suppresses a retry.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.db import get_connection
from app.models.event import Event, EventStatus

logger = logging.getLogger(__name__)

# Keep last_error bounded; a destination can return a very large body.
MAX_ERROR_LENGTH = 2000


class EventStore(Protocol):
    async def insert(
        self,
        *,
        source: str,
        topic: str,
        webhook_id: str,
        shop_domain: str | None,
        payload: Any,
    ) -> UUID | None:
        """Persist a received event. Returns None if (source, webhook_id) was already seen."""
        ...

    async def claim(self, *, limit: int, lease_seconds: int) -> list[Event]:
        """Atomically move up to `limit` due events to `processing` and return them.

        Due = `received` with no/past `next_retry_at`, or `processing` whose
        lease (`next_retry_at`) has expired. Uses FOR UPDATE SKIP LOCKED so
        concurrent workers never claim the same row.
        """
        ...

    async def mark_delivered(self, event_id: UUID, *, attempts: int, external_id: str) -> None: ...

    async def schedule_retry(
        self, event_id: UUID, *, attempts: int, error: str, next_retry_at: datetime
    ) -> None: ...

    async def mark_dead(self, event_id: UUID, *, attempts: int, error: str) -> None: ...

    # -- operational surface (health, triage, replay) ------------------

    async def count_by_status(self) -> dict[str, int]:
        """{'received': n, 'processing': n, 'delivered': n, 'dead': n} — every key present."""
        ...

    async def last_delivered_at(self) -> datetime | None: ...

    async def list_events(self, *, status: EventStatus | None, limit: int) -> list[Event]:
        """Newest first."""
        ...

    async def get(self, event_id: UUID) -> Event | None: ...

    async def replay(self, event_id: UUID) -> Event | None:
        """dead → received with a fresh attempt budget. None if not found or not dead."""
        ...


def _truncate(error: str) -> str:
    return error if len(error) <= MAX_ERROR_LENGTH else error[: MAX_ERROR_LENGTH - 1] + "…"


class PostgresEventStore:
    """EventStore over the asyncpg pool in app.db. Each method is one transaction."""

    async def insert(
        self,
        *,
        source: str,
        topic: str,
        webhook_id: str,
        shop_domain: str | None,
        payload: Any,
    ) -> UUID | None:
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO events (source, topic, webhook_id, shop_domain, payload)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (source, webhook_id) DO NOTHING
                RETURNING id
                """,
                source,
                topic,
                webhook_id,
                shop_domain,
                payload,
            )
        return row["id"] if row else None

    async def claim(self, *, limit: int, lease_seconds: int) -> list[Event]:
        async with get_connection() as conn:
            rows = await conn.fetch(
                """
                UPDATE events
                   SET status = 'processing',
                       next_retry_at = now() + make_interval(secs => $2)
                 WHERE id IN (
                       SELECT id
                         FROM events
                        WHERE (status = 'received'
                               AND (next_retry_at IS NULL OR next_retry_at <= now()))
                           OR (status = 'processing' AND next_retry_at <= now())
                        ORDER BY received_at
                        LIMIT $1
                          FOR UPDATE SKIP LOCKED
                 )
                RETURNING *
                """,
                limit,
                float(lease_seconds),
            )
        return [Event.model_validate(dict(r)) for r in rows]

    async def mark_delivered(self, event_id: UUID, *, attempts: int, external_id: str) -> None:
        async with get_connection() as conn:
            await conn.execute(
                """
                UPDATE events
                   SET status = 'delivered',
                       attempts = $2,
                       external_id = $3,
                       delivered_at = now(),
                       last_error = NULL,
                       next_retry_at = NULL
                 WHERE id = $1
                """,
                event_id,
                attempts,
                external_id,
            )

    async def schedule_retry(
        self, event_id: UUID, *, attempts: int, error: str, next_retry_at: datetime
    ) -> None:
        async with get_connection() as conn:
            await conn.execute(
                """
                UPDATE events
                   SET status = 'received',
                       attempts = $2,
                       last_error = $3,
                       next_retry_at = $4
                 WHERE id = $1
                """,
                event_id,
                attempts,
                _truncate(error),
                next_retry_at,
            )

    async def mark_dead(self, event_id: UUID, *, attempts: int, error: str) -> None:
        async with get_connection() as conn:
            await conn.execute(
                """
                UPDATE events
                   SET status = 'dead',
                       attempts = $2,
                       last_error = $3,
                       next_retry_at = NULL
                 WHERE id = $1
                """,
                event_id,
                attempts,
                _truncate(error),
            )

    # -- operational surface --------------------------------------------

    async def count_by_status(self) -> dict[str, int]:
        async with get_connection() as conn:
            rows = await conn.fetch("SELECT status, count(*) AS n FROM events GROUP BY status")
        counts = {s.value: 0 for s in EventStatus}
        for r in rows:
            counts[r["status"]] = int(r["n"])
        return counts

    async def last_delivered_at(self) -> datetime | None:
        async with get_connection() as conn:
            value = await conn.fetchval("SELECT max(delivered_at) FROM events")
        return value

    async def list_events(self, *, status: EventStatus | None, limit: int) -> list[Event]:
        async with get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM events
                 WHERE ($1::text IS NULL OR status = $1)
                 ORDER BY received_at DESC
                 LIMIT $2
                """,
                status.value if status else None,
                limit,
            )
        return [Event.model_validate(dict(r)) for r in rows]

    async def get(self, event_id: UUID) -> Event | None:
        async with get_connection() as conn:
            row = await conn.fetchrow("SELECT * FROM events WHERE id = $1", event_id)
        return Event.model_validate(dict(row)) if row else None

    async def replay(self, event_id: UUID) -> Event | None:
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                UPDATE events
                   SET status = 'received',
                       attempts = 0,
                       next_retry_at = NULL
                 WHERE id = $1 AND status = 'dead'
                RETURNING *
                """,
                event_id,
            )
        return Event.model_validate(dict(row)) if row else None


def get_event_store() -> EventStore:
    """FastAPI dependency / worker factory. Tests override this."""
    return PostgresEventStore()
