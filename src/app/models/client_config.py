"""Per-tenant runtime configuration."""

from __future__ import annotations

from datetime import datetime
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

    def feature(self, flag: str, default: bool = False) -> bool:
        value = self.feature_flags.get(flag, default)
        return bool(value)

    def webhook_secret(self, integration: str) -> str | None:
        return self.webhook_signing_secrets.get(integration)
