"""Low-level event stream for debugging and analytics.

Anything noteworthy — webhook received, SMS sent, CRM pushed, qualifier
ran — drops an Event with its full payload.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class Event(BaseModel):
    id: UUID
    client_id: UUID
    lead_id: UUID | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}
