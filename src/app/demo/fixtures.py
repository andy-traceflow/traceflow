"""Canned multi-tenant demo data for the public read-only demo (DEMO_MODE).

This is the single source of truth for the demo dataset, shared by the deployed
demo (``app.demo.fake_conn``) and the local ``scripts/dev_admin_preview.py``
harness. Every row here is invented — there is NO real client data and no
database connection involved. UUIDs are derived with ``uuid5`` so the dataset
is identical on every process start (stable demo links, reproducible tests).

Shape note: the dicts here mirror the columns each admin SQL statement selects
(see ``routers/admin/*`` and ``demo/fake_conn.py``). asyncpg's JSON codec
returns dicts for JSONB columns, so JSON fields are plain dicts/lists here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

NOW = datetime.now(UTC)

# Stable identity for the demo session (see routers/demo.py + admin_auth.py).
DEMO_ADMIN_ID: UUID = uuid5(NAMESPACE_URL, "traceflow-demo/admin")
DEMO_EMAIL = "demo@traceflow.app"
DEMO_ADMIN_NAME = "Demo Viewer"


def _cid(slug: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"traceflow-demo/client/{slug}")


def _lid(slug: str, i: int) -> UUID:
    return uuid5(NAMESPACE_URL, f"traceflow-demo/lead/{slug}/{i}")


def _mid(slug: str, i: int, j: int) -> UUID:
    return uuid5(NAMESPACE_URL, f"traceflow-demo/msg/{slug}/{i}/{j}")


# ===========================================================================
# Client roster — varied tier / status / CRM so the multi-tenant switcher and
# every panel have something interesting to show.
#   (slug, business_name, tier, status, crm_provider, timezone, monthly_fee)
# ===========================================================================

_ROSTER: list[tuple[str, str, str, str, str | None, str, int]] = [
    ("summit-stone", "Summit Stone & Surface", "founding_partner", "active", "hubspot", "America/Denver", 397),
    ("coastal-counters", "Coastal Counters Co.", "founding_partner", "active", "ghl", "America/Los_Angeles", 397),
    ("ironwood-floors", "Ironwood Flooring", "standard", "active", "monday", "America/Chicago", 597),
    ("desert-tile", "Desert Tile Works", "standard", "active", "hubspot", "America/Phoenix", 597),
    ("granite-peak", "Granite Peak Countertops", "pro", "active", "ghl", "America/Denver", 897),
    ("lakeside-cabinets", "Lakeside Cabinetry", "standard", "active", None, "America/New_York", 597),
    ("metro-epoxy", "Metro Epoxy & Concrete", "standard", "active", "hubspot", "America/Chicago", 597),
    ("vista-resurfacing", "Vista Resurfacing", "pro", "active", "monday", "America/Los_Angeles", 897),
    ("heritage-marble", "Heritage Marble", "standard", "paused", "ghl", "America/New_York", 597),
    ("brightline-surfaces", "Brightline Surfaces", "trial", "trial", None, "America/Phoenix", 0),
]

_ROSTER_INDEX = {slug: i for i, (slug, *_) in enumerate(_ROSTER)}

_SERVICE_TYPES = {
    "summit-stone": ["countertop", "backsplash"],
    "coastal-counters": ["countertop", "vanity"],
    "ironwood-floors": ["hardwood_floor", "luxury_vinyl"],
    "desert-tile": ["tile_floor", "shower_tile"],
    "granite-peak": ["countertop", "fireplace_surround"],
    "lakeside-cabinets": ["cabinets", "countertop"],
    "metro-epoxy": ["epoxy_floor", "concrete_polish"],
    "vista-resurfacing": ["bathtub_resurface", "countertop_resurface"],
    "heritage-marble": ["marble_restoration", "countertop"],
    "brightline-surfaces": ["countertop", "tile_floor"],
}

_NAMES = [
    "Maria Lopez", "Tom Becker", "Priya Nair", "James Whitfield", "Sofia Romano",
    "Derek Chen", "Hannah Brooks", "Luis Alvarez", "Grace Okafor", "Nathan Reed",
    "Emily Carter", "Omar Haddad", "Rachel Kim", "Victor Santos", "Chloe Bennett",
    "Marcus Webb", "Ava Thompson", "Diego Morales", "Lena Petrov", "Samuel Ortiz",
]

_BUDGETS = ["under_5k", "5k-15k", "15k-50k", "50k_plus"]
_TIMEFRAMES = ["this_week", "this_month", "next_quarter", "just_researching"]


def _greeting(name: str) -> str:
    return f"Hi, this is {name} — sorry we missed your call! What can we help you with today?"


def _config_row(slug: str, name: str, tier: str, status: str, crm: str | None,
                tz: str, monthly_fee: int, cap: int, used: int) -> dict[str, Any]:
    """The merged clients + client_configs row that _CONFIG_SELECT returns."""
    return {
        "client_id": _cid(slug),
        "slug": slug,
        "business_name": name,
        "status": status,
        "tier": tier,
        "timezone": tz,
        "business_hours": {
            "mon": {"open": "08:00", "close": "17:00"},
            "tue": {"open": "08:00", "close": "17:00"},
            "wed": {"open": "08:00", "close": "17:00"},
            "thu": {"open": "08:00", "close": "17:00"},
            "fri": {"open": "08:00", "close": "16:00"},
        },
        "service_area_zips": ["89101", "89102", "89103", "89052"],
        "twilio_number": f"+170255{51000 + _ROSTER_INDEX[slug]}",
        "vip_keywords": ["urgent", "asap", "whole house", "commercial"],
        "vip_value_threshold": 25000.0,
        "crm_provider": crm,
        # Secrets are surfaced only as has_crm_credentials / webhook_integrations.
        "crm_credentials": {"access_token": "demo-redacted"} if crm else {},
        "webhook_signing_secrets": ({"twilio": "demo-redacted", "crm": "demo-redacted"}
                                    if crm else {"twilio": "demo-redacted"}),
        "qualification_prompt": None,
        "greeting_template": _greeting(name),
        "prompt_versions": {"greeting": "greeting-v2", "qualifier": "qualifier-v3"},
        "ai_interaction_cap_monthly": cap,
        "ai_interactions_used": used,
        "ai_period_resets_at": NOW + timedelta(days=12),
        "brand": {
            "business_name": name,
            "category": "surface contractor",
            "service_types": _SERVICE_TYPES[slug],
            "tone_of_voice": "friendly and professional",
        },
        "notification_emails": [f"ops@{slug}.example"],
        "owner_alert_emails": [f"owner@{slug}.example"],
        "owner_alert_phones": ["+17025550199"],
        "feature_flags": {},
        "classification_config": {
            "crm_lookup_enabled": crm is not None,
            "spam_filtering_enabled": True,
            "spam_risk_threshold": "moderate",
            "text_existing_customers": True,
            "text_vendors": False,
            "drop_spam_silently": True,
        },
        "existing_customer_alert_contact": f"owner@{slug}.example",
        "vendor_allowlist": ["+17025550150"],
        "revenue_config": {
            "mode": "crm" if crm else "owner_report",
            "monthly_fee": monthly_fee,
            "attribution_window_days": 90,
        },
        "updated_at": NOW - timedelta(days=2),
    }


def _lead(slug: str, i: int, **overrides: Any) -> dict[str, Any]:
    """A full leads row (matches models.lead.Lead / LeadDetailOut)."""
    name = _NAMES[(i + _ROSTER_INDEX[slug]) % len(_NAMES)]
    services = _SERVICE_TYPES[slug]
    base: dict[str, Any] = {
        "id": _lid(slug, i),
        "client_id": _cid(slug),
        "external_id": None,
        "source_system": "twilio_missed_call",
        "contact_name": name,
        "contact_company": None,
        "phone": f"+170255{52000 + i * 7 + _ROSTER_INDEX[slug] * 30}",
        "email": None,
        "address": None,
        "service_type": services[i % len(services)],
        "sqft": float(20 + (i * 13) % 120),
        "budget_range": _BUDGETS[i % len(_BUDGETS)],
        "timeframe": _TIMEFRAMES[i % len(_TIMEFRAMES)],
        "classification": "potential_lead",
        "qualification_status": "qualifying",
        "qualification_score": 55 + (i * 7) % 40,
        "outcome": "open",
        "recovered_value": None,
        "outcome_source": None,
        "outcome_recorded_at": None,
        "notes": "",
        "raw_payload": {"CallSid": f"CA{_ROSTER_INDEX[slug]:02d}{i:030d}"},
        "is_test": False,
        "created_at": NOW - timedelta(hours=6 * i + 2),
        "qualified_at": None,
        "pushed_to_crm_at": None,
        "updated_at": NOW - timedelta(hours=6 * i),
    }
    base.update(overrides)
    return base


def _build_leads(slug: str, crm: str | None) -> list[dict[str, Any]]:
    """A varied lead stream per client: a couple of hero leads, a spread of
    generic potential leads, plus one of each non-lead classification."""
    def ext(i: int) -> str:
        return f"{(crm or 'crm')[:2]}-{_ROSTER_INDEX[slug]}{i:03d}"

    leads: list[dict[str, Any]] = [
        # Hero 1 — won, recovered revenue, pushed to CRM.
        _lead(
            slug, 1,
            qualification_status="qualified",
            qualification_score=88,
            budget_range="15k-50k",
            outcome="won",
            recovered_value=18500,
            outcome_source="crm" if crm else "owner_report",
            outcome_recorded_at=NOW - timedelta(days=3),
            external_id=ext(1) if crm else None,
            pushed_to_crm_at=(NOW - timedelta(days=6)) if crm else None,
            qualified_at=NOW - timedelta(days=6, hours=1),
        ),
        # Hero 2 — high value, in flight.
        _lead(
            slug, 2,
            qualification_status="high_value",
            qualification_score=95,
            budget_range="50k_plus",
            timeframe="this_week",
            external_id=ext(2) if crm else None,
            pushed_to_crm_at=(NOW - timedelta(hours=20)) if crm else None,
            qualified_at=NOW - timedelta(hours=21),
        ),
        # A lost one (booked elsewhere).
        _lead(
            slug, 3,
            qualification_status="qualified",
            qualification_score=72,
            outcome="lost",
            outcome_source="owner_report",
            outcome_recorded_at=NOW - timedelta(days=1),
        ),
    ]
    # A spread of generic potential leads.
    leads.extend(_lead(slug, i) for i in range(4, 12))
    # Non-lead classifications (kept out of recovery metrics).
    leads.append(_lead(
        slug, 12, classification="existing_customer",
        qualification_status="non_lead_contact",
    ))
    leads.append(_lead(
        slug, 13, classification="known_non_lead",
        qualification_status="non_lead_contact", contact_name=None,
        notes="Vendor — on allowlist.",
    ))
    leads.append(_lead(
        slug, 14, classification="spam", qualification_status="spam",
        contact_name=None, qualification_score=None,
        raw_payload={"CallSid": f"CA{_ROSTER_INDEX[slug]:02d}-spam"},
    ))
    # A test row (excluded from metrics + default lead views).
    leads.append(_lead(slug, 15, contact_name="Test Lead", is_test=True))
    return leads


def _build_messages(
    slug: str, name: str, leads: list[dict[str, Any]]
) -> dict[UUID, list[dict[str, Any]]]:
    """A short SMS thread on the first hero lead; nothing on the rest."""
    hero = leads[0]
    return {
        hero["id"]: [
            {
                "id": _mid(slug, 1, 0),
                "direction": "outbound",
                "channel": "sms",
                "body": _greeting(name),
                "ai_generated": True,
                "prompt_version": "greeting-v2",
                "created_at": hero["created_at"] + timedelta(minutes=1),
            },
            {
                "id": _mid(slug, 1, 1),
                "direction": "inbound",
                "channel": "sms",
                "body": "Hi! Looking to redo our kitchen countertops, about 45 sqft. "
                        "What would that run?",
                "ai_generated": False,
                "prompt_version": None,
                "created_at": hero["created_at"] + timedelta(minutes=9),
            },
            {
                "id": _mid(slug, 1, 2),
                "direction": "outbound",
                "channel": "sms",
                "body": "Great project! Are you thinking quartz, granite, or something "
                        "else — and what's your rough budget range?",
                "ai_generated": True,
                "prompt_version": "qualifier-v3",
                "created_at": hero["created_at"] + timedelta(minutes=10),
            },
            {
                "id": _mid(slug, 1, 3),
                "direction": "inbound",
                "channel": "sms",
                "body": "Quartz, probably $15-20k. We'd want it done this month.",
                "ai_generated": False,
                "prompt_version": None,
                "created_at": hero["created_at"] + timedelta(minutes=14),
            },
        ]
    }


def _build_routing(
    slug: str, leads: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Routing-activity buckets + a recent routing-log stream."""
    potential = sum(
        1 for ld in leads if ld["classification"] == "potential_lead" and not ld["is_test"]
    )
    buckets = [
        {"bucket": "potential_lead", "n": potential},
        {"bucket": "existing_customer", "n": 4},
        {"bucket": "known_non_lead", "n": 2},
        {"bucket": "spam", "n": 3},
        {"bucket": "active_conversation", "n": 2},
    ]
    log = [
        {
            "created_at": NOW - timedelta(hours=1),
            "event_type": "twilio_missed_call_received",
            "payload": {"route": "potential_lead", "classification": "potential_lead",
                        "reason": "unknown caller, sales intent"},
            "lead_id": leads[1]["id"],
            "phone": leads[1]["phone"],
        },
        {
            "created_at": NOW - timedelta(hours=2, minutes=30),
            "event_type": "twilio_missed_call_received",
            "payload": {"route": "spam", "classification": "spam",
                        "reason": "high spam-risk score"},
            "lead_id": leads[-2]["id"],
            "phone": leads[-2]["phone"],
        },
        {
            "created_at": NOW - timedelta(hours=4),
            "event_type": "missed_call_during_active_conversation",
            "payload": {"call_sid": "CA-active", "from": leads[0]["phone"]},
            "lead_id": None,
            "phone": None,
        },
        {
            "created_at": NOW - timedelta(hours=7),
            "event_type": "greeting_suppressed",
            "payload": {"route": "known_non_lead", "classification": "known_non_lead",
                        "reason": "vendor allowlist match"},
            "lead_id": None,
            "phone": "+17025550150",
        },
    ]
    return buckets, log


