"""Knowledge base entries — SIA Module C.

The source taxonomy (products, substrates) is generalized into a single
`tags: list[str]` field. The per-client vocabulary lives in
client_configs.brand and is enforced at write time by the UI, not by
this model — keeps the schema flexible across verticals.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class KBEntryBase(BaseModel):
    """Shared fields for create/update."""

    question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)
    category: str = Field(default="")
    tags: list[str] = Field(default_factory=list)
    source: str = Field(default="manual")


class KBEntryCreate(KBEntryBase):
    """Body for POST /kb. client_id comes from the tenant context, not the payload."""


class KBEntryUpdate(BaseModel):
    """All fields optional for partial updates."""

    question: str | None = Field(None, min_length=1)
    answer: str | None = Field(None, min_length=1)
    category: str | None = None
    tags: list[str] | None = None
    source: str | None = None


class KBEntryRead(KBEntryBase):
    id: int
    client_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KBEntryList(BaseModel):
    data: list[KBEntryRead]
    count: int
