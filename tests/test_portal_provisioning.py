"""Portal account provisioning — Supabase user + tenant-admin permission row.

Pure test: the Supabase call fails closed when unconfigured. DB-backed test
(skips without TRACEFLOW_TEST_DB_URL) mocks the Supabase call and verifies the
user_permissions grant + that the keystone then resolves the user to the tenant.

Run the full set:
    TRACEFLOW_TEST_DB_URL=postgres://... pytest tests/test_portal_provisioning.py -v
"""

from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest

from app.services import provisioning
from app.services.provisioning import (
    PortalUserError,
    ProvisionSpec,
    provision_client,
    provision_portal_user,
)


@pytest.mark.asyncio
async def test_create_supabase_user_requires_config() -> None:
    """No SUPABASE_URL/SERVICE_KEY → fail closed, never a silent no-op."""
    with pytest.raises(PortalUserError):
        await provisioning._create_supabase_user("x@example.com", "X", invite=False)


@pytest.fixture
async def raw_conn(db_url: str):
    conn = await asyncpg.connect(db_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def pool(db_url: str):
    from app import db

    await db.init_pool()
    try:
        yield
    finally:
        await db.close_pool()


@pytest.mark.asyncio
async def test_provision_portal_user_grants_tenant_admin(pool, raw_conn, monkeypatch) -> None:
    """With the Supabase call mocked, the user becomes tenant-admin and the
    portal keystone resolves them to exactly that client."""
    from app.middleware.portal_auth import _load_memberships

    # Stand up a real client to attach the user to.
    spec = ProvisionSpec(slug=f"pp-{uuid4().hex[:10]}", business_name="Portal Prov Co")
    client_id = await provision_client(spec)

    # Simulate Supabase creating the auth user: pre-insert the row the mock
    # will "return", so user_permissions' FK to auth.users is satisfied.
    fake_user_id = uuid4()
    await raw_conn.execute("RESET ROLE")
    await raw_conn.execute(
        "INSERT INTO auth.users (id, email) VALUES ($1, $2)",
        fake_user_id, "owner@portalprov.example",
    )

    async def _fake_create(email, name, *, invite):  # noqa: ANN001 - test stub
        return fake_user_id

    monkeypatch.setattr(provisioning, "_create_supabase_user", _fake_create)

    try:
        returned = await provision_portal_user(
            client_id, "owner@portalprov.example", "Pat Owner", invite=False
        )
        assert returned == fake_user_id

        perms = await raw_conn.fetchrow(
            "SELECT is_admin, can_edit_config FROM user_permissions "
            "WHERE client_id = $1 AND user_id = $2",
            client_id, fake_user_id,
        )
        assert perms is not None
        assert perms["is_admin"] is True
        assert perms["can_edit_config"] is True

        # The keystone now resolves this user to the tenant.
        memberships = await _load_memberships(str(fake_user_id))
        assert client_id in memberships
        assert memberships[client_id].is_admin is True
    finally:
        await raw_conn.execute("RESET ROLE")
        await raw_conn.execute("DELETE FROM clients WHERE id = $1", client_id)
        await raw_conn.execute("DELETE FROM auth.users WHERE id = $1", fake_user_id)
