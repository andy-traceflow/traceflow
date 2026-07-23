"""Admin surface tests — /api/admin/* (ADR-0004).

Auth is the real machinery (tokens minted with the test secret, the gate
loads an admin row from a mocked service connection); only the DB and audit
layers are mocked, so the suite runs offline. The gate-sweep test iterates
every route under /api/admin and asserts it 401s without a token — any
future admin route is covered automatically.

Patch map (each module imports its own collaborators):
- gate:    app.services.admin_auth.get_service_connection (+ get_settings)
- login:   app.routers.admin.auth.get_service_connection / record_audit_event
- handlers: app.routers.admin.<module>.get_service_connection / record_audit_event
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app
from app.services.admin_auth import LoginRateLimiter, hash_password

ADMIN_SECRET = "test-admin-secret-value-32-bytes-long"  # ≥32 bytes: silences PyJWT HMAC warning
ADMIN_ID = uuid4()
ADMIN_EMAIL = "andy@traceflow.app"
PASSWORD = "correct horse battery staple"
PASSWORD_HASH = hash_password(PASSWORD)


def _fake_settings(secret: str = ADMIN_SECRET) -> Mock:
    s = Mock()
    s.admin_jwt_secret = secret
    return s


def _admin_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": ADMIN_ID,
        "email": ADMIN_EMAIL,
        "name": "Andy",
        "role": "owner",
        "is_active": True,
        "password_hash": PASSWORD_HASH,
        "last_login_at": None,
    }
    base.update(overrides)
    return base


def _token(admin_id: UUID = ADMIN_ID) -> str:
    now = datetime.now(UTC).timestamp()
    return jwt.encode(
        {"sub": str(admin_id), "email": ADMIN_EMAIL, "role": "owner",
         "iat": int(now), "exp": int(now) + 3600},
        ADMIN_SECRET,
        algorithm="HS256",
    )


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}"}


def _fake_service_conn(conn: Any):
    @asynccontextmanager
    async def _ctx():
        yield conn

    return _ctx


def _patch_conn(module: str, conn: Any):
    """Patch get_service_connection at one admin submodule's import site."""
    return patch(
        f"app.routers.admin.{module}.get_service_connection", new=_fake_service_conn(conn)
    )


def _patch_audit(module: str):
    return patch(f"app.routers.admin.{module}.record_audit_event", new=AsyncMock())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _admin_settings():
    """Known secret at both import sites that read it for admin auth."""
    with (
        patch("app.services.admin_auth.get_settings", return_value=_fake_settings()),
        patch("app.routers.admin.auth.get_settings", return_value=_fake_settings()),
    ):
        yield


@pytest.fixture(autouse=True)
def _fresh_limiter():
    """A clean rate limiter per test (auth.py reads it via the module attr)."""
    with patch("app.services.admin_auth.login_limiter", new=LoginRateLimiter()):
        yield


