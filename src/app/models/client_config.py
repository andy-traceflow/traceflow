"""Per-tenant runtime configuration."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ClientConfig(BaseModel):
    client_id: UUID

    # Operational
    business_hours: dict[str, dict[str, str]] = Field(default_factory=dict)
    service_area_zips: list[str] = Field(default_factory=list)
    twilio_number: str | None = None
    vip_keywords: list[str] = Field(default_factory=list)
    vip_value_threshold: float | None = None

    # Integration routing
    crm_provider: str | None = None
    crm_credentials: dict[str, Any] = Field(default_factory=dict)
    webhook_signing_secrets: dict[str, str] = Field(default_factory=dict)

    # AI behavior
    qualification_prompt: str | None = None
    greeting_template: str | None = None
    # Non-lead route acks (migration 021) — distinct messages, both nullable.
    existing_customer_template: str | None = None
    vendor_ack_template: str | None = None
    # Qualification closings (migration 025) — rendered by
    # prompts.greeting.render_handoff / render_decline when code terminates the
    # conversation. Nullable; NULL falls back to the default there.
    handoff_template: str | None = None
    decline_template: str | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    ai_interaction_cap_monthly: int = 1000
    ai_interactions_used: int = 0
    ai_period_resets_at: datetime

    # Branding
    brand: dict[str, Any] = Field(default_factory=dict)

    # Notification delivery
    notification_emails: list[str] = Field(default_factory=list)
    owner_alert_emails: list[str] = Field(default_factory=list)
    owner_alert_phones: list[str] = Field(default_factory=list)

    # Feature flags
    feature_flags: dict[str, Any] = Field(default_factory=dict)

    # Caller classification (lead lifecycle Section 3)
    classification_config: dict[str, Any] = Field(default_factory=dict)
    existing_customer_alert_contact: str | None = None
    vendor_allowlist: list[str] = Field(default_factory=list)

    # Revenue tracking (recovered-revenue attribution)
    revenue_config: dict[str, Any] = Field(default_factory=dict)

    # Returning-caller conversation windows (migration 019)
    conversation_config: dict[str, Any] = Field(default_factory=dict)

    # Contact source-of-truth resolver config (migration 019, Slice 2.5)
    contact_config: dict[str, Any] = Field(default_factory=dict)

    # Config-driven qualification schema (migration 020). Parsed via
    # services.qualification.get_schema, which falls back to the default when
    # empty/invalid.
    qualification_schema: dict[str, Any] = Field(default_factory=dict)

    # Structured business identity + logistics captured at onboarding
    # (migration 023). Read via the accessors below. Never holds credentials.
    business_profile: dict[str, Any] = Field(default_factory=dict)

    updated_at: datetime

    model_config = {"from_attributes": True}

    # ------------------------------------------------------------------
    # Convenience accessors — keeps callers from spreading dict lookups
    # of the same brand/feature_flags shape across the codebase.
    # ------------------------------------------------------------------
    @property
    def business_name(self) -> str:
        return self.brand.get("business_name", "")

    @property
    def category(self) -> str:
        return self.brand.get("category", "service business")

    @property
    def tone_of_voice(self) -> str:
        return self.brand.get("tone_of_voice", "friendly and professional")

    @property
    def service_types(self) -> list[str]:
        return self.brand.get("service_types", []) or []

    @property
    def default_phone_region(self) -> str:
        """ISO region used to normalize hand-entered phone numbers to E.164.
        Config-driven (brand.phone_region), defaulting to US — our clients are
        US/Canada surface contractors. Never a constant at the call site."""
        return self.brand.get("phone_region", "US")

    def feature(self, flag: str, default: bool = False) -> bool:
        value = self.feature_flags.get(flag, default)
        return bool(value)

    # ------------------------------------------------------------------
    # Business profile (migration 023) — onboarding identity + logistics.
    # Accessors return None/{} on absence so callers never branch on shape.
    # ------------------------------------------------------------------
    @property
    def website_url(self) -> str | None:
        return self.business_profile.get("website_url") or None

    @property
    def business_address(self) -> str | None:
        return self.business_profile.get("address") or None

    def business_contact(self, role: str = "owner") -> dict[str, str]:
        """Structured person block for 'owner' or 'day_to_day'; {} when absent."""
        value = self.business_profile.get(role)
        return value if isinstance(value, dict) else {}

    def webhook_secret(self, integration: str) -> str | None:
        return self.webhook_signing_secrets.get(integration)

    # ------------------------------------------------------------------
    # Caller-classification tolerances. Defaults baked in here (matching
    # migration 013) so a row with a partial/empty classification_config
    # still behaves correctly.
    # ------------------------------------------------------------------
    @property
    def crm_lookup_enabled(self) -> bool:
        return bool(self.classification_config.get("crm_lookup_enabled", True))

    @property
    def spam_filtering_enabled(self) -> bool:
        return bool(self.classification_config.get("spam_filtering_enabled", True))

    @property
    def spam_risk_threshold(self) -> str:
        return self.classification_config.get("spam_risk_threshold", "moderate")

    @property
    def text_existing_customers(self) -> bool:
        return bool(self.classification_config.get("text_existing_customers", True))

    @property
    def text_vendors(self) -> bool:
        return bool(self.classification_config.get("text_vendors", False))

    @property
    def drop_spam_silently(self) -> bool:
        return bool(self.classification_config.get("drop_spam_silently", True))

    @property
    def rescore_spam_after_days(self) -> int | None:
        """Days after which a contact typed `spam` is re-scored on a new call.
        Default None = never re-score (a spam inference sticks until manual
        revocation). Returns None when unset or unparseable."""
        raw = self.classification_config.get("rescore_spam_after_days")
        if raw in (None, ""):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Returning-caller conversation windows (migration 019). Defaults match
    # the materialized conversation_config default so a partial/empty object
    # still behaves.
    # ------------------------------------------------------------------
    @property
    def resume_window_hours(self) -> int:
        try:
            return int(self.conversation_config.get("resume_window_hours", 336))
        except (TypeError, ValueError):
            return 336

    @property
    def reopen_window_days(self) -> int:
        try:
            return int(self.conversation_config.get("reopen_window_days", 90))
        except (TypeError, ValueError):
            return 90

    @property
    def recognize_returning_callers(self) -> bool:
        return bool(self.conversation_config.get("recognize_returning_callers", True))

    @property
    def reuse_lead_on_resume(self) -> bool:
        return bool(self.conversation_config.get("reuse_lead_on_resume", True))

    @property
    def terminal_resume_window_minutes(self) -> int:
        """Minutes after a lead reaches a TERMINAL status during which a new
        inbound SMS RESUMES that same lead (flip back to qualifying) instead of
        opening a fresh one. Prevents lead-splitting / re-interrogation. Backed
        by conversation_config; default 120."""
        try:
            return int(self.conversation_config.get("terminal_resume_window_minutes", 120))
        except (TypeError, ValueError):
            return 120

    # ------------------------------------------------------------------
    # Contact source-of-truth resolver config (migration 019, Slice 2.5).
    # ------------------------------------------------------------------
    @property
    def contact_source_of_truth(self) -> str:
        """'auto' | 'crm' | 'traceflow'. 'auto' is resolved to a concrete mode
        at runtime by services.contacts.resolve_contact_type — never branched on
        elsewhere."""
        return self.contact_config.get("source_of_truth", "auto")

    @property
    def crm_write_back_contact_type(self) -> bool:
        """OFF by default. TraceFlow never writes an inferred type back to a
        client's CRM; only manual classifications are eligible, and only when
        this is explicitly enabled."""
        return bool(self.contact_config.get("crm_write_back_contact_type", False))

    @property
    def contact_type_cache_days(self) -> int:
        try:
            return int(self.contact_config.get("contact_type_cache_days", 30))
        except (TypeError, ValueError):
            return 30

    # ------------------------------------------------------------------
    # Revenue-tracking config. Default 'estimated' (the digest's budget-bucket
    # proxy) so a tenant with no revenue_config behaves exactly as before.
    # ------------------------------------------------------------------
    @property
    def revenue_mode(self) -> str:
        """'estimated' | 'owner_report' | 'crm'. Only 'crm' runs CRM readback."""
        return self.revenue_config.get("mode", "estimated")

    @property
    def attribution_window_days(self) -> int:
        """Days after creation a lead's CRM value is tracked, then frozen."""
        try:
            return int(self.revenue_config.get("attribution_window_days", 90))
        except (TypeError, ValueError):
            return 90

    @property
    def monthly_fee(self) -> Decimal | None:
        """Monthly retainer in dollars (revenue_config.monthly_fee) — powers the
        monthly report's ROI multiple (PRD §13: recovered revenue / monthly fee).
        None when unset or unparseable; the ROI line is simply omitted."""
        raw = self.revenue_config.get("monthly_fee")
        if raw in (None, ""):
            return None
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return None
        return value if value > 0 else None
