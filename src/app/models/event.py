"""The one persisted shape: a row in the `events` table.

See migrations/001_create_events.sql for the lifecycle and the dual
meaning of `next_retry_at`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EventStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    DELIVERED = "delivered"
    DEAD = "dead"


class Event(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    topic: str
    webhook_id: str
    shop_domain: str | None = None
    payload: Any
    status: EventStatus = EventStatus.RECEIVED
    attempts: int = 0
    last_error: str | None = None
    next_retry_at: datetime | None = None
    external_id: str | None = None
    received_at: datetime
    delivered_at: datetime | None = None