@pytest.fixture(autouse=True)
def _gate_conn():
    """require_admin_user loads this admin row on every gated request."""
    conn = AsyncMock()
    conn.fetchrow.return_value = _admin_row()
    with patch(
        "app.services.admin_auth.get_service_connection", new=_fake_service_conn(conn)
    ):
        yield conn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _lead_row(client_id: Any, external_id: str | None = None, **overrides: Any) -> dict[str, Any]:
    base = {
        "id": uuid4(),
        "client_id": client_id,
        "external_id": external_id,
        "crm_external_id": None,
        "source_system": "twilio_missed_call",
        "contact_name": "Jane Doe",
        "contact_company": None,
        "phone": "+15551112222",
        "email": None,
        "address": None,
        "service_type": "countertop",
        "sqft": 40.0,
        "budget_range": None,
        "timeframe": None,
        "classification": "potential_lead",
        "qualification_status": "qualifying",
        "qualification_score": None,
        "outcome": "open",
        "recovered_value": None,
        "outcome_source": None,
        "outcome_recorded_at": None,
        "notes": "",
        "raw_payload": {},
        "is_test": False,
        "created_at": datetime.now(UTC),
        "qualified_at": None,
        "pushed_to_crm_at": None,
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return base


def _lead_list_row(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": uuid4(),
        "created_at": datetime.now(UTC),
        "contact_name": "Jane Doe",
        "phone": "+15551112222",
        "email": None,
        "classification": "potential_lead",
        "qualification_status": "qualified",
        "qualification_score": 80,
        "service_type": "countertop",
        "budget_range": "5k-15k",
        "timeframe": None,
        "outcome": "open",
        "recovered_value": None,
        "external_id": None,
        "pushed_to_crm_at": None,
        "is_test": False,
        "message_count": 4,
        "last_message_at": datetime.now(UTC),
    }
    base.update(overrides)
    return base


def _config_row(client_id: Any, **overrides: Any) -> dict[str, Any]:
    """client_configs row shape (for the repush handler's ClientConfig load)."""
    base = {
        "client_id": client_id,
        "crm_provider": "monday",
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return base


def _config_join_row(client_id: Any, **overrides: Any) -> dict[str, Any]:
    """The _CONFIG_SELECT join row (clients + client_configs)."""
    base: dict[str, Any] = {
        "client_id": client_id,
        "slug": "acme",
        "business_name": "Acme Surfaces",
        "status": "active",
        "tier": "founding_partner",
        "timezone": "America/Los_Angeles",
        "business_hours": {"mon": {"open": "08:00", "close": "17:00"}},
        "service_area_zips": ["89101"],
        "twilio_number": "+17025550000",
        "vip_keywords": ["urgent"],
        "vip_value_threshold": 10000.0,
        "crm_provider": "hubspot",
        "crm_credentials": {"access_token": "pat-secret"},
        "webhook_signing_secrets": {"twilio": "shh"},
        "qualification_prompt": None,
        "greeting_template": None,
        "prompt_versions": {},
        "ai_interaction_cap_monthly": 1000,
        "ai_interactions_used": 250,
        "ai_period_resets_at": datetime.now(UTC),
        "brand": {"business_name": "Acme Surfaces"},
        "notification_emails": ["ops@acme.test"],
        "owner_alert_emails": ["owner@acme.test"],
        "owner_alert_phones": [],
        "feature_flags": {},
        "classification_config": {"spam_risk_threshold": "high"},
        "existing_customer_alert_contact": None,
        "vendor_allowlist": [],
        "revenue_config": {"mode": "crm", "monthly_fee": 397},
        "conversation_config": {"resume_window_hours": 336},
        "contact_config": {"source_of_truth": "auto"},
        "qualification_schema": {},
        "existing_customer_template": None,
        "vendor_ack_template": None,
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return base


# ===========================================================================
# The gate sweep — every admin route 401s without a token
# ===========================================================================


def _admin_routes() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/api/admin") or route.path == "/api/admin/login":
            continue
        path = (
            route.path.replace("{client_id}", str(uuid4()))
            .replace("{lead_id}", str(uuid4()))
            .replace("{integration}", "crm")
            .replace("{canonical_field}", "phone")
        )
        out.extend((path, m) for m in route.methods if m not in ("HEAD", "OPTIONS"))
    return out


def test_gate_sweep_every_admin_route_requires_a_token(client):
    routes = _admin_routes()
    assert len(routes) >= 13  # all tiers present; grows automatically
    for path, method in routes:
        bare = client.request(method, path)
        assert bare.status_code == 401, f"{method} {path} without token -> {bare.status_code}"
        garbage = client.request(method, path, headers={"Authorization": "Bearer garbage"})
        assert garbage.status_code == 401, f"{method} {path} garbage token -> {garbage.status_code}"


def test_gate_inactive_admin_is_403_on_gated_route(client):
    conn = AsyncMock()
    conn.fetchrow.return_value = _admin_row(is_active=False)
    with patch(
        "app.services.admin_auth.get_service_connection", new=_fake_service_conn(conn)
    ):
        resp = client.get("/api/admin/me", headers=_auth())
    assert resp.status_code == 403


# ===========================================================================
# Login + /me
# ===========================================================================


def _login(client, email: str = ADMIN_EMAIL, password: str = PASSWORD, row: Any = "default"):
    conn = AsyncMock()
    conn.fetchrow.return_value = _admin_row() if row == "default" else row
    with _patch_conn("auth", conn), _patch_audit("auth") as audit:
        resp = client.post("/api/admin/login", json={"email": email, "password": password})
    return resp, conn, audit


def test_login_success_mints_token_and_audits(client):
    resp, conn, audit = _login(client)
    assert resp.status_code == 200
    body = resp.json()
    payload = jwt.decode(body["access_token"], ADMIN_SECRET, algorithms=["HS256"])
    assert payload["sub"] == str(ADMIN_ID)
    assert body["admin"]["email"] == ADMIN_EMAIL
    assert body["token_type"] == "bearer"
    # last_login_at update fired
    update_sql = conn.execute.await_args.args[0]
    assert "last_login_at" in update_sql
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["operation"] == "login"
    assert audit.await_args.kwargs["actor"] == ADMIN_EMAIL


def test_login_normalizes_email(client):
    resp, conn, _ = _login(client, email="  ANDY@Traceflow.APP ")
    assert resp.status_code == 200
    assert conn.fetchrow.await_args.args[-1] == ADMIN_EMAIL


def test_login_failures_are_generic_401s(client):
    wrong_pw, _, _ = _login(client, password="nope")
    unknown, _, _ = _login(client, row=None)
    inactive, _, _ = _login(client, row=_admin_row(is_active=False))
    assert wrong_pw.status_code == unknown.status_code == inactive.status_code == 401
    # identical bodies: the endpoint never confirms an email exists
    assert wrong_pw.json() == unknown.json() == inactive.json()


def test_login_rate_limited_after_failures(client):
    for _ in range(5):
        resp, _, _ = _login(client, password="nope")
        assert resp.status_code == 401
    resp, _, _ = _login(client, password="nope")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    # and a correct password is locked out too until the window slides
    resp, _, _ = _login(client)
    assert resp.status_code == 429


def test_login_503_when_secret_unset(client):
    with (
        patch("app.routers.admin.auth.get_settings", return_value=_fake_settings("")),
        patch("app.services.admin_auth.get_settings", return_value=_fake_settings("")),
    ):
        resp = client.post(
            "/api/admin/login", json={"email": ADMIN_EMAIL, "password": PASSWORD}
        )
    assert resp.status_code == 503


def test_me_returns_gate_identity(client):
    resp = client.get("/api/admin/me", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(ADMIN_ID)
    assert body["email"] == ADMIN_EMAIL
    assert body["role"] == "owner"


# ===========================================================================
# Clients list + config
# ===========================================================================


def test_list_clients(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "id": cid,
            "slug": "acme",
            "business_name": "Acme Surfaces",
            "status": "active",
            "tier": "founding_partner",
            "timezone": "America/Los_Angeles",
            "launched_at": None,
            "created_at": datetime.now(UTC),
            "crm_provider": "hubspot",
            "twilio_number": "+17025550000",
            "leads_30d": 12,
        }
    ]
    with _patch_conn("clients", conn):
        resp = client.get("/api/admin/clients", headers=_auth())
    assert resp.status_code == 200
    [item] = resp.json()
    assert item["id"] == str(cid)
    assert item["leads_30d"] == 12
    assert item["status"] == "active"


def test_get_config_redacts_secrets(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchrow.return_value = _config_join_row(cid)
    with _patch_conn("clients", conn):
        resp = client.get(f"/api/admin/clients/{cid}/config", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert "crm_credentials" not in body
    assert "webhook_signing_secrets" not in body
    assert body["has_crm_credentials"] is True
    assert body["webhook_integrations"] == ["twilio"]
    # partial stored classification_config merges over model defaults
    assert body["classification_config"]["spam_risk_threshold"] == "high"
    assert body["classification_config"]["crm_lookup_enabled"] is True


def test_get_config_404_unknown_client(client):
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    with _patch_conn("clients", conn):
        resp = client.get(f"/api/admin/clients/{uuid4()}/config", headers=_auth())
    assert resp.status_code == 404


def test_put_config_partial_update_writes_only_provided_fields(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    conn.fetchrow.return_value = _config_join_row(cid)
    with _patch_conn("clients", conn), _patch_audit("clients") as audit:
        resp = client.put(
            f"/api/admin/clients/{cid}/config",
            headers=_auth(),
            json={"vip_keywords": ["urgent", "vip"]},
        )
    assert resp.status_code == 200
    sql = conn.execute.await_args.args[0]
    assert "UPDATE client_configs SET vip_keywords = $2" in sql
    assert "timezone" not in sql and "brand" not in sql
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["actor"] == ADMIN_EMAIL
    assert audit.await_args.kwargs["actor_user_id"] == ADMIN_ID
    assert audit.await_args.kwargs["snapshot"]["fields"] == ["vip_keywords"]


def test_put_config_timezone_routes_to_clients_table(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    conn.fetchrow.return_value = _config_join_row(cid)
    with _patch_conn("clients", conn), _patch_audit("clients"):
        resp = client.put(
            f"/api/admin/clients/{cid}/config",
            headers=_auth(),
            json={"timezone": "America/Chicago"},
        )
    assert resp.status_code == 200
    sql = conn.execute.await_args.args[0]
    assert "UPDATE clients SET timezone = $2" in sql
    assert "client_configs" not in sql


def test_put_config_nested_classification_block(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    conn.fetchrow.return_value = _config_join_row(cid)
    with _patch_conn("clients", conn), _patch_audit("clients"):
        resp = client.put(
            f"/api/admin/clients/{cid}/config",
            headers=_auth(),
            json={"classification_config": {"spam_risk_threshold": "low"}},
        )
    assert resp.status_code == 200
    written = conn.execute.await_args.args[2]
    # full block written (defaults merged by the typed model)
    assert written["spam_risk_threshold"] == "low"
    assert written["text_vendors"] is False


def test_put_config_rejects_unknown_and_secret_fields(client):
    cid = uuid4()
    for body in (
        {"definitely_not_a_field": 1},
        {"crm_credentials": {"access_token": "x"}},
        {"webhook_signing_secrets": {"twilio": "x"}},
    ):
        resp = client.put(f"/api/admin/clients/{cid}/config", headers=_auth(), json=body)
        assert resp.status_code == 422, body


def test_put_config_empty_body_400_and_unknown_client_404(client):
    cid = uuid4()
    resp = client.put(f"/api/admin/clients/{cid}/config", headers=_auth(), json={})
    assert resp.status_code == 400

    conn = AsyncMock()
    conn.fetchval.return_value = None
    with _patch_conn("clients", conn):
        resp = client.put(
            f"/api/admin/clients/{cid}/config", headers=_auth(), json={"timezone": "UTC"}
        )
    assert resp.status_code == 404


# ===========================================================================
# Leads — list / detail / conversation
# ===========================================================================


def test_list_leads_defaults_to_potential_lead(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = [_lead_list_row()]
    conn.fetchval.return_value = 7
    with _patch_conn("leads", conn):
        resp = client.get(f"/api/admin/clients/{cid}/leads", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 7
    assert len(body["data"]) == 1
    assert body["data"][0]["message_count"] == 4
    args = conn.fetch.await_args.args
    assert args[2] == "potential_lead"  # default filter
    assert args[3] is False  # include_test default


def test_list_leads_all_and_validation(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = []
    conn.fetchval.return_value = 0
    with _patch_conn("leads", conn):
        ok = client.get(
            f"/api/admin/clients/{cid}/leads?classification=all&include_test=true",
            headers=_auth(),
        )
        assert ok.status_code == 200
        assert conn.fetch.await_args.args[2] == "all"
        assert conn.fetch.await_args.args[3] is True

    bad_class = client.get(
        f"/api/admin/clients/{cid}/leads?classification=bogus", headers=_auth()
    )
    assert bad_class.status_code == 422
    bad_limit = client.get(f"/api/admin/clients/{cid}/leads?limit=300", headers=_auth())
    assert bad_limit.status_code == 422


def test_lead_detail_includes_intent_from_events(client):
    cid = uuid4()
    lead = _lead_row(cid)
    intent_at = datetime.now(UTC)
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        lead,
        {"payload": {"intent": "sales", "proceeded": True}, "created_at": intent_at},
    ]
    conn.fetchval.return_value = 3
    with _patch_conn("leads", conn):
        resp = client.get(f"/api/admin/clients/{cid}/leads/{lead['id']}", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"]["intent"] == "sales"
    assert body["intent"]["proceeded"] is True
    assert body["message_count"] == 3
    assert body["is_test"] is False
    # the lead lookup is client-scoped (the isolation invariant)
    assert "client_id = $2" in conn.fetchrow.await_args_list[0].args[0]


def test_lead_detail_no_intent_and_mismatch_404(client):
    cid = uuid4()
    lead = _lead_row(cid)
    conn = AsyncMock()
    conn.fetchrow.side_effect = [lead, None]
    conn.fetchval.return_value = 0
    with _patch_conn("leads", conn):
        resp = client.get(f"/api/admin/clients/{cid}/leads/{lead['id']}", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["intent"] is None

    conn = AsyncMock()
    conn.fetchrow.return_value = None  # wrong client_id or absent — same 404
    with _patch_conn("leads", conn):
        resp = client.get(f"/api/admin/clients/{cid}/leads/{uuid4()}", headers=_auth())
    assert resp.status_code == 404


def test_conversation_ordered_ascending(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    conn.fetch.return_value = [
        {
            "id": uuid4(),
            "direction": "outbound",
            "channel": "sms",
            "body": "Hi! Sorry we missed your call.",
            "ai_generated": True,
            "prompt_version": "greeting-v2",
            "created_at": datetime.now(UTC),
        }
    ]
    with _patch_conn("leads", conn):
        resp = client.get(
            f"/api/admin/clients/{cid}/leads/{uuid4()}/conversation", headers=_auth()
        )
    assert resp.status_code == 200
    assert resp.json()[0]["direction"] == "outbound"
    assert "ORDER BY created_at ASC" in conn.fetch.await_args.args[0]

    conn = AsyncMock()
    conn.fetchval.return_value = None
    with _patch_conn("leads", conn):
        resp = client.get(
            f"/api/admin/clients/{cid}/leads/{uuid4()}/conversation", headers=_auth()
        )
    assert resp.status_code == 404


# ===========================================================================
# Routing activity + log
# ===========================================================================


def test_routing_activity_breakdown_and_rates(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"bucket": "potential_lead", "n": 6},
        {"bucket": "spam", "n": 2},
        {"bucket": "active_conversation", "n": 2},
    ]
    with _patch_conn("activity", conn):
        resp = client.get(f"/api/admin/clients/{cid}/routing-activity", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_calls"] == 10
    assert body["breakdown"]["active_conversation"] == 2
    assert body["genuine_lead_rate"] == 0.6
    assert body["spam_rate"] == 0.2
    assert body["window_days"] == 30


def test_routing_activity_zero_window_is_quiet_not_crash(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = []
    with _patch_conn("activity", conn):
        resp = client.get(f"/api/admin/clients/{cid}/routing-activity", headers=_auth())
    body = resp.json()
    assert body["total_calls"] == 0
    assert body["genuine_lead_rate"] == 0.0
    assert body["spam_rate"] == 0.0

    bad = client.get(
        f"/api/admin/clients/{cid}/routing-activity?window_days=0", headers=_auth()
    )
    assert bad.status_code == 422


def test_routing_log_mapping(client):
    cid = uuid4()
    lead_id = uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "created_at": datetime.now(UTC),
            "event_type": "twilio_missed_call_received",
            "payload": {"route": "spam", "classification": "spam", "reason": "high risk score"},
            "lead_id": lead_id,
            "phone": "+15559990000",
        },
        {
            "created_at": datetime.now(UTC),
            "event_type": "missed_call_during_active_conversation",
            "payload": {"call_sid": "CA1", "from": "+15551112222"},
            "lead_id": None,
            "phone": None,
        },
    ]
    with _patch_conn("activity", conn):
        resp = client.get(f"/api/admin/clients/{cid}/routing-log", headers=_auth())
    assert resp.status_code == 200
    first, second = resp.json()
    assert first["routing_decision"] == "spam"
    assert first["caller"] == "+15559990000"
    assert first["reason"] == "high risk score"
    assert second["routing_decision"] == "active_conversation"
    assert second["caller"] == "+15551112222"  # falls back to payload 'from'


# ===========================================================================
# Repush (parity port) + outcome + mark-test
# ===========================================================================


def _repush(client, cid, conn, adapter=None):
    adapter = adapter if adapter is not None else Mock()
    with (
        _patch_conn("leads", conn),
        _patch_audit("leads") as audit,
        patch("app.routers.admin.leads.get_adapter", return_value=adapter),
    ):
        resp = client.post(
            f"/api/admin/clients/{cid}/leads/{uuid4()}/repush", headers=_auth()
        )
    return resp, audit


def test_repush_lead_not_found_or_wrong_client_404(client):
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    resp, _ = _repush(client, uuid4(), conn)
    assert resp.status_code == 404
    # the lookup is client-scoped — this is what makes a cross-client id a 404
    assert "client_id = $2" in conn.fetchrow.await_args.args[0]


def test_repush_no_provider_400(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_lead_row(cid), _config_row(cid, crm_provider=None)]
    resp, _ = _repush(client, cid, conn)
    assert resp.status_code == 400


def test_repush_pushes_when_no_crm_external_id(client):
    cid = uuid4()
    conn = AsyncMock()
    # A missed-call lead carries a source-system external_id (CallSid) but no
    # crm_external_id — it must still PUSH, not take the update branch.
    conn.fetchrow.side_effect = [
        _lead_row(cid, external_id="CAcallsid123", crm_external_id=None),
        _config_row(cid),
    ]
    adapter = Mock()
    adapter.push_lead = AsyncMock(return_value="monday-item-999")
    adapter.update_lead = AsyncMock()

    resp, audit = _repush(client, cid, conn, adapter)
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "push"
    assert body["external_id"] == "monday-item-999"
    adapter.push_lead.assert_awaited_once()
    adapter.update_lead.assert_not_called()
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["operation"] == "sync"
    assert audit.await_args.kwargs["actor"] == ADMIN_EMAIL  # was 'founder_retool'
    assert audit.await_args.kwargs["actor_user_id"] == ADMIN_ID


def test_repush_updates_when_crm_external_id_set(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _lead_row(cid, crm_external_id="monday-item-existing"),
        _config_row(cid),
    ]
    adapter = Mock()
    adapter.push_lead = AsyncMock()
    adapter.update_lead = AsyncMock()

    resp, _ = _repush(client, cid, conn, adapter)
    assert resp.status_code == 200
    assert resp.json()["action"] == "update"
    adapter.update_lead.assert_awaited_once()
    # the CRM record id (not a source id) is what goes to the adapter
    assert adapter.update_lead.await_args.args[0] == "monday-item-existing"
    adapter.push_lead.assert_not_called()


def test_outcome_won_without_value_400(client):
    resp = client.post(
        f"/api/admin/clients/{uuid4()}/leads/{uuid4()}/outcome",
        headers=_auth(),
        json={"outcome": "won"},
    )
    assert resp.status_code == 400


def test_outcome_lead_not_found_404(client):
    conn = AsyncMock()
    conn.fetchval.return_value = None
    with _patch_conn("leads", conn):
        resp = client.post(
            f"/api/admin/clients/{uuid4()}/leads/{uuid4()}/outcome",
            headers=_auth(),
            json={"outcome": "won", "recovered_value": 4500},
        )
    assert resp.status_code == 404


def test_outcome_won_records_value_and_audits_admin(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    with _patch_conn("leads", conn), _patch_audit("leads") as audit:
        resp = client.post(
            f"/api/admin/clients/{cid}/leads/{uuid4()}/outcome",
            headers=_auth(),
            json={"outcome": "won", "recovered_value": "4500.00"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "won"
    assert body["recovered_value"] == "4500.00"
    assert body["source"] == "owner_report"
    conn.execute.assert_awaited_once()
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["actor"] == ADMIN_EMAIL


def test_mark_test_sets_and_unsets(client):
    cid = uuid4()
    lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    with _patch_conn("leads", conn), _patch_audit("leads") as audit:
        resp = client.post(
            f"/api/admin/clients/{cid}/leads/{lead_id}/mark-test",
            headers=_auth(),
            json={},
        )
    assert resp.status_code == 200
    assert resp.json() == {"lead_id": str(lead_id), "is_test": True}
    assert conn.execute.await_args.args[1] is True
    assert audit.await_args.kwargs["snapshot"] == {"is_test": True}

    conn = AsyncMock()
    conn.fetchval.return_value = 1
    with _patch_conn("leads", conn), _patch_audit("leads"):
        resp = client.post(
            f"/api/admin/clients/{cid}/leads/{lead_id}/mark-test",
            headers=_auth(),
            json={"is_test": False},
        )
    assert resp.json()["is_test"] is False

    conn = AsyncMock()
    conn.fetchval.return_value = None
    with _patch_conn("leads", conn):
        resp = client.post(
            f"/api/admin/clients/{cid}/leads/{uuid4()}/mark-test", headers=_auth(), json={}
        )
    assert resp.status_code == 404


# ===========================================================================
# Field mappings
# ===========================================================================


def test_field_mappings_list_and_filter(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "integration": "crm",
            "canonical_field": "phone",
            "external_field": "phone",
            "external_field_type": "standard",
            "transform": None,
            "notes": None,
            "updated_at": datetime.now(UTC),
        }
    ]
    with _patch_conn("mappings", conn):
        resp = client.get(f"/api/admin/clients/{cid}/field-mappings", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()[0]["canonical_field"] == "phone"
        assert conn.fetch.await_args.args[2] is None  # no integration filter

        resp = client.get(
            f"/api/admin/clients/{cid}/field-mappings?integration=crm", headers=_auth()
        )
        assert conn.fetch.await_args.args[2] == "crm"


def test_field_mapping_upsert(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    conn.fetchrow.return_value = {
        "integration": "crm",
        "canonical_field": "sqft",
        "external_field": "cf_sqft",
        "external_field_type": "custom_field",
        "transform": None,
        "notes": "GHL custom field id",
        "updated_at": datetime.now(UTC),
    }
    with _patch_conn("mappings", conn), _patch_audit("mappings") as audit:
        resp = client.put(
            f"/api/admin/clients/{cid}/field-mappings",
            headers=_auth(),
            json={
                "integration": "crm",
                "canonical_field": "sqft",
                "external_field": "cf_sqft",
                "external_field_type": "custom_field",
                "notes": "GHL custom field id",
            },
        )
    assert resp.status_code == 200
    sql = conn.fetchrow.await_args.args[0]
    assert "ON CONFLICT (client_id, integration, canonical_field) DO UPDATE" in sql
    assert audit.await_args.kwargs["operation"] == "update"
    assert audit.await_args.kwargs["target_id"] == "crm:sqft"


def test_field_mapping_upsert_bad_type_422_and_unknown_client_404(client):
    cid = uuid4()
    resp = client.put(
        f"/api/admin/clients/{cid}/field-mappings",
        headers=_auth(),
        json={
            "integration": "crm",
            "canonical_field": "sqft",
            "external_field": "x",
            "external_field_type": "not-a-type",
        },
    )
    assert resp.status_code == 422

    conn = AsyncMock()
    conn.fetchval.return_value = None
    with _patch_conn("mappings", conn):
        resp = client.put(
            f"/api/admin/clients/{cid}/field-mappings",
            headers=_auth(),
            json={"integration": "crm", "canonical_field": "sqft", "external_field": "x"},
        )
    assert resp.status_code == 404


def test_field_mapping_delete_audits_old_row(client):
    cid = uuid4()
    old_row = {
        "integration": "crm",
        "canonical_field": "sqft",
        "external_field": "cf_sqft",
        "external_field_type": "custom_field",
        "transform": None,
        "notes": None,
    }
    conn = AsyncMock()
    conn.fetchrow.return_value = old_row
    with _patch_conn("mappings", conn), _patch_audit("mappings") as audit:
        resp = client.delete(
            f"/api/admin/clients/{cid}/field-mappings/crm/sqft", headers=_auth()
        )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    conn.execute.assert_awaited_once()
    assert audit.await_args.kwargs["operation"] == "delete"
    assert audit.await_args.kwargs["snapshot"] == old_row

    conn = AsyncMock()
    conn.fetchrow.return_value = None
    with _patch_conn("mappings", conn):
        resp = client.delete(
            f"/api/admin/clients/{cid}/field-mappings/crm/sqft", headers=_auth()
        )
    assert resp.status_code == 404


# ===========================================================================
# AI usage
# ===========================================================================


def test_ai_usage_get(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "ai_interaction_cap_monthly": 1000,
        "ai_interactions_used": 250,
        "ai_period_resets_at": datetime.now(UTC),
    }
    with _patch_conn("clients", conn):
        resp = client.get(f"/api/admin/clients/{cid}/ai-usage", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["remaining"] == 750
    assert body["percent_used"] == 25.0


def test_ai_usage_reset(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "ai_interaction_cap_monthly": 1000,
        "ai_interactions_used": 250,
        "ai_period_resets_at": datetime.now(UTC),
    }
    with _patch_conn("clients", conn), _patch_audit("clients") as audit:
        resp = client.post(f"/api/admin/clients/{cid}/ai-usage/reset", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["used"] == 0
    assert "ai_interactions_used = 0" in conn.execute.await_args.args[0]
    assert audit.await_args.kwargs["snapshot"]["previous_used"] == 250

    conn = AsyncMock()
    conn.fetchrow.return_value = None
    with _patch_conn("clients", conn):
        resp = client.post(f"/api/admin/clients/{cid}/ai-usage/reset", headers=_auth())
    assert resp.status_code == 404


# ===========================================================================
# Config: new Slice 2–3 blocks + qualification-schema validation
# ===========================================================================


def test_put_config_bad_qualification_schema_is_422(client):
    # An enum field with no options fails the QualificationSchema model → 422
    # at request validation, before the handler runs.
    resp = client.put(
        f"/api/admin/clients/{uuid4()}/config",
        json={"qualification_schema": {"fields": [
            {"key": "m", "label": "M", "type": "enum", "ask": "?"}
        ]}},
        headers=_auth(),
    )
    assert resp.status_code == 422


def test_put_config_valid_qualification_schema_accepted(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchval.return_value = 1  # client exists
    conn.fetchrow.return_value = _config_join_row(cid)  # _CONFIG_SELECT re-fetch
    valid = {"fields": [
        {"key": "contact_name", "label": "Name", "type": "string",
         "maps_to": "contact_name", "ask": "Your name?"}
    ]}
    with _patch_conn("clients", conn), _patch_audit("clients"):
        resp = client.put(
            f"/api/admin/clients/{cid}/config",
            json={"qualification_schema": valid},
            headers=_auth(),
        )
    assert resp.status_code == 200
    assert any("UPDATE client_configs" in c.args[0] for c in conn.execute.call_args_list)


# ===========================================================================
# Contacts (Slice 5)
# ===========================================================================


def _contact_row(client_id: Any, **overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid4(), "client_id": client_id, "phone": "+15551112222", "name": "Maria",
        "contact_type": "prospect", "contact_type_source": "inferred", "contact_type_at": now,
        "contact_type_reason": None, "crm_external_id": None, "known_facts": {"zip": "89101"},
        "summary": "Prior countertop inquiry.", "last_intent": None,
        "call_count": 2, "lead_count": 1, "first_seen_at": now, "last_seen_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


def _contact_list_row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid4(), "phone": "+15551112222", "name": "Maria", "contact_type": "prospect",
        "contact_type_source": "inferred", "call_count": 2, "lead_count": 1,
        "last_seen_at": now, "summary": None,
    }
    base.update(overrides)
    return base


def _contact_lead_row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid4(), "created_at": now, "qualification_status": "qualified",
        "classification": "potential_lead", "service_type": "countertop",
        "qualification_score": 80, "value_score": 65, "outcome": "open", "recovered_value": None,
    }
    base.update(overrides)
    return base


def test_list_contacts(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = [_contact_list_row(), _contact_list_row(contact_type="customer")]
    conn.fetchval.return_value = 2
    with _patch_conn("contacts", conn):
        resp = client.get(f"/api/admin/clients/{cid}/contacts", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert len(body["data"]) == 2


def test_list_contacts_threads_type_and_search(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = []
    conn.fetchval.return_value = 0
    with _patch_conn("contacts", conn):
        resp = client.get(
            f"/api/admin/clients/{cid}/contacts?contact_type=customer&search=maria",
            headers=_auth(),
        )
    assert resp.status_code == 200
    args = conn.fetch.call_args.args
    assert args[2] == "customer"  # $2 type filter
    assert args[3] == "maria"     # $3 search


def test_get_contact_detail_includes_leads_and_scores(client):
    cid, contact_id = uuid4(), uuid4()
    conn = AsyncMock()
    conn.fetchrow.return_value = _contact_row(cid, id=contact_id)
    conn.fetch.return_value = [_contact_lead_row()]
    with _patch_conn("contacts", conn):
        resp = client.get(f"/api/admin/clients/{cid}/contacts/{contact_id}", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == "Prior countertop inquiry."
    assert body["known_facts"] == {"zip": "89101"}
    assert body["leads"][0]["qualification_score"] == 80  # completeness
    assert body["leads"][0]["value_score"] == 65          # value, kept separate


def test_get_contact_404_wrong_client(client):
    conn = AsyncMock()
    conn.fetchrow.return_value = None  # client-scoped miss → 404
    with _patch_conn("contacts", conn):
        resp = client.get(f"/api/admin/clients/{uuid4()}/contacts/{uuid4()}", headers=_auth())
    assert resp.status_code == 404


def test_retype_contact_writes_manual(client):
    cid, contact_id = uuid4(), uuid4()
    conn = AsyncMock()
    conn.fetchval.return_value = 1  # contact exists
    with (
        _patch_conn("contacts", conn),
        _patch_audit("contacts"),
        patch("app.routers.admin.contacts.set_contact_type",
              new=AsyncMock(return_value=True)) as set_type,
    ):
        resp = client.patch(
            f"/api/admin/clients/{cid}/contacts/{contact_id}",
            json={"contact_type": "customer", "reason": "known client"},
            headers=_auth(),
        )
    assert resp.status_code == 200
    assert resp.json()["source"] == "manual"
    assert set_type.await_args.args[3].value == "manual"  # source=manual, always


def test_retype_contact_can_set_blocked(client):
    cid, contact_id = uuid4(), uuid4()
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    with (
        _patch_conn("contacts", conn),
        _patch_audit("contacts"),
        patch("app.routers.admin.contacts.set_contact_type", new=AsyncMock(return_value=True)),
    ):
        resp = client.patch(
            f"/api/admin/clients/{cid}/contacts/{contact_id}",
            json={"contact_type": "blocked"},
            headers=_auth(),
        )
    assert resp.status_code == 200  # blocked is a manual-only decision — allowed here


def test_retype_contact_404(client):
    conn = AsyncMock()
    conn.fetchval.return_value = None
    with _patch_conn("contacts", conn), _patch_audit("contacts"):
        resp = client.patch(
            f"/api/admin/clients/{uuid4()}/contacts/{uuid4()}",
            json={"contact_type": "customer"},
            headers=_auth(),
        )
    assert resp.status_code == 404


def test_retype_contact_bad_type_422(client):
    resp = client.patch(
        f"/api/admin/clients/{uuid4()}/contacts/{uuid4()}",
        json={"contact_type": "vip"},  # not one of the six
        headers=_auth(),
    )
    assert resp.status_code == 422
