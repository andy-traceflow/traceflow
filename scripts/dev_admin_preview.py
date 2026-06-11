"""Run the admin surface against canned in-memory data — UI dev/preview only.

    python scripts/dev_admin_preview.py        # serves http://localhost:8000/admin
    login: dev@traceflow.app / preview

The REAL app runs (real routers, real auth, real JWTs); only the DB layer is
replaced with a tiny SQL-shape router over canned rows, the same technique
tests/test_admin.py uses. Config edits, mark-test, and mapping upserts mutate
the in-memory rows so round-trips look real; everything resets on restart.
NEVER deploy this — it exists so the SPA can be developed without a database.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

os.environ.setdefault("ADMIN_JWT_SECRET", "dev-preview-secret-0123456789abcdef")

NOW = datetime.now(UTC)
CLIENT_ID = uuid4()
ADMIN_ID = uuid4()

ADMIN_ROW: dict[str, Any] = {
    "id": ADMIN_ID,
    "email": "dev@traceflow.app",
    "name": "Dev Preview",
    "role": "owner",
    "is_active": True,
    "password_hash": None,  # filled in main() (needs app import)
    "last_login_at": None,
}

CONFIG_ROW: dict[str, Any] = {
    "client_id": CLIENT_ID,
    "slug": "acme",
    "business_name": "Acme Surfaces",
    "status": "active",
    "tier": "founding_partner",
    "timezone": "America/Los_Angeles",
    "business_hours": {"mon": {"open": "08:00", "close": "17:00"}},
    "service_area_zips": ["89101", "89102"],
    "twilio_number": "+17025550100",
    "vip_keywords": ["urgent", "asap"],
    "vip_value_threshold": 10000.0,
    "crm_provider": "hubspot",
    "crm_credentials": {"access_token": "redacted"},
    "webhook_signing_secrets": {"twilio": "redacted"},
    "qualification_prompt": None,
    "greeting_template": "Hi, this is Acme Surfaces — sorry we missed your call! "
    "What can we help you with?",
    "prompt_versions": {},
    "ai_interaction_cap_monthly": 1000,
    "ai_interactions_used": 387,
    "ai_period_resets_at": NOW + timedelta(days=12),
    "brand": {"business_name": "Acme Surfaces"},
    "notification_emails": ["ops@acme.test"],
    "owner_alert_emails": ["owner@acme.test"],
    "owner_alert_phones": ["+17025550199"],
    "feature_flags": {},
    "classification_config": {"spam_risk_threshold": "moderate"},
    "existing_customer_alert_contact": "owner@acme.test",
    "vendor_allowlist": ["+17025550150"],
    "revenue_config": {"mode": "crm", "monthly_fee": 397},
    "updated_at": NOW,
}


def _lead(i: int, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": uuid4(),
        "client_id": CLIENT_ID,
        "external_id": None,
        "source_system": "twilio_missed_call",
        "contact_name": f"Caller {i}",
        "contact_company": None,
        "phone": f"+1702555{1000 + i}",
        "email": None,
        "address": None,
        "service_type": "countertop",
        "sqft": 45.0,
        "budget_range": "5k-15k",
        "timeframe": "this_month",
        "classification": "potential_lead",
        "qualification_status": "qualifying",
        "qualification_score": 60,
        "outcome": "open",
        "recovered_value": None,
        "outcome_source": None,
        "outcome_recorded_at": None,
        "notes": "",
        "raw_payload": {"CallSid": f"CA{i:032d}"},
        "is_test": False,
        "created_at": NOW - timedelta(hours=4 * i),
        "qualified_at": None,
        "pushed_to_crm_at": None,
        "updated_at": NOW,
    }
    base.update(overrides)
    return base


LEADS = [
    _lead(
        1,
        contact_name="Maria Lopez",
        qualification_status="qualified",
        qualification_score=85,
        external_id="hs-301",
        pushed_to_crm_at=NOW - timedelta(hours=2),
    ),
    _lead(
        2,
        contact_name="Tom Becker",
        qualification_status="high_value",
        budget_range="15k-50k",
        outcome="won",
        recovered_value=18500,
        outcome_source="crm",
        external_id="hs-288",
    ),
    _lead(3, contact_name=None, qualification_status="unqualified"),
    _lead(4, classification="spam", qualification_status="spam", contact_name=None),
    _lead(5, contact_name="Test Row", is_test=True),
]

MESSAGES = [
    {
        "id": uuid4(),
        "direction": "outbound",
        "channel": "sms",
        "body": "Hi, this is Acme Surfaces — sorry we missed your call! "
        "What can we help you with?",
        "ai_generated": True,
        "prompt_version": "greeting-v2",
        "created_at": NOW - timedelta(hours=4),
    },
    {
        "id": uuid4(),
        "direction": "inbound",
        "channel": "sms",
        "body": "Hey — looking to redo our kitchen countertops, maybe 45 sqft. "
        "What would that run?",
        "ai_generated": False,
        "prompt_version": None,
        "created_at": NOW - timedelta(hours=3, minutes=50),
    },
    {
        "id": uuid4(),
        "direction": "outbound",
        "channel": "sms",
        "body": "Great project! Are you thinking quartz, granite, or something else? "
        "And what's your rough budget range?",
        "ai_generated": True,
        "prompt_version": "qualifier-v3",
        "created_at": NOW - timedelta(hours=3, minutes=49),
    },
]

ROUTING_BUCKETS = [
    {"bucket": "potential_lead", "n": 14},
    {"bucket": "existing_customer", "n": 4},
    {"bucket": "known_non_lead", "n": 2},
    {"bucket": "spam", "n": 3},
    {"bucket": "active_conversation", "n": 2},
]

ROUTING_LOG = [
    {
        "created_at": NOW - timedelta(hours=1),
        "event_type": "twilio_missed_call_received",
        "payload": {"route": "potential_lead", "classification": "potential_lead",
                    "reason": "unknown caller"},
        "lead_id": LEADS[0]["id"],
        "phone": LEADS[0]["phone"],
    },
    {
        "created_at": NOW - timedelta(hours=2),
        "event_type": "twilio_missed_call_received",
        "payload": {"route": "spam", "classification": "spam",
                    "reason": "high spam risk score"},
        "lead_id": LEADS[3]["id"],
        "phone": LEADS[3]["phone"],
    },
    {
        "created_at": NOW - timedelta(hours=3),
        "event_type": "missed_call_during_active_conversation",
        "payload": {"call_sid": "CA9", "from": "+17025551001"},
        "lead_id": None,
        "phone": None,
    },
    {
        "created_at": NOW - timedelta(hours=5),
        "event_type": "greeting_suppressed",
        "payload": {"route": "known_non_lead", "classification": "known_non_lead",
                    "reason": "vendor allowlist match"},
        "lead_id": None,
        "phone": "+17025550150",
    },
]

MAPPINGS = [
    {
        "integration": "crm",
        "canonical_field": "service_type",
        "external_field": "service_interest",
        "external_field_type": "custom_property",
        "transform": None,
        "notes": "HubSpot custom property",
        "updated_at": NOW,
    },
    {
        "integration": "crm",
        "canonical_field": "sqft",
        "external_field": "project_sqft",
        "external_field_type": "custom_property",
        "transform": None,
        "notes": None,
        "updated_at": NOW,
    },
]


def _find_lead(lead_id: Any) -> dict[str, Any] | None:
    return next((lead for lead in LEADS if lead["id"] == lead_id), None)


def _apply_config_update(sql: str, args: tuple[Any, ...]) -> None:
    """Parse 'UPDATE client_configs SET a = $2, b = $3 WHERE ...' and apply
    to CONFIG_ROW so the preview round-trips edits."""
    cols = [part.split("=")[0].strip() for part in sql.split("SET", 1)[1].split("WHERE", 1)[0].split(",")]
    for col, value in zip(cols, args[1:], strict=False):
        CONFIG_ROW[col] = value


def _norm(sql: str) -> str:
    """Collapse whitespace so substring routing survives multiline SQL."""
    return " ".join(sql.split())


class FakeConn:
    """Routes SQL by shape to the canned rows. Just enough for /api/admin."""

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        sql = _norm(sql)
        if "FROM admin_users WHERE email" in sql:
            return dict(ADMIN_ROW) if args[0] == ADMIN_ROW["email"] else None
        if "FROM admin_users" in sql:
            return dict(ADMIN_ROW) if args[0] == ADMIN_ID else None
        if "JOIN client_configs cc ON" in sql:
            return dict(CONFIG_ROW) if args[0] == CLIENT_ID else None
        if "SELECT * FROM leads" in sql:
            lead = _find_lead(args[0])
            return dict(lead) if lead and args[1] == CLIENT_ID else None
        if "event_type = 'intent_classified'" in sql:
            return {
                "payload": {"intent": "sales", "proceeded": True},
                "created_at": NOW - timedelta(hours=3, minutes=50),
            }
        if "SELECT * FROM client_configs" in sql:
            return dict(CONFIG_ROW)
        if "ai_interaction_cap_monthly" in sql and "FROM client_configs" in sql:
            return dict(CONFIG_ROW) if args[0] == CLIENT_ID else None
        if "INSERT INTO client_field_mappings" in sql:
            row = {
                "integration": args[1],
                "canonical_field": args[2],
                "external_field": args[3],
                "external_field_type": args[4],
                "transform": args[5],
                "notes": args[6],
                "updated_at": datetime.now(UTC),
            }
            MAPPINGS[:] = [
                m
                for m in MAPPINGS
                if not (m["integration"] == args[1] and m["canonical_field"] == args[2])
            ] + [row]
            return dict(row)
        if "FROM client_field_mappings" in sql and "WHERE client_id = $1 AND integration" in sql:
            return next(
                (
                    dict(m)
                    for m in MAPPINGS
                    if m["integration"] == args[1] and m["canonical_field"] == args[2]
                ),
                None,
            )
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        sql = _norm(sql)
        if "FROM clients c" in sql and "LEFT JOIN client_configs" in sql:
            return [
                {
                    "id": CLIENT_ID,
                    "slug": "acme",
                    "business_name": "Acme Surfaces",
                    "status": "active",
                    "tier": "founding_partner",
                    "timezone": "America/Los_Angeles",
                    "launched_at": NOW - timedelta(days=20),
                    "created_at": NOW - timedelta(days=30),
                    "crm_provider": "hubspot",
                    "twilio_number": "+17025550100",
                    "leads_30d": 14,
                }
            ]
        if "FROM leads l" in sql:
            classification, include_test = args[1], args[2]
            rows = [
                {
                    **{k: lead[k] for k in (
                        "id", "created_at", "contact_name", "phone", "email",
                        "classification", "qualification_status", "qualification_score",
                        "service_type", "budget_range", "timeframe", "outcome",
                        "recovered_value", "external_id", "pushed_to_crm_at", "is_test",
                    )},
                    "message_count": len(MESSAGES) if lead is LEADS[0] else 0,
                    "last_message_at": MESSAGES[-1]["created_at"] if lead is LEADS[0] else None,
                }
                for lead in LEADS
                if (classification == "all" or lead["classification"] == classification)
                and (include_test or not lead["is_test"])
            ]
            return rows
        if "FROM messages" in sql:
            lead = _find_lead(args[1])
            return [dict(m) for m in MESSAGES] if lead is LEADS[0] else []
        if "GROUP BY 1" in sql:
            return [dict(b) for b in ROUTING_BUCKETS]
        if "FROM events e" in sql:
            return [dict(r) for r in ROUTING_LOG]
        if "FROM client_field_mappings" in sql:
            integration = args[1] if len(args) > 1 else None
            return [
                dict(m) for m in MAPPINGS if integration is None or m["integration"] == integration
            ]
        return []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        sql = _norm(sql)
        if "SELECT 1 FROM clients" in sql:
            return 1 if args[0] == CLIENT_ID else None
        if "SELECT 1 FROM leads" in sql:
            lead = _find_lead(args[0])
            return 1 if lead and args[1] == CLIENT_ID else None
        if "count(*)" in sql and "FROM leads" in sql:
            classification, include_test = args[1], args[2]
            return sum(
                1
                for lead in LEADS
                if (classification == "all" or lead["classification"] == classification)
                and (include_test or not lead["is_test"])
            )
        if "count(*)" in sql and "FROM messages" in sql:
            lead = _find_lead(args[1])
            return len(MESSAGES) if lead is LEADS[0] else 0
        return None

    async def execute(self, sql: str, *args: Any) -> None:
        sql = _norm(sql)
        if sql.startswith("UPDATE client_configs SET ai_interactions_used = 0"):
            CONFIG_ROW["ai_interactions_used"] = 0
        elif sql.strip().startswith("UPDATE client_configs SET"):
            _apply_config_update(sql, args)
        elif "UPDATE clients SET timezone" in sql:
            CONFIG_ROW["timezone"] = args[1]
        elif "UPDATE leads SET is_test" in sql:
            lead = _find_lead(args[1])
            if lead:
                lead["is_test"] = args[0]
        elif "DELETE FROM client_field_mappings" in sql:
            MAPPINGS[:] = [
                m
                for m in MAPPINGS
                if not (m["integration"] == args[1] and m["canonical_field"] == args[2])
            ]
        elif "UPDATE leads" in sql and "outcome" in sql:
            lead = _find_lead(args[3])
            if lead:
                lead["outcome"], lead["recovered_value"], lead["outcome_source"] = (
                    args[0],
                    args[1],
                    args[2],
                )


@asynccontextmanager
async def fake_service_connection():
    yield FakeConn()


def main() -> None:
    import uvicorn

    import app.routers.admin.activity as activity_mod
    import app.routers.admin.auth as auth_mod
    import app.routers.admin.clients as clients_mod
    import app.routers.admin.leads as leads_mod
    import app.routers.admin.mappings as mappings_mod
    import app.services.admin_auth as admin_auth_mod
    import app.services.audit as audit_mod
    from app.main import app
    from app.services.admin_auth import hash_password

    ADMIN_ROW["password_hash"] = hash_password("preview")

    for mod in (auth_mod, clients_mod, leads_mod, activity_mod, mappings_mod,
                admin_auth_mod, audit_mod):
        mod.get_service_connection = fake_service_connection  # type: ignore[attr-defined]

    print("Admin preview: http://localhost:8000/admin  (dev@traceflow.app / preview)")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
