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

from app.models.qualification import DEFAULT_QUALIFICATION_SCHEMA_DICT

NOW = datetime.now(UTC)

# Stable identity for the demo session (see routers/demo.py + admin_auth.py).
DEMO_ADMIN_ID: UUID = uuid5(NAMESPACE_URL, "traceflow-demo/admin")
DEMO_EMAIL = "demo@traceflow.app"
DEMO_ADMIN_NAME = "Demo Viewer"


def _cid(slug: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"traceflow-demo/client/{slug}")


def _lid(slug: str, i: int) -> UUID:
    return uuid5(NAMESPACE_URL, f"traceflow-demo/lead/{slug}/{i}")


def _mid(lead_id: UUID, j: int) -> UUID:
    return uuid5(NAMESPACE_URL, f"traceflow-demo/msg/{lead_id}/{j}")


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

# How many "generic" potential leads each client gets, on top of the 3 hero
# leads + 3 non-lead classifications + 1 test row. Varied so the switcher's
# leads/30d and each Leads list look like real, differently-sized businesses.
_GENERIC_LEADS = {
    "summit-stone": 22,
    "coastal-counters": 17,
    "ironwood-floors": 13,
    "desert-tile": 9,
    "granite-peak": 28,
    "lakeside-cabinets": 6,
    "metro-epoxy": 15,
    "vista-resurfacing": 19,
    "heritage-marble": 4,
    "brightline-surfaces": 3,
}

# (qualification_status, score) cycled across generic leads so a client's
# stream is a realistic mix of engaged / in-progress / dead-end callers —
# which in turn drives how long each lead's SMS thread is (see _conversation).
_GENERIC_VARIANTS: list[tuple[str, int]] = [
    ("qualifying", 62),
    ("qualified", 84),
    ("unqualified", 28),
    ("qualifying", 71),
    ("qualified", 90),
    ("unqualified", 35),
    ("qualifying", 58),
]

_NAMES = [
    "Maria Lopez", "Tom Becker", "Priya Nair", "James Whitfield", "Sofia Romano",
    "Derek Chen", "Hannah Brooks", "Luis Alvarez", "Grace Okafor", "Nathan Reed",
    "Emily Carter", "Omar Haddad", "Rachel Kim", "Victor Santos", "Chloe Bennett",
    "Marcus Webb", "Ava Thompson", "Diego Morales", "Lena Petrov", "Samuel Ortiz",
    "Olivia Hayes", "Andre Dubois", "Mei Lin", "Carlos Vega", "Fiona Walsh",
    "Jamal Carter", "Ingrid Solberg", "Pedro Ramos", "Tara Singh", "Eli Foster",
]

_BUDGETS = ["under_5k", "5k-15k", "15k-50k", "50k_plus"]
_TIMEFRAMES = ["this_week", "this_month", "next_quarter", "just_researching"]


# ===========================================================================
# Conversation generation — varied SMS threads from template pools so no two
# leads read the same. Outbound = AI auto-reply/qualifier; inbound = caller.
# ===========================================================================

_INBOUND_OPENERS = [
    "Hi! We're looking to get our {svc} redone — about {sqft} sq ft. Any idea on cost?",
    "Hey, yes we just called about {svc}. Trying to get a ballpark before we commit.",
    "Thanks for getting back to me! We need {svc} done as part of a remodel.",
    "Hi — do you handle {svc}? We'd want it {timeframe}.",
    "Got your text. We're redoing the kitchen and need new {svc}. Can someone come measure?",
    "Yes! Looking for {svc} in the master bath. How soon could you start?",
    "Hi there, hoping for a quote on {svc}, roughly {sqft} sq ft.",
    "Appreciate the quick reply — we've been meaning to tackle this {svc} project for a while.",
]

