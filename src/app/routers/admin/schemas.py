"""Request/response models for the /api/admin surface.

Reality notes baked into these shapes (don't "fix" them back to the spec):
- classification values are potential_lead | existing_customer |
  known_non_lead | spam (migration 014); intent is NOT a leads column — it
  rides the latest intent_classified event (IntentInfo).
- business_name / tier / timezone / slug / status live on clients, not
  client_configs, so the config payload is a JOIN of both.
- Secrets never leave the API: crm_credentials and webhook_signing_secrets
  are surfaced as has_crm_credentials / webhook_integrations and are not
  writable here (extra="forbid" on the update model rejects attempts) —
  the onboarding script owns them.
- Money fields are floats here (display-only); canonical Decimal handling
  stays in the pipeline/jobs.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.lead import LeadOutcome, OutcomeSource
from app.models.qualification import QualificationSchema

# ===========================================================================
# Auth
# ===========================================================================


class AdminLoginIn(BaseModel):
    email: str
    password: str


class AdminMeOut(BaseModel):
    id: UUID
    email: str
    name: str
    role: str
    last_login_at: datetime | None = None


class AdminLoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    admin: AdminMeOut


# ===========================================================================
# Clients + config
# ===========================================================================


class ClientListItem(BaseModel):
    id: UUID
    slug: str
    business_name: str
    status: str  # active | paused | churned | trial
    tier: str  # founding_partner | standard | pro | full_stack
    timezone: str
    crm_provider: str | None = None
    twilio_number: str | None = None
    launched_at: datetime | None = None
    created_at: datetime
    leads_30d: int = 0


class ClassificationConfig(BaseModel):
    """Lead-lifecycle v2 filtering toggles (migration 013 defaults)."""

    crm_lookup_enabled: bool = True
    spam_filtering_enabled: bool = True
    spam_risk_threshold: Literal["low", "moderate", "high"] = "moderate"
    text_existing_customers: bool = True
    text_vendors: bool = False
    drop_spam_silently: bool = True


class ClientConfigAdminOut(BaseModel):
    # from clients
    client_id: UUID
    slug: str
    business_name: str
    status: str
    tier: str
    timezone: str
    # from client_configs
    business_hours: dict[str, Any]
    service_area_zips: list[str]
    twilio_number: str | None
    vip_keywords: list[str]
    vip_value_threshold: float | None
    crm_provider: str | None
    qualification_prompt: str | None
    greeting_template: str | None
    prompt_versions: dict[str, Any]
    ai_interaction_cap_monthly: int
    ai_interactions_used: int
    ai_period_resets_at: datetime
    brand: dict[str, Any]
    notification_emails: list[str]
    owner_alert_emails: list[str]
    owner_alert_phones: list[str]
    feature_flags: dict[str, Any]
    classification_config: ClassificationConfig
    existing_customer_alert_contact: str | None
    vendor_allowlist: list[str]
    revenue_config: dict[str, Any]
    # Returning-caller + resolver + qualification config (Slices 2–3)
    conversation_config: dict[str, Any]
    contact_config: dict[str, Any]
    qualification_schema: dict[str, Any]
    existing_customer_template: str | None
    vendor_ack_template: str | None
    # secrets, redacted to presence/keys
    has_crm_credentials: bool
    webhook_integrations: list[str]
    updated_at: datetime


class ClientConfigUpdate(BaseModel):
    """Partial update — only provided fields are written (exclude_unset).

    extra="forbid" turns a typo'd field into a 422 instead of a silent
    no-op, and structurally rejects crm_credentials /
    webhook_signing_secrets / status, which are deliberately absent.
    timezone routes to the clients table; everything else to client_configs.
    """

    model_config = ConfigDict(extra="forbid")

    timezone: str | None = None
    business_hours: dict[str, Any] | None = None
    service_area_zips: list[str] | None = None
    twilio_number: str | None = None
    vip_keywords: list[str] | None = None
    vip_value_threshold: float | None = None
    crm_provider: str | None = None
    qualification_prompt: str | None = None
    greeting_template: str | None = None
    prompt_versions: dict[str, Any] | None = None
    ai_interaction_cap_monthly: int | None = None
    brand: dict[str, Any] | None = None
    notification_emails: list[str] | None = None
    owner_alert_emails: list[str] | None = None
    owner_alert_phones: list[str] | None = None
    feature_flags: dict[str, Any] | None = None
    classification_config: ClassificationConfig | None = None
    existing_customer_alert_contact: str | None = None
    vendor_allowlist: list[str] | None = None
    revenue_config: dict[str, Any] | None = None
    conversation_config: dict[str, Any] | None = None
    contact_config: dict[str, Any] | None = None
    # Validated through the full model on write → a bad shape is a 422, not a
    # silently-stored broken schema.
    qualification_schema: QualificationSchema | None = None
    existing_customer_template: str | None = None
    vendor_ack_template: str | None = None


# ===========================================================================
# Leads
# ===========================================================================


class LeadListItem(BaseModel):
    id: UUID
    created_at: datetime
    contact_name: str | None
    phone: str | None
    email: str | None
    classification: str
    qualification_status: str
    qualification_score: int | None
    service_type: str | None
    budget_range: str | None
    timeframe: str | None
    outcome: str
    recovered_value: float | None
    external_id: str | None
    pushed_to_crm_at: datetime | None
    is_test: bool
    message_count: int
    last_message_at: datetime | None


class LeadListOut(BaseModel):
    data: list[LeadListItem]
    count: int


class IntentInfo(BaseModel):
    """Latest post-reply intent classification (from the intent_classified
    event — there is no leads.intent column)."""

    intent: str | None
    proceeded: bool | None
    at: datetime


class LeadDetailOut(BaseModel):
    id: UUID
    client_id: UUID
    external_id: str | None
    source_system: str
    contact_name: str | None
    contact_company: str | None
    phone: str | None
    email: str | None
    address: str | None
    service_type: str | None
    sqft: float | None
    budget_range: str | None
    timeframe: str | None
    classification: str
    qualification_status: str
    qualification_score: int | None
    outcome: str
    recovered_value: float | None
    outcome_source: str | None
    outcome_recorded_at: datetime | None
    notes: str | None
    raw_payload: dict[str, Any]
    is_test: bool
    intent: IntentInfo | None
    message_count: int
    created_at: datetime
    qualified_at: datetime | None
    pushed_to_crm_at: datetime | None
    updated_at: datetime


class ConversationMessage(BaseModel):
    id: UUID
    direction: str  # inbound | outbound
    channel: str  # sms | email | chat | voice
    body: str
    ai_generated: bool
    prompt_version: str | None
    created_at: datetime


class LeadOutcomeIn(BaseModel):
    outcome: LeadOutcome
    recovered_value: Decimal | None = None
    source: OutcomeSource = OutcomeSource.owner_report


class MarkTestIn(BaseModel):
    is_test: bool = True


# ===========================================================================
# Routing / classification activity
# ===========================================================================


class RoutingActivityOut(BaseModel):
    window_days: int
    total_calls: int
    breakdown: dict[str, int]  # potential_lead / existing_customer / known_non_lead / spam / active_conversation
    genuine_lead_rate: float
    spam_rate: float


class RoutingLogItem(BaseModel):
    created_at: datetime
    event_type: str
    routing_decision: str | None
    caller: str | None
    reason: str | None
    lead_id: UUID | None


# ===========================================================================
# Field mappings
# ===========================================================================


class FieldMappingIn(BaseModel):
    integration: str  # 'crm' | 'shopify' | 'website_form' | 'monday' | ...
    canonical_field: str
    external_field: str
    external_field_type: Literal["standard", "custom_field", "custom_property", "column"] = (
        "standard"
    )
    transform: dict[str, Any] | None = None
    notes: str | None = None


class FieldMappingOut(FieldMappingIn):
    updated_at: datetime


# ===========================================================================
# AI usage
# ===========================================================================


class AIUsageOut(BaseModel):
    cap: int
    used: int
    remaining: int
    percent_used: float
    resets_at: datetime


# ===========================================================================
# Contacts (Slice 5)
# ===========================================================================


class ContactListItem(BaseModel):
    id: UUID
    phone: str
    name: str | None
    contact_type: str  # unknown | prospect | customer | vendor | spam | blocked
    contact_type_source: str  # manual | crm | inferred
    call_count: int
    lead_count: int
    last_seen_at: datetime
    summary: str | None


class ContactListOut(BaseModel):
    data: list[ContactListItem]
    count: int


class ContactLeadItem(BaseModel):
    id: UUID
    created_at: datetime
    qualification_status: str
    classification: str
    service_type: str | None
    qualification_score: int | None  # completeness
    value_score: int | None
    outcome: str
    recovered_value: float | None


class ContactDetailOut(BaseModel):
    id: UUID
    client_id: UUID
    phone: str
    name: str | None
    contact_type: str
    contact_type_source: str
    contact_type_at: datetime | None
    contact_type_reason: str | None
    crm_external_id: str | None
    known_facts: dict[str, Any]
    summary: str | None
    last_intent: str | None
    call_count: int
    lead_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    leads: list[ContactLeadItem]


class ContactRetypeIn(BaseModel):
    """The ONLY path that writes contact_type_source='manual' (and the only way
    to set 'blocked'). extra='forbid' so a typo is a 422."""

    model_config = ConfigDict(extra="forbid")

    contact_type: Literal["unknown", "prospect", "customer", "vendor", "spam", "blocked"]
    reason: str | None = None
