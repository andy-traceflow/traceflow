"""Portal auth keystone — user → client_id → RLS scoping.

Two layers:
  - Pure-function tests for active-tenant resolution (always run).
  - DB-backed tests proving _load_memberships is user-scoped and that a
    resolved principal's tenant context makes get_connection() RLS-scoped.
    These skip without TRACEFLOW_TEST_DB_URL, like the tenant isolation suite.

Run the full set:
    TRACEFLOW_TEST_DB_URL=postgres://... pytest tests/test_portal_auth.py -v
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import HTTPException

from app.middleware.portal_auth import (
    _load_memberships,
    _resolve_active_tenant,
)
from app.services.permissions import UserPermissions

# ===========================================================================
# Pure-function: active-tenant resolution (no DB)
# ===========================================================================


def _memberships(*client_ids: UUID) -> dict[UUID, UserPermissions]:
    return {cid: UserPermissions() for cid in client_ids}


def test_single_membership_resolves_without_header() -> None:
    cid = uuid4()
    assert _resolve_active_tenant(_memberships(cid), None) == cid


def test_multi_membership_requires_header() -> None:
    a, b = uuid4(), uuid4()
    with pytest.raises(HTTPException) as exc:
        _resolve_active_tenant(_memberships(a, b), None)
    assert exc.value.status_code == 400


def test_multi_membership_uses_valid_header() -> None:
    a, b = uuid4(), uuid4()
    assert _resolve_active_tenant(_memberships(a, b), str(b)) == b


def test_header_for_non_member_tenant_is_403() -> None:
    a = uuid4()
    outsider = uuid4()
    with pytest.raises(HTTPException) as exc:
        _resolve_active_tenant(_memberships(a), str(outsider))
    assert exc.value.status_code == 403


def test_malformed_header_is_400() -> None:
    a = uuid4()
    with pytest.raises(HTTPException) as exc:
        _resolve_active_tenant(_memberships(a), "not-a-uuid")
    assert exc.value.status_code == 400


# ===========================================================================
# DB-backed: membership loading + RLS scoping
# ===========================================================================


@pytest.fixture
async def raw_conn(db_url: str):
    """Service-role connection for setup/teardown (bypasses RLS)."""
    conn = await asyncpg.connect(db_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def pool(db_url: str):
    """Initialize the app's asyncpg pool so portal_auth's service/tenant
    connections work, then tear it down."""
    from app import db

    await db.init_pool()
    try:
        yield
    finally:
        await db.close_pool()


@pytest.fixture
async def portal_fixture(raw_conn: asyncpg.Connection):
    """Two tenants, one user per tenant, one lead per tenant.

    user_a is a tenant-admin of client_a. Everything is created as the
    default (bypassrls) role and cleaned up afterward.
    """
    await raw_conn.execute("RESET ROLE")
    client_a, client_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()

    await raw_conn.execute(
        "INSERT INTO clients (id, slug, business_name) VALUES ($1, $2, $3), ($4, $5, $6)",
        client_a, f"portal-a-{client_a}", "Portal A",
        client_b, f"portal-b-{client_b}", "Portal B",
    )
    await raw_conn.execute(
        "INSERT INTO auth.users (id, email) VALUES ($1, $2), ($3, $4)",
        user_a, "a@example.com", user_b, "b@example.com",
    )
    await raw_conn.execute(
        """
        INSERT INTO user_permissions (client_id, user_id, is_admin, can_edit_config)
        VALUES ($1, $2, TRUE, TRUE), ($3, $4, FALSE, FALSE)
        """,
        client_a, user_a, client_b, user_b,
    )
    lead_a = await raw_conn.fetchval(
        "INSERT INTO leads (client_id, source_system, raw_payload) "
        "VALUES ($1, 'test', $2::jsonb) RETURNING id",
        client_a, json.dumps({"marker": "a"}),
    )
    lead_b = await raw_conn.fetchval(
        "INSERT INTO leads (client_id, source_system, raw_payload) "
        "VALUES ($1, 'test', $2::jsonb) RETURNING id",
        client_b, json.dumps({"marker": "b"}),
    )

    yield {
        "client_a": client_a, "client_b": client_b,
        "user_a": user_a, "user_b": user_b,
        "lead_a": lead_a, "lead_b": lead_b,
    }

    await raw_conn.execute("RESET ROLE")
    # clients cascade to configs/permissions/leads; auth.users cascades permissions.
    await raw_conn.execute("DELETE FROM clients WHERE id IN ($1, $2)", client_a, client_b)
    await raw_conn.execute("DELETE FROM auth.users WHERE id IN ($1, $2)", user_a, user_b)


@pytest.mark.asyncio
async def test_load_memberships_is_user_scoped(pool, portal_fixture) -> None:
    """A user sees only their own tenant membership + correct flags."""
    fx = portal_fixture
    memberships = await _load_memberships(str(fx["user_a"]))

    assert set(memberships) == {fx["client_a"]}, "membership leaked another tenant"
    assert memberships[fx["client_a"]].is_admin is True
    assert memberships[fx["client_a"]].can_edit_config is True


@pytest.mark.asyncio
async def test_resolved_principal_scopes_reads_to_its_tenant(pool, portal_fixture) -> None:
    """Setting the resolved tenant makes get_connection() RLS-scoped: user_a
    sees client_a's lead and NOT client_b's."""
    from app.db import get_connection, set_current_tenant

    fx = portal_fixture
    memberships = await _load_memberships(str(fx["user_a"]))
    active = _resolve_active_tenant(memberships, None)
    assert active == fx["client_a"]

    set_current_tenant(active)
    try:
        async with get_connection() as conn:
            rows = await conn.fetch("SELECT id FROM leads")
        ids = {r["id"] for r in rows}
    finally:
        set_current_tenant(None)

    assert fx["lead_a"] in ids, "user could not see their own tenant's lead"
    assert fx["lead_b"] not in ids, "LEAK: portal user saw another tenant's lead"


@pytest.mark.asyncio
async def test_unknown_user_has_no_memberships(pool, portal_fixture) -> None:
    """A JWT for a user with no user_permissions rows resolves to zero tenants
    (the dependency turns this into a 403)."""
    memberships = await _load_memberships(str(uuid4()))
    assert memberships == {}
