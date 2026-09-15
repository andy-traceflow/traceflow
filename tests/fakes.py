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

    def row(self, event_id: UUID) -> Event:
        """Sync accessor for assertions (the Protocol's `get` is async)."""
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

    # -- operational surface --------------------------------------------

    fail_reads_with: Exception | None = None

    def _maybe_fail(self) -> None:
        if self.fail_reads_with is not None:
            raise self.fail_reads_with

    async def count_by_status(self) -> dict[str, int]:
        self._maybe_fail()
        counts = {s.value: 0 for s in EventStatus}
        for e in self.events.values():
            counts[e.status.value] += 1
        return counts

    async def last_delivered_at(self) -> datetime | None:
        self._maybe_fail()
        stamps = [e.delivered_at for e in self.events.values() if e.delivered_at]
        return max(stamps) if stamps else None

    async def list_events(self, *, status: EventStatus | None, limit: int) -> list[Event]:
        self._maybe_fail()
        rows = [e for e in self.events.values() if status is None or e.status == status]
        rows.sort(key=lambda e: e.received_at, reverse=True)
        return rows[:limit]

    async def get(self, event_id: UUID) -> Event | None:
        self._maybe_fail()
        return self.events.get(event_id)

    async def replay(self, event_id: UUID) -> Event | None:
        self._maybe_fail()
        event = self.events.get(event_id)
        if event is None or event.status != EventStatus.DEAD:
            return None
        return self._update(event_id, status=EventStatus.RECEIVED, attempts=0, next_retry_at=None)
