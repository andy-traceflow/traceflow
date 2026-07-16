"""Canonical Contact — the durable caller identity that lives above the lead.

Phone used to live only on `leads`, so a caller's memory died with the lead.
The contact is the person record: one row per (client_id, phone), with leads
hanging off it. It carries ONE vocabulary for "what is this caller"
(`ContactType`) plus provenance (`ContactTypeSource`) so an AI inference can
never silently overwrite a human decision — the precedence rule lives in
services/contacts.set_contact_type.

`known_facts` is PERSON-scoped only (see PERSON_FACT_KEYS). Project-scoped
values — sqft, material, budget_range, timeframe, project_stage — die with the
lead and must never be written here.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.crm_contact import ContactType as CRMContactType


class ContactType(StrEnum):
    """What a caller IS. The single vocabulary the whole platform routes on.

    unknown  — never seen; spam-scored on call.
    prospect — engaged, not yet a customer; NEVER spam-scored (an invisible
               revenue leak if it were).
    customer — existing customer; priority service routing, no sales qualifying.
    vendor   — supplier/partner; nuisance, minimal or no ack.
    spam     — an INFERENCE; revocable.
    blocked  — a HUMAN decision; never overwritten by any classifier/scorer/CRM.
    """

    unknown = "unknown"
    prospect = "prospect"
    customer = "customer"
    vendor = "vendor"
    spam = "spam"
    blocked = "blocked"


class ContactTypeSource(StrEnum):
    """Who decided the contact_type. Precedence: manual > crm > inferred."""

    manual = "manual"
    crm = "crm"
    inferred = "inferred"


# The ONLY keys allowed in contacts.known_facts. Person-scoped and durable —
# they describe the human, not a project. Project-scoped fields die with the lead.
PERSON_FACT_KEYS: frozenset[str] = frozenset(
    {"contact_name", "address", "zip", "preferred_contact_time"}
)

# The CRM adapter boundary keeps its own minimal ContactType (crm_contact.py);
# it maps INTO this vocabulary here. A CRM 'lead' is a prospect to us. spam and
# blocked have no CRM origin.
_CRM_TYPE_MAP: dict[CRMContactType, ContactType] = {
    CRMContactType.customer: ContactType.customer,
    CRMContactType.vendor: ContactType.vendor,
    CRMContactType.lead: ContactType.prospect,
    CRMContactType.unknown: ContactType.unknown,
}


def from_crm_contact_type(crm_type: CRMContactType) -> ContactType:
    """Map an adapter-boundary CRM contact type into the canonical vocabulary."""
    return _CRM_TYPE_MAP.get(crm_type, ContactType.unknown)


class Contact(BaseModel):
    id: UUID
    client_id: UUID
    phone: str
    name: str | None = None

    contact_type: ContactType = ContactType.unknown
    contact_type_source: ContactTypeSource = ContactTypeSource.inferred
    contact_type_at: datetime | None = None
    contact_type_reason: str | None = None

    crm_external_id: str | None = None

    known_facts: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
    last_intent: str | None = None

    call_count: int = 0
    lead_count: int = 0

    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ContactLeadRef(BaseModel):
    """A lead as it appears in a contact's history — id, status, and the
    project-scoped fields worth surfacing when recognizing a returning caller."""

    id: UUID
    qualification_status: str
    created_at: datetime
    service_type: str | None = None
    sqft: float | None = None
    budget_range: str | None = None
    timeframe: str | None = None


class ContactHistory(BaseModel):
    """A contact plus its recent leads. Assembled by contacts.contact_history."""

    contact: Contact
    leads: list[ContactLeadRef] = Field(default_factory=list)
