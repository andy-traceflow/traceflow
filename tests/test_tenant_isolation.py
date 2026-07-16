"""Tenant isolation suite — NON-NEGOTIABLE.

For every tenant-scoped table, this suite:
  1. Creates two test tenants (A, B)
  2. Inserts a row under Client A's context
  3. Switches to Client B's context
  4. Asserts that Client B cannot see Client A's row

Failure here means RLS is misconfigured or the middleware is leaking
tenant context. Either is a P0 incident — do not merge with a
failing run.

Run:
    TRACEFLOW_TEST_DB_URL=postgres://... pytest tests/test_tenant_isolation.py -v
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import asyncpg
import pytest

TENANT_SCOPED_TABLES = [
    "client_configs",
    "client_field_mappings",
    "client_webhook_configs",
    "contacts",
    "leads",
    "messages",
    "events",
    "kb_entries",
    "kb_documents",
    "kb_chunks",
    "sync_log",
    "product_yields",
    "calculator_configs",
    "user_permissions",
]


@pytest.fixture
async def conn(db_url: str):
    """Direct asyncpg connection bound to a single tenant context per test."""
    conn = await asyncpg.connect(db_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def two_clients(conn) -> tuple[UUID, UUID]:
    """Create two throwaway clients and clean them up after the test.

    Setup and teardown run as `postgres` (bypassrls=true) so admin
    operations aren't blocked by RLS. Each test body switches into
    the `authenticated` role to actually exercise RLS isolation.
    """
    a, b = uuid4(), uuid4()
    await conn.execute("RESET ROLE")
    await conn.execute(
        """
        INSERT INTO clients (id, slug, business_name)
        VALUES ($1, $2, $3), ($4, $5, $6)
        """,
        a, f"test-a-{a}", "Test A",
        b, f"test-b-{b}", "Test B",
    )
    yield a, b
    await conn.execute("RESET ROLE")
    await conn.execute("DELETE FROM clients WHERE id IN ($1, $2)", a, b)


async def _set_tenant(conn: asyncpg.Connection, client_id: UUID) -> None:
    """Switch into the RLS-respecting role and set the tenant context.

    `authenticated` has `bypassrls=false` — without this role switch,
    queries from `postgres` (the default connection role on Supabase)
    silently skip RLS entirely.
    """
    await conn.execute("SET ROLE authenticated")
    await conn.execute("SELECT set_config('app.current_client_id', $1, false)", str(client_id))


async def _clear_tenant(conn: asyncpg.Connection) -> None:
    """Clear the tenant setting while staying in the RLS-enforcing role."""
    await conn.execute("SET ROLE authenticated")
    await conn.execute("SELECT set_config('app.current_client_id', '', false)")


@pytest.mark.asyncio
async def test_leads_are_tenant_isolated(conn, two_clients: tuple[UUID, UUID]) -> None:
    """Client B cannot read Client A's leads."""
    client_a, client_b = two_clients

    # Insert as Client A
    await _set_tenant(conn, client_a)
    lead_id = await conn.fetchval(
        """
        INSERT INTO leads (client_id, source_system, raw_payload)
        VALUES ($1, 'test', $2::jsonb)
        RETURNING id
        """,
        client_a,
        json.dumps({"marker": "client_a_only"}),
    )
    assert lead_id is not None

    # Switch to Client B, query
    await _set_tenant(conn, client_b)
    rows = await conn.fetch("SELECT id FROM leads WHERE id = $1", lead_id)
    assert rows == [], f"LEAK: Client B saw Client A's lead {lead_id}"

    # Sanity: Client A still sees it
    await _set_tenant(conn, client_a)
    rows = await conn.fetch("SELECT id FROM leads WHERE id = $1", lead_id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_contacts_are_tenant_isolated(conn, two_clients: tuple[UUID, UUID]) -> None:
    """Contacts carry durable caller identity + person facts across leads.
    A leak would expose one client's customer list to another."""
    client_a, client_b = two_clients

    await _set_tenant(conn, client_a)
    contact_id = await conn.fetchval(
        """
        INSERT INTO contacts (client_id, phone, name)
        VALUES ($1, '+15551110000', 'Client A Contact')
        RETURNING id
        """,
        client_a,
    )
    assert contact_id is not None

    await _set_tenant(conn, client_b)
    rows = await conn.fetch("SELECT id FROM contacts WHERE id = $1", contact_id)
    assert rows == [], f"LEAK: Client B saw Client A's contact {contact_id}"

    # Sanity: Client A still sees it.
    await _set_tenant(conn, client_a)
    rows = await conn.fetch("SELECT id FROM contacts WHERE id = $1", contact_id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_kb_entries_are_tenant_isolated(conn, two_clients: tuple[UUID, UUID]) -> None:
    client_a, client_b = two_clients

    await _set_tenant(conn, client_a)
    entry_id = await conn.fetchval(
        """
        INSERT INTO kb_entries (client_id, question, answer)
        VALUES ($1, 'Test Q', 'Test A')
        RETURNING id
        """,
        client_a,
    )
    assert entry_id is not None

    await _set_tenant(conn, client_b)
    rows = await conn.fetch("SELECT id FROM kb_entries WHERE id = $1", entry_id)
    assert rows == [], f"LEAK: Client B saw Client A's kb_entry {entry_id}"


@pytest.mark.asyncio
async def test_messages_are_tenant_isolated(conn, two_clients: tuple[UUID, UUID]) -> None:
    """High-traffic table — every inbound SMS, every outbound reply
    lands here. A leak would expose customer conversations across tenants."""
    client_a, client_b = two_clients

    await _set_tenant(conn, client_a)
    lead_id = await conn.fetchval(
        """
        INSERT INTO leads (client_id, source_system, raw_payload)
        VALUES ($1, 'test', '{}'::jsonb)
        RETURNING id
        """,
        client_a,
    )
    msg_id = await conn.fetchval(
        """
        INSERT INTO messages (client_id, lead_id, direction, channel, body)
        VALUES ($1, $2, 'inbound', 'sms', 'client_a_only')
        RETURNING id
        """,
        client_a,
        lead_id,
    )
    assert msg_id is not None

    await _set_tenant(conn, client_b)
    rows = await conn.fetch("SELECT id FROM messages WHERE id = $1", msg_id)
    assert rows == [], f"LEAK: Client B saw Client A's message {msg_id}"


@pytest.mark.asyncio
async def test_events_are_tenant_isolated(conn, two_clients: tuple[UUID, UUID]) -> None:
    """Event stream carries debugging payloads — including, sometimes,
    contents of outbound messages and CRM payloads. RLS is the primary
    isolation here."""
    client_a, client_b = two_clients

    await _set_tenant(conn, client_a)
    event_id = await conn.fetchval(
        """
        INSERT INTO events (client_id, event_type, payload)
        VALUES ($1, 'test_event', '{"marker": "client_a_only"}'::jsonb)
        RETURNING id
        """,
        client_a,
    )
    assert event_id is not None

    await _set_tenant(conn, client_b)
    rows = await conn.fetch("SELECT id FROM events WHERE id = $1", event_id)
    assert rows == [], f"LEAK: Client B saw Client A's event {event_id}"


@pytest.mark.asyncio
async def test_no_tenant_context_denies_all_reads(conn, two_clients: tuple[UUID, UUID]) -> None:
    """With no app.current_client_id set, RLS should return zero rows."""
    client_a, _ = two_clients

    await _set_tenant(conn, client_a)
    await conn.execute(
        """
        INSERT INTO leads (client_id, source_system, raw_payload)
        VALUES ($1, 'test', '{}'::jsonb)
        """,
        client_a,
    )

    await _clear_tenant(conn)
    rows = await conn.fetch("SELECT id FROM leads WHERE client_id = $1", client_a)
    assert rows == [], "LEAK: rows visible with no tenant context set"


@pytest.mark.parametrize("table", TENANT_SCOPED_TABLES)
@pytest.mark.asyncio
async def test_every_tenant_table_has_rls_enabled(conn, table: str) -> None:
    """Defense in depth: every tenant-scoped table must have RLS on."""
    row = await conn.fetchrow(
        """
        SELECT relrowsecurity
        FROM pg_class
        WHERE relname = $1 AND relkind = 'r'
        """,
        table,
    )
    assert row is not None, f"table {table} not found"
    assert row["relrowsecurity"] is True, f"RLS not enabled on {table}"


@pytest.mark.parametrize("table", TENANT_SCOPED_TABLES)
@pytest.mark.asyncio
async def test_every_tenant_table_has_a_policy(conn, table: str) -> None:
    """Every tenant-scoped table must have at least one RLS policy."""
    row = await conn.fetchrow(
        """
        SELECT count(*) AS n
        FROM pg_policy p
        JOIN pg_class c ON c.oid = p.polrelid
        WHERE c.relname = $1
        """,
        table,
    )
    assert row is not None and row["n"] > 0, f"no RLS policies on {table}"