def _build_mappings(slug: str, crm: str | None) -> list[dict[str, Any]]:
    if crm is None:
        return []
    field_type = "custom_property" if crm == "hubspot" else "custom_field"
    return [
        {
            "integration": "crm",
            "canonical_field": "service_type",
            "external_field": "service_interest",
            "external_field_type": field_type,
            "transform": None,
            "notes": f"{crm} custom field",
            "updated_at": NOW - timedelta(days=5),
        },
        {
            "integration": "crm",
            "canonical_field": "sqft",
            "external_field": "project_sqft",
            "external_field_type": field_type,
            "transform": None,
            "notes": None,
            "updated_at": NOW - timedelta(days=5),
        },
        {
            "integration": "crm",
            "canonical_field": "budget_range",
            "external_field": "budget",
            "external_field_type": "standard",
            "transform": {"map": {"5k-15k": "5000-15000"}},
            "notes": "Normalized to CRM's range strings.",
            "updated_at": NOW - timedelta(days=5),
        },
    ]


# ===========================================================================
# Assemble the registry. CLIENTS maps client_id -> the full per-tenant bundle.
# ===========================================================================


def _build() -> dict[UUID, dict[str, Any]]:
    out: dict[UUID, dict[str, Any]] = {}
    for i, (slug, name, tier, status, crm, tz, fee) in enumerate(_ROSTER):
        cap = 1000 if tier in ("founding_partner", "standard") else 2500
        used = 120 + (i * 137) % 800
        config = _config_row(slug, name, tier, status, crm, tz, fee, cap, used)
        leads = _build_leads(slug, crm)
        messages = _build_messages(slug, name, leads)
        buckets, log = _build_routing(slug, leads)
        mappings = _build_mappings(slug, crm)
        cid = _cid(slug)
        leads_30d = sum(
            1 for ld in leads
            if not ld["is_test"] and ld["created_at"] >= NOW - timedelta(days=30)
        )
        out[cid] = {
            "config": config,
            "list_row": {
                "id": cid,
                "slug": slug,
                "business_name": name,
                "status": status,
                "tier": tier,
                "timezone": tz,
                "launched_at": (NOW - timedelta(days=45 - i)) if status != "trial" else None,
                "created_at": NOW - timedelta(days=60 - i),
                "crm_provider": crm,
                "twilio_number": config["twilio_number"],
                "leads_30d": leads_30d,
            },
            "leads": leads,
            "messages": messages,
            "routing_buckets": buckets,
            "routing_log": log,
            "mappings": mappings,
        }
    return out


# Lazy single-build cache so importing this module is cheap and the dataset is
# shared. A demo write — only reachable via dev_admin_preview, never the locked
# public demo — mutates this in place and resets on process restart.
_CLIENTS: dict[UUID, dict[str, Any]] | None = None


def clients() -> dict[UUID, dict[str, Any]]:
    global _CLIENTS
    if _CLIENTS is None:
        _CLIENTS = _build()
    return _CLIENTS