_OUTBOUND_QUALIFIERS = [
    "Happy to help! Roughly what's your budget, and when are you hoping to start?",
    "Great — do you know the approximate square footage, and which material you're leaning toward?",
    "We can definitely take care of that. What's your timeline, and a rough budget range?",
    "Awesome. Are you replacing existing {svc} or is this a new install? Any budget in mind?",
    "Glad to help! What part of town are you in, and when would you like this done by?",
    "For sure — what's your budget looking like, and is there a deadline you're working toward?",
]

_INBOUND_DETAILS = [
    "Probably {budget}. We'd like it wrapped up {timeframe}.",
    "Budget's {budget}, and timeline is {timeframe} ideally.",
    "Maybe {budget}? Not in a huge rush — {timeframe}.",
    "We're somewhat flexible, {budget} ish. Hoping to start {timeframe}.",
    "Looking at {budget}. Timeline-wise, {timeframe}.",
    "Around {budget} if the quality's there. We'd want it {timeframe}.",
]

_OUTBOUND_BOOKINGS = [
    "Perfect — I can have an estimator reach out today to set up a free measure. What time works?",
    "That works! Want me to get you on the schedule for an on-site quote this week?",
    "Got it — I'll have the {business} team call to lock in a visit. Mornings or afternoons?",
    "Sounds great. I'll pass this to our project lead and we'll get a detailed quote over to you.",
    "Awesome — we can usually get someone out within a few days. What's the best time to reach you?",
]

_INBOUND_CONFIRMS = [
    "Afternoons are best, thanks!",
    "Mornings work great — talk soon.",
    "Sounds good, looking forward to it!",
    "Perfect, thank you!",
    "Great, I'll keep an eye out for the call.",
]

_INBOUND_EXISTING = [
    "Hi, we're already customers — just had a question about our recent install.",
    "Oh hey, we had {svc} done by you last year. Wanted to ask about a touch-up.",
    "We're existing clients — calling about a warranty question, not a new job.",
]

_OUTBOUND_EXISTING = [
    "Of course! Let me get someone from the team to follow up with you directly.",
    "Happy to help — I'll have your account manager reach out today.",
    "Thanks for being a customer! Passing this along to our service team now.",
]


def _pick(pool: list[str], seed: int, offset: int) -> str:
    return pool[(seed + offset) % len(pool)]


def _h_budget(b: str | None) -> str:
    return {
        "under_5k": "under $5k", "5k-15k": "around $5–15k",
        "15k-50k": "$15–50k", "50k_plus": "$50k or so",
    }.get(b or "", "flexible")


def _h_time(t: str | None) -> str:
    return {
        "this_week": "this week", "this_month": "this month",
        "next_quarter": "in the next couple months", "just_researching": "just researching for now",
    }.get(t or "", "soon")


def _h_svc(s: str | None) -> str:
    return (s or "the work").replace("_", " ")


def _greeting(name: str) -> str:
    return f"Hi, this is {name} — sorry we missed your call! What can we help you with today?"


def _msg(lead: dict[str, Any], j: int, direction: str, body: str,
         t0: datetime, minutes: int, prompt_version: str | None = None) -> dict[str, Any]:
    return {
        "id": _mid(lead["id"], j),
        "direction": direction,
        "channel": "sms",
        "body": body,
        "ai_generated": direction == "outbound",
        "prompt_version": prompt_version,
        "created_at": t0 + timedelta(minutes=minutes),
    }


