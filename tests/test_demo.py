"""DEMO_MODE tests — the public, no-login, read-only admin demo.

Covers the four things that make the demo safe: it's gated on DEMO_MODE; a
demo-role token reads through the in-memory FakeConn (never the DB); every
mutating verb is blocked with a 403; and the demo identity is inert when
DEMO_MODE is off. The leak test pins the real pool to a sentinel that raises if
touched — a demo read must complete without going near it.

Patch map (mirrors test_admin.py — each module reads its own get_settings):
- demo-login route: app.routers.demo.get_settings
- gate + token mint: app.services.admin_auth.get_settings
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

import app.db as db
from app.demo import DEMO_ADMIN_ID, DEMO_EMAIL, clients
from app.main import app

ADMIN_SECRET = "test-admin-secret-value-32-bytes-long"  # ≥32 bytes
A_CLIENT = str(next(iter(clients())))
A_LEAD = str(clients()[next(iter(clients()))]["leads"][0]["id"])
A_CONTACT = str(clients()[next(iter(clients()))]["contacts"][0]["id"])


def _settings(demo_mode: bool = True) -> Mock:
    s = Mock()
    s.admin_jwt_secret = ADMIN_SECRET
    s.demo_mode = demo_mode
    return s


def _demo_token(role: str = "demo") -> str:
    now = int(datetime.now(UTC).timestamp())
    return jwt.encode(
        {"sub": str(DEMO_ADMIN_ID), "email": DEMO_EMAIL, "role": role,
         "iat": now, "exp": now + 3600},
        ADMIN_SECRET,
        algorithm="HS256",
    )


def _auth(role: str = "demo") -> dict[str, str]:
    return {"Authorization": f"Bearer {_demo_token(role)}"}


def _fake_conn(conn: Any):
    @asynccontextmanager
    async def _ctx():
        yield conn

    return _ctx


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def demo_on():
    """DEMO_MODE=true at both sites that read it."""
    with (
        patch("app.routers.demo.get_settings", return_value=_settings(True)),
        patch("app.services.admin_auth.get_settings", return_value=_settings(True)),
    ):
        yield


@pytest.fixture
def demo_off():
    with (
        patch("app.routers.demo.get_settings", return_value=_settings(False)),
        patch("app.services.admin_auth.get_settings", return_value=_settings(False)),
    ):
        yield


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def test_demo_login_is_404_when_mode_off(client, demo_off):
    assert client.post("/api/demo-login").status_code == 404


def test_demo_login_issues_a_demo_token_when_on(client, demo_on):
    resp = client.post("/api/demo-login")
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["admin"]["role"] == "demo"
    assert body["admin"]["email"] == DEMO_EMAIL
    decoded = jwt.decode(body["access_token"], ADMIN_SECRET, algorithms=["HS256"])
    assert decoded["role"] == "demo"
    assert decoded["sub"] == str(DEMO_ADMIN_ID)


def test_demo_login_route_is_outside_admin_prefix():
    # The gate-sweep test (test_admin.py) asserts every /api/admin route is
    # gated; demo-login must not be swept into that, so it lives elsewhere.
    assert "/api/demo-login" in {getattr(r, "path", None) for r in app.routes}
    assert not "/api/demo-login".startswith("/api/admin")


# ---------------------------------------------------------------------------
# Reads — served from the FakeConn
# ---------------------------------------------------------------------------


def test_demo_token_reads_clients(client, demo_on):
    resp = client.get("/api/admin/clients", headers=_auth())
    assert resp.status_code == 200
    assert len(resp.json()) == len(clients())


@pytest.mark.parametrize(
    "path",
    [
        "/api/admin/clients",
        f"/api/admin/clients/{A_CLIENT}/config",
        f"/api/admin/clients/{A_CLIENT}/ai-usage",
        f"/api/admin/clients/{A_CLIENT}/leads?classification=all&include_test=true",
        f"/api/admin/clients/{A_CLIENT}/leads/{A_LEAD}",
        f"/api/admin/clients/{A_CLIENT}/leads/{A_LEAD}/conversation",
        f"/api/admin/clients/{A_CLIENT}/routing-activity",
        f"/api/admin/clients/{A_CLIENT}/routing-log",
        f"/api/admin/clients/{A_CLIENT}/field-mappings",
        f"/api/admin/clients/{A_CLIENT}/contacts",
        f"/api/admin/clients/{A_CLIENT}/contacts?contact_type=customer&search=a",
        f"/api/admin/clients/{A_CLIENT}/contacts/{A_CONTACT}",
    ],
)
def test_demo_token_reaches_every_read(client, demo_on, path):
    assert client.get(path, headers=_auth()).status_code == 200


# ---------------------------------------------------------------------------
# Writes — every mutating verb is blocked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("PUT", f"/api/admin/clients/{A_CLIENT}/config", {"timezone": "America/New_York"}),
        ("POST", f"/api/admin/clients/{A_CLIENT}/ai-usage/reset", None),
        ("POST", f"/api/admin/clients/{A_CLIENT}/leads/{A_LEAD}/repush", None),
        ("POST", f"/api/admin/clients/{A_CLIENT}/leads/{A_LEAD}/outcome",
         {"outcome": "won", "recovered_value": 1000}),
        ("POST", f"/api/admin/clients/{A_CLIENT}/leads/{A_LEAD}/mark-test", {"is_test": True}),
        ("PUT", f"/api/admin/clients/{A_CLIENT}/field-mappings",
         {"integration": "crm", "canonical_field": "x", "external_field": "y"}),
        ("DELETE", f"/api/admin/clients/{A_CLIENT}/field-mappings/crm/sqft", None),
    ],
)
def test_demo_token_blocks_every_write(client, demo_on, method, path, body):
    resp = client.request(method, path, headers=_auth(), json=body)
    assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"
    assert "read-only" in resp.json()["detail"].lower()


def test_demo_repush_never_calls_the_crm_adapter(client, demo_on):
    # The repush handler would otherwise hit a real CRM. The write-block must
    # stop it before the adapter is ever resolved.
    with patch("app.routers.admin.leads.get_adapter") as get_adapter:
        resp = client.post(
            f"/api/admin/clients/{A_CLIENT}/leads/{A_LEAD}/repush", headers=_auth()
        )
    assert resp.status_code == 403
    get_adapter.assert_not_called()


# ---------------------------------------------------------------------------
# Confinement
# ---------------------------------------------------------------------------


def test_demo_identity_is_inert_when_mode_off(client, demo_off):
    # A demo-role token with DEMO_MODE off falls through to the normal DB load
    # and 401s as an unknown admin (no such admin_users row).
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    with patch("app.services.admin_auth.get_service_connection", new=_fake_conn(conn)):
        resp = client.get("/api/admin/clients", headers=_auth())
    assert resp.status_code == 401


def test_demo_read_never_touches_the_real_pool(client, demo_on):
    class Boom:
        def acquire(self, *a, **k):
            raise AssertionError("demo request reached the real DB pool")

        async def close(self):
            pass

    original = db._pool
    db._pool = Boom()
    try:
        resp = client.get("/api/admin/clients", headers=_auth())
    finally:
        db._pool = original
    assert resp.status_code == 200
    assert len(resp.json()) == len(clients())
