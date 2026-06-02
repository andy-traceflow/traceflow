"""Canonical CRM contact — minimal shape returned by adapter phone lookups.

Caller classification (lead lifecycle Section 3) asks the client's CRM
"do you already know this number?" before any SMS/AI is spent. Adapters
answer with this canonical type so the classifier never reasons about
provider-specific contact payloads.

contact_type is derived per-adapter from the CRM's own tagging / pipeline
stage; how a given client's tags map to customer-vs-vendor is itself
per-client config.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ContactType(StrEnum):
    customer = "customer"
    vendor = "vendor"
    lead = "lead"
    unknown = "unknown"


class CRMContact(BaseModel):
    external_id: str
    name: str | None = None
    tags: list[str] = Field(default_factory=list)
    contact_type: ContactType = ContactType.unknown
