"""Admin endpoint tests — verify_admin_token + /api/admin/leads/{id}/repush."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from app.main import app
from app.middleware.auth import verify_admin_token

ADMIN_SECRET = "test-admin-secret-value-32-bytes-long"  # ≥32 chars silences PyJWT's HMAC key-length warning


def _fake_settings(secret: str = ADMIN_SECRET) -> Mock:
    s = Mock()
    s.admin_jwt_secret = secret
    return s


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _admin_auth_settings():
    """Patch the admin verifier's settings to a known good secret."""
    with patch("app.middleware.auth.get_settings", return_value=_fake_settings()):
        yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _fake_service_conn(conn: Any):
    @asynccontextmanager
    async def _ctx():
        yield conn

    return _ctx


def _lead_row(client_id: Any, external_id: str | None = None, **overrides: Any) -> dict[str, Any]:
    base = {
        "id": uuid4(),
        "client_id": client_id,
        "external_id": external_id,
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
        "qualification_status": "qualifying",
        "qualification_score": None,
        "notes": "",
        "raw_payload": {},
        "created_at": datetime.now(UTC),
        "qualified_at": None,
        "pushed_to_crm_at": None,
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return base


def _config_row(client_id: Any, **overrides: Any) -> dict[str, Any]:
    base = {
        "client_id": client_id,
        "crm_provider": "monday",
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# verify_admin_token — direct unit tests
# ---------------------------------------------------------------------------

def test_verify_admin_token_accepts_bare_secret():
    verify_admin_token(_bearer(ADMIN_SECRET))  # no raise


def test_verify_admin_token_accepts_valid_hs256_jwt():
    token = jwt.encode(
        {"sub": "admin", "exp": int(time.time()) + 60}, ADMIN_SECRET, algorithm="HS256"
    )
    verify_admin_token(_bearer(token))  # no raise


def test_verify_admin_token_rejects_wrong_token():
    with pytest.raises(HTTPException) as exc:
        verify_admin_token(_bearer("not-the-secret"))
    assert exc.value.status_code == 401


def test_verify_admin_token_rejects_expired_jwt():
    token = jwt.encode(
        {"sub": "admin", "exp": int(time.time()) - 60}, ADMIN_SECRET, algorithm="HS256"
    )
    with pytest.raises(HTTPException) as exc:
        verify_admin_token(_bearer(token))
    assert exc.value.status_code == 401


def test_verify_admin_token_503_when_secret_unset():
    with patch("app.middleware.auth.get_settings", return_value=_fake_settings(secret="")):
        with pytest.raises(HTTPException) as exc:
            verify_admin_token(_bearer("anything"))
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# /api/admin/leads/{lead_id}/repush — endpoint tests via TestClient
# ---------------------------------------------------------------------------

def test_repush_no_auth_returns_401(client):
    # FastAPI's HTTPBearer auto_error returns 401 (semantically correct for
    # "no credentials provided") on this version; older versions returned 403.
    resp = client.post(f"/api/admin/leads/{uuid4()}/repush")
    assert resp.status_code == 401


def test_repush_wrong_token_returns_401(client):
    resp = client.post(
        f"/api/admin/leads/{uuid4()}/repush",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


def test_repush_lead_not_found_returns_404(client):
    conn = AsyncMock()
    conn.fetchrow.return_value = None  # no lead

    with patch("app.routers.admin.get_service_connection", new=_fake_service_conn(conn)):
        resp = client.post(
            f"/api/admin/leads/{uuid4()}/repush",
            headers={"Authorization": f"Bearer {ADMIN_SECRET}"},
        )
    assert resp.status_code == 404


def test_repush_no_provider_returns_400(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _lead_row(cid),
        _config_row(cid, crm_provider=None),
    ]

    with patch("app.routers.admin.get_service_connection", new=_fake_service_conn(conn)):
        resp = client.post(
            f"/api/admin/leads/{uuid4()}/repush",
            headers={"Authorization": f"Bearer {ADMIN_SECRET}"},
        )
    assert resp.status_code == 400


def test_repush_pushes_when_no_external_id(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _lead_row(cid, external_id=None),
        _config_row(cid),
    ]

    adapter = Mock()
    adapter.push_lead = AsyncMock(return_value="monday-item-999")
    adapter.update_lead = AsyncMock()

    with (
        patch("app.routers.admin.get_service_connection", new=_fake_service_conn(conn)),
        patch("app.routers.admin.get_adapter", return_value=adapter),
        patch("app.routers.admin.record_audit_event", new=AsyncMock()) as mock_audit,
    ):
        resp = client.post(
            f"/api/admin/leads/{uuid4()}/repush",
            headers={"Authorization": f"Bearer {ADMIN_SECRET}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "push"
    assert body["external_id"] == "monday-item-999"
    adapter.push_lead.assert_awaited_once()
    adapter.update_lead.assert_not_called()
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args.kwargs["operation"] == "sync"
    assert mock_audit.call_args.kwargs["actor"] == "founder_retool"


def test_repush_updates_when_external_id_set(client):
    cid = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _lead_row(cid, external_id="monday-item-existing"),
        _config_row(cid),
    ]

    adapter = Mock()
    adapter.push_lead = AsyncMock()
    adapter.update_lead = AsyncMock()

    with (
        patch("app.routers.admin.get_service_connection", new=_fake_service_conn(conn)),
        patch("app.routers.admin.get_adapter", return_value=adapter),
        patch("app.routers.admin.record_audit_event", new=AsyncMock()),
    ):
        resp = client.post(
            f"/api/admin/leads/{uuid4()}/repush",
            headers={"Authorization": f"Bearer {ADMIN_SECRET}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "update"
    assert body["external_id"] == "monday-item-existing"
    adapter.update_lead.assert_awaited_once()
    adapter.push_lead.assert_not_called()
