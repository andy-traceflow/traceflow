"""Messages exchanged on a lead (SMS, email, chat)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class MessageDirection(StrEnum):
    inbound = "inbound"
    outbound = "outbound"


class MessageChannel(StrEnum):
    sms = "sms"
    email = "email"
    chat = "chat"
    voice = "voice"


class Message(BaseModel):
    id: UUID
    client_id: UUID
    lead_id: UUID
    direction: MessageDirection
    channel: MessageChannel
    body: str
    ai_generated: bool = False
    prompt_version: str | None = None
    raw_payload: dict[str, Any] | None = Field(default=None)
    created_at: datetime

    model_config = {"from_attributes": True}