def _conversation(name: str, lead: dict[str, Any], seed: int) -> list[dict[str, Any]]:
    """Build a varied SMS thread for one lead. Length tracks how far the caller
    got: spam/vendor/test → none; unqualified → just the auto-reply (ghosted);
    qualifying → a back-and-forth; qualified/high_value → through to booking."""
    cls = lead["classification"]
    if cls in ("spam", "known_non_lead") or lead["is_test"]:
        return []  # spam dropped, vendor greeting suppressed, test rows quiet

    t0 = lead["created_at"]
    msgs = [_msg(lead, 0, "outbound", _greeting(name), t0, 1, "greeting-v2")]

    if cls == "existing_customer":
        svc = _h_svc(lead["service_type"])
        msgs.append(_msg(lead, 1, "inbound", _pick(_INBOUND_EXISTING, seed, 0).format(svc=svc), t0, 6))
        msgs.append(_msg(lead, 2, "outbound", _pick(_OUTBOUND_EXISTING, seed, 0), t0, 8, "qualifier-v3"))
        return msgs

    if lead["qualification_status"] == "unqualified":
        return msgs  # auto-reply sent, caller never wrote back

    svc = _h_svc(lead["service_type"])
    sqft = int(lead["sqft"] or 0)
    budget = _h_budget(lead["budget_range"])
    tf = _h_time(lead["timeframe"])
    msgs.append(_msg(lead, 1, "inbound",
                     _pick(_INBOUND_OPENERS, seed, 1).format(svc=svc, sqft=sqft, timeframe=tf), t0, 8))
    msgs.append(_msg(lead, 2, "outbound",
                     _pick(_OUTBOUND_QUALIFIERS, seed, 2).format(svc=svc), t0, 9, "qualifier-v3"))
    msgs.append(_msg(lead, 3, "inbound",
                     _pick(_INBOUND_DETAILS, seed, 1).format(budget=budget, timeframe=tf), t0, 15))
    if lead["qualification_status"] in ("qualified", "high_value"):
        msgs.append(_msg(lead, 4, "outbound",
                         _pick(_OUTBOUND_BOOKINGS, seed, 3).format(business=name), t0, 18, "qualifier-v3"))
        msgs.append(_msg(lead, 5, "inbound", _pick(_INBOUND_CONFIRMS, seed, 2), t0, 27))
    return msgs


# ===========================================================================
# Rows
# ===========================================================================


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
        # Non-lead route acks (migration 021) — None → sensible defaults.
        "existing_customer_template": None,
        "vendor_ack_template": None,
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
        # Returning-caller windows (migration 019).
        "conversation_config": {
            "resume_window_hours": 336,
            "reopen_window_days": 90,
            "recognize_returning_callers": True,
            "reuse_lead_on_resume": True,
        },
        # Contact source-of-truth resolver config (migration 019, Slice 2.5).
        # 'auto' resolves to 'crm' for clients with a CRM, 'traceflow' otherwise.
        "contact_config": {
            "source_of_truth": "auto",
            "crm_write_back_contact_type": False,
            "contact_type_cache_days": 30,
        },
        # The seeded default schema (mirrors the prod column default) so the
        # admin qualification editor has real fields to render.
        "qualification_schema": DEFAULT_QUALIFICATION_SCHEMA_DICT,
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
        # contact_id links to the durable contact (migration 018). The demo's
        # contacts panel lands in Slice 5; until then leads carry a null link.
        "contact_id": None,
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
        # qualification_score is now the completeness score (migration 020).
        "qualification_score": 55 + (i * 7) % 40,
        "value_score": 40 + (i * 11) % 55,
        "qualification_data": {},
        "outcome": "open",
        "recovered_value": None,
        "outcome_source": None,
        "outcome_recorded_at": None,
        "notes": "",
        "raw_payload": {"CallSid": f"CA{_ROSTER_INDEX[slug]:02d}{i:030d}"},
        "is_test": False,
        # Conversation activity (migration 019).
        "last_inbound_at": None,
        "last_outbound_at": None,
        "turn_count": 0,
        "created_at": NOW - timedelta(hours=6 * i + 2),
        "qualified_at": None,
        "pushed_to_crm_at": None,
        "updated_at": NOW - timedelta(hours=6 * i),
    }
    base.update(overrides)
    return base


