"""Normalize an onboarding submission into a ProvisionSpec-shaped dict.

The 77-field onboarding form splits across TraceFlow's config model like this:
  - identity/tier/timezone       → clients columns
  - voice/branding               → client_configs.brand JSONB
  - address, DBA, website, GBP,
    owner/PoC people, tech stack,
    logistics                    → client_configs.business_profile JSONB (mig 023)
  - alerts, VIP, module toggles  → typed columns / feature_flags

This mapper operates on a CANONICAL payload — the flat key set the native form
will POST, and the shape a future Jotform field-id translation layer will emit.
The Jotform → canonical translation is intentionally NOT hardcoded here: it
depends on the live form's question ids, which belong next to the intake
webhook when that's wired. Keeping this mapper canonical-only means it's
form-agnostic and directly unit-testable.

Credentials are never mapped — the form doesn't collect them and provision
refuses them.
"""

from __future__ import annotations

from typing import Any

from app.services.provisioning import slugify

# Canonical keys that route into the brand JSONB block.
_BRAND_KEYS = ("category", "tone_of_voice", "primary_color", "logo_url", "service_types")

# Canonical keys that route into the business_profile JSONB block (flat scalars).
_PROFILE_SCALARS = (
    "dba",
    "address",
    "website_url",
    "google_business_profile_url",
    "google_business_profile_owner_email",
)
# Nested person/struct blocks copied through verbatim when present.
_PROFILE_BLOCKS = ("owner", "day_to_day", "tech", "logistics")


def _clean(value: Any) -> Any:
    """Drop empty strings so absent fields don't overwrite column defaults."""
    return None if isinstance(value, str) and not value.strip() else value


def map_submission(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a dict suitable for ProvisionSpec(**result).

    Best-effort: unknown keys are ignored, empty strings are dropped, and a
    missing slug is derived from the business name. Validation (required
    fields, crm_provider domain, slug pattern) happens when the caller
    constructs ProvisionSpec — this function does not raise on thin input.
    """
    business_name = _clean(payload.get("business_name")) or ""

    brand: dict[str, Any] = {"business_name": business_name}
    for key in _BRAND_KEYS:
        value = _clean(payload.get(key))
        if value not in (None, [], {}):
            brand[key] = value

    profile: dict[str, Any] = {}
    if _clean(payload.get("legal_name")):
        profile["legal_name"] = _clean(payload.get("legal_name"))
    for key in _PROFILE_SCALARS:
        value = _clean(payload.get(key))
        if value is not None:
            profile[key] = value
    for block in _PROFILE_BLOCKS:
        value = payload.get(block)
        if isinstance(value, dict) and any(_clean(v) for v in value.values()):
            profile[block] = {k: v for k, v in value.items() if _clean(v) is not None}

    crm = _clean(payload.get("crm_provider"))
    if isinstance(crm, str):
        crm = crm.strip().lower()
        if crm not in ("ghl", "hubspot", "monday", "generic"):
            crm = None  # unknown CRM → escape-hatch handled via generic webhook later

    spec: dict[str, Any] = {
        "slug": _clean(payload.get("slug")) or slugify(business_name),
        "business_name": business_name,
        "tier": _clean(payload.get("tier")) or "standard",
        "timezone": _clean(payload.get("timezone")) or "America/Los_Angeles",
        "business_hours": payload.get("business_hours") or {},
        "service_area_zips": payload.get("service_area_zips") or [],
        "vip_keywords": payload.get("vip_keywords") or [],
        "vip_value_threshold": payload.get("vip_value_threshold"),
        "crm_provider": crm,
        "greeting_template": _clean(payload.get("greeting_template")),
        "brand": brand,
        "notification_emails": payload.get("notification_emails") or [],
        "owner_alert_emails": payload.get("owner_alert_emails") or [],
        "owner_alert_phones": payload.get("owner_alert_phones") or [],
        "feature_flags": payload.get("feature_flags") or payload.get("modules") or {},
        "business_profile": profile,
    }
    return spec
