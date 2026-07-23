"""Canonical Lead — the contract every adapter translates to/from.

Mess of dealing with different CRMs and form vendors lives at the
edges (adapters); the rest of the pipeline reasons about leads in
this shape.

raw_payload is non-negotiable. Debugging an integration failure
without the original webhook body is misery.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class QualificationStatus(StrEnum):
    unqualified = "unqualified"
    qualifying = "qualifying"
    qualified = "qualified"
    high_value = "high_value"
    needs_review = "needs_review"
    spam = "spam"
    duplicate = "duplicate"
    # Terminal post-reply intent outcomes (see prompts/intent.py).
    support_touch = "support_touch"
    non_lead_contact = "non_lead_contact"
    # Hard-gate failure (out of service area, below a disqualify_if floor).
    disqualified = "disqualified"


class Classification(StrEnum):
    """What the caller IS — set pre-send, refined post-reply.

    Orthogonal to qualification_status (how far the lead got). Only
    `potential_lead` rows count toward the missed-call recovery rate.
    """

    potential_lead = "potential_lead"
    existing_customer = "existing_customer"
    known_non_lead = "known_non_lead"
    spam = "spam"


class LeadOutcome(StrEnum):
    """Whether a recovered lead ultimately booked. Orthogonal to
    qualification_status and classification; drives recovered-revenue metrics."""

    open = "open"
    won = "won"
    lost = "lost"


class OutcomeSource(StrEnum):
    """Where a lead's recovered_value came from — provenance keeps actuals
    (crm / owner_report) from being blended with the budget-bucket estimate."""

    crm = "crm"
    owner_report = "owner_report"
    estimated = "estimated"


class Lead(BaseModel):
    id: UUID
    client_id: UUID
    external_id: str | None = None
    source_system: str

    # Link up to the durable contact (migration 018). Nullable: a lead may
    # predate its backfilled contact, or the contact may have been deleted.
    contact_id: UUID | None = None

    contact_name: str | None = None
    contact_company: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None

    service_type: str | None = None
    sqft: float | None = None
    budget_range: str | None = None
    timeframe: str | None = None

    qualification_status: QualificationStatus = QualificationStatus.unqualified
    # Completeness score (migration 020): captured required weight / applicable
    # required weight × 100. NEVER blended with value_score.
    qualification_score: int | None = None
    # Deterministic estimate of the job's value, independent of completeness.
    value_score: int | None = None
    # Non-canonical captured fields (schema fields with no maps_to).
    qualification_data: dict[str, Any] = Field(default_factory=dict)
    classification: Classification = Classification.potential_lead

    # Booked outcome (recovered-revenue attribution) — set by the revenue_sync
    # CRM readback or the admin outcome endpoint; never by the AI/qualifier.
    outcome: LeadOutcome = LeadOutcome.open
    recovered_value: Decimal | None = None
    outcome_source: OutcomeSource | None = None
    outcome_recorded_at: datetime | None = None

    notes: str = ""
    raw_payload: dict[str, Any]

    # Conversation activity (migration 019) — drives resume/reopen windows.
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    turn_count: int = 0

    created_at: datetime
    qualified_at: datetime | None = None
    pushed_to_crm_at: datetime | None = None
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("notes", mode="before")
    @classmethod
    def _coerce_null_notes(cls, value: Any) -> Any:
        """`leads.notes` is nullable in Postgres, but the contract here is
        "always a string" — the CRM adapters push `lead.notes` straight into
        their payloads, so None would leak a null into a client's CRM.

        Rows written without a notes value therefore come back as None and used
        to blow up `Lead(**dict(row))` with a ValidationError, which took down
        the whole inbound-SMS reply path (the caller never got past the first
        question). Coerce to "" rather than widening the field to `str | None`,
        which would push the same None downstream. Migration 024 also backfills
        and defaults the column; this stays as defense in depth for old rows.
        """
        return "" if value is None else value


class LeadCreate(BaseModel):
    """Payload for inserting a new lead. id/timestamps are server-assigned."""

    client_id: UUID
    source_system: str
    external_id: str | None = None
    contact_name: str | None = None
    contact_company: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    service_type: str | None = None
    sqft: float | None = None
    budget_range: str | None = None
    timeframe: str | None = None
    qualification_status: QualificationStatus = QualificationStatus.unqualified
    qualification_score: int | None = None
    classification: Classification = Classification.potential_lead
    notes: str = ""
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class LeadUpdate(BaseModel):
    """Partial update; all fields optional."""

    contact_name: str | None = None
    contact_company: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    service_type: str | None = None
    sqft: float | None = None
    budget_range: str | None = None
    timeframe: str | None = None
    qualification_status: QualificationStatus | None = None
    qualification_score: int | None = None
    classification: Classification | None = None
    notes: str | None = None
    external_id: str | None = None
