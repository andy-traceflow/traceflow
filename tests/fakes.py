"""In-memory EventStore for unit tests. Mirrors PostgresEventStore semantics:
dedupe on (source, webhook_id), due-set rules, and the processing lease."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.models.event import Event, EventStatus


class InMemoryEventStore:
    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self.events: dict[UUID, Event] = {}
        self._now = now or (lambda: datetime.now(UTC))
        self.fail_insert_with: Exception | None = None

    # -- helpers for assertions -------------------------------------------

    def get(self, event_id: UUID) -> Event:
        return self.events[event_id]

    def by_status(self, status: EventStatus) -> list[Event]:
        return [e for e in self.events.values() if e.status == status]

    def _update(self, event_id: UUID, **changes: Any) -> Event:
        updated = self.events[event_id].model_copy(update=changes)
        self.events[event_id] = updated
        return updated

    # -- EventStore ------------------------------------------------------

    async def insert(
        self,
        *,
        source: str,
        topic: str,
        webhook_id: str,
        shop_domain: str | None,
        payload: Any,
    ) -> UUID | None:
        if self.fail_insert_with is not None:
            raise self.fail_insert_with
        for existing in self.events.values():
            if existing.source == source and existing.webhook_id == webhook_id:
                return None
        event = Event(
            id=uuid4(),
            source=source,
            topic=topic,
            webhook_id=webhook_id,
            shop_domain=shop_domain,
            payload=payload,
            received_at=self._now(),
        )
        self.events[event.id] = event
        return event.id

    async def claim(self, *, limit: int, lease_seconds: int) -> list[Event]:
        now = self._now()
        due = [
            e
            for e in sorted(self.events.values(), key=lambda e: e.received_at)
            if (
                e.status == EventStatus.RECEIVED
                and (e.next_retry_at is None or e.next_retry_at <= now)
            )
            or (
                e.status == EventStatus.PROCESSING
                and e.next_retry_at is not None
                and e.next_retry_at <= now
            )
        ][:limit]
        return [
            self._update(
                e.id,
                status=EventStatus.PROCESSING,
                next_retry_at=now + timedelta(seconds=lease_seconds),
            )
            for e in due
        ]

    async def mark_delivered(self, event_id: UUID, *, attempts: int, external_id: str) -> None:
        self._update(
            event_id,
            status=EventStatus.DELIVERED,
            attempts=attempts,
            external_id=external_id,
            delivered_at=self._now(),
            last_error=None,
            next_retry_at=None,
        )

    async def schedule_retry(
        self, event_id: UUID, *, attempts: int, error: str, next_retry_at: datetime
    ) -> None:
        self._update(
            event_id,
            status=EventStatus.RECEIVED,
            attempts=attempts,
            last_error=error,
            next_retry_at=next_retry_at,
        )

    async def mark_dead(self, event_id: UUID, *, attempts: int, error: str) -> None:
        self._update(
            event_id,
            status=EventStatus.DEAD,
            attempts=attempts,
            last_error=error,
            next_retry_at=None,
        )