def _build_leads(slug: str, crm: str | None) -> list[dict[str, Any]]:
    """A varied lead stream per client: 3 hero leads, a per-client-sized spread
    of generic potential leads (mixed statuses), then one of each non-lead
    classification + a test row. Order is kept stable so _build_routing can
    reference heroes (leads[0:2]) and the spam row (leads[-2])."""
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
    # Per-client spread of generic potential leads, statuses cycled for variety.
    n = _GENERIC_LEADS[slug]
    for k in range(n):
        status, score = _GENERIC_VARIANTS[k % len(_GENERIC_VARIANTS)]
        leads.append(_lead(slug, 4 + k, qualification_status=status, qualification_score=score))

    # Non-lead classifications + a test row, indexed AFTER the generics so their
    # uuid5 ids never collide with a generic lead's.
    base = 4 + n
    leads.append(_lead(
        slug, base, classification="existing_customer", qualification_status="non_lead_contact",
    ))
    leads.append(_lead(
        slug, base + 1, classification="known_non_lead",
        qualification_status="non_lead_contact", contact_name=None,
        notes="Vendor — on allowlist.",
    ))
    leads.append(_lead(
        slug, base + 2, classification="spam", qualification_status="spam",
        contact_name=None, qualification_score=None,
        raw_payload={"CallSid": f"CA{_ROSTER_INDEX[slug]:02d}-spam"},
    ))
    leads.append(_lead(slug, base + 3, contact_name="Test Lead", is_test=True))
    return leads


def _build_messages(
    slug: str, name: str, leads: list[dict[str, Any]]
) -> dict[UUID, list[dict[str, Any]]]:
    """A varied SMS thread per eligible lead (see _conversation)."""
    out: dict[UUID, list[dict[str, Any]]] = {}
    for pos, lead in enumerate(leads):
        thread = _conversation(name, lead, seed=pos + _ROSTER_INDEX[slug])
        if thread:
            out[lead["id"]] = thread
    return out


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


def _coid(slug: str, phone: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"traceflow-demo/contact/{slug}/{phone}")


_CONTACT_TYPE_FROM_CLASS = {
    "existing_customer": "customer",
    "known_non_lead": "vendor",
    "spam": "spam",
    "potential_lead": "prospect",
}


def _build_contacts(slug: str, leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One contact per distinct phone (mirrors the migration 018 backfill), with
    each lead linked back via contact_id. Type derived from the lead's
    classification so the panel's New/Existing/Vendors/Filtered groups populate."""
    by_phone: dict[str, list[dict[str, Any]]] = {}
    for lead in leads:
        by_phone.setdefault(lead["phone"], []).append(lead)

    contacts: list[dict[str, Any]] = []
    for phone, group in by_phone.items():
        ordered = sorted(group, key=lambda ld: ld["created_at"])
        latest = ordered[-1]
        cid = _coid(slug, phone)
        ctype = _CONTACT_TYPE_FROM_CLASS.get(latest["classification"], "prospect")
        name = next((ld["contact_name"] for ld in reversed(ordered) if ld["contact_name"]), None)
        for lead in group:
            lead["contact_id"] = cid
        contacts.append({
            "id": cid,
            "client_id": _cid(slug),
            "phone": phone,
            "name": name,
            "contact_type": ctype,
            "contact_type_source": "crm" if ctype in ("customer", "vendor") else "inferred",
            "contact_type_at": latest["created_at"],
            "contact_type_reason": None,
            "crm_external_id": None,
            "known_facts": {"contact_name": name} if name else {},
            "summary": (
                f"Previously inquired about {latest['service_type'].replace('_', ' ')}."
                if ctype == "prospect" and latest.get("service_type") else None
            ),
            "last_intent": None,
            "call_count": len(group),
            "lead_count": len(group),
            "first_seen_at": ordered[0]["created_at"],
            "last_seen_at": latest["created_at"],
            "updated_at": latest["updated_at"],
        })
    contacts.sort(key=lambda c: c["last_seen_at"], reverse=True)
    return contacts


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
        contacts = _build_contacts(slug, leads)  # links each lead's contact_id
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
            "contacts": contacts,
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
