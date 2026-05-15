"""Tenant model."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class ClientStatus(str, Enum):
    active = "active"
    paused = "paused"
    churned = "churned"
    trial = "trial"


class ClientTier(str, Enum):
    founding_partner = "founding_partner"
    standard = "standard"
    pro = "pro"
    full_stack = "full_stack"


class Client(BaseModel):
    id: UUID
    slug: str
    business_name: str
    status: ClientStatus = ClientStatus.active
    tier: ClientTier = ClientTier.standard
    timezone: str = "America/Los_Angeles"
    signed_at: datetime
    launched_at: datetime | None = None
    churned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ClientCreate(BaseModel):
    slug: str = Field(..., pattern=r"^[a-z0-9-]+$", min_length=2, max_length=64)
    business_name: str = Field(..., min_length=1)
    tier: ClientTier = ClientTier.standard
    timezone: str = "America/Los_Angeles"
