"""Contact identity service — get-or-create, precedence, history, facts.

The DB is mocked so the suite runs offline. The focus is the pieces that carry
real logic: the get-or-create upsert shape, the contact-type precedence matrix
(manual > crm > inferred, blocked manual-only), and the person-fact contract.
Cross-tenant isolation is proved separately, against a live DB, in
tests/test_tenant_isolation.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.contact import (
    Contact,
    ContactType,
    ContactTypeSource,
    from_crm_contact_type,
)
from app.models.crm_contact import ContactType as CRMContactType
from app.services import contacts
from app.services.contacts import (
    _decide_type_write,
    merge_known_facts,
    resolve_contact,
    set_contact_type,
)


def _contact_row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid4(),
        "client_id": uuid4(),
        "phone": "+17025178074",
        "name": None,
        "contact_type": "unknown",
        "contact_type_source": "inferred",
        "contact_type_at": None,
        "contact_type_reason": None,
        "crm_external_id": None,
        "known_facts": {},
        "summary": None,
        "last_intent": None,
        "call_count": 0,
        "lead_count": 0,
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# resolve_contact — get-or-create, normalized, idempotent by construction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_contact_returns_contact() -> None:
    row = _contact_row()
    conn = AsyncMock()
    conn.fetchrow.return_value = row
    contact = await resolve_contact(conn, row["client_id"], "+17025178074")
    assert isinstance(contact, Contact)
    assert contact.id == row["id"]


@pytest.mark.asyncio
async def test_resolve_contact_normalizes_phone_before_write() -> None:
    row = _contact_row()
    conn = AsyncMock()
    conn.fetchrow.return_value = row
    await resolve_contact(conn, row["client_id"], "(702) 517-8074")
    # Second positional arg to the upsert is the E.164-normalized phone.
    _, client_arg, phone_arg = conn.fetchrow.call_args.args
    assert phone_arg == "+17025178074"


@pytest.mark.asyncio
async def test_resolve_contact_uses_upsert() -> None:
    """Idempotency under concurrent webhooks rides on ON CONFLICT DO UPDATE."""
    row = _contact_row()
    conn = AsyncMock()
    conn.fetchrow.return_value = row
    await resolve_contact(conn, row["client_id"], "+17025178074")
    sql = conn.fetchrow.call_args.args[0]
    assert "ON CONFLICT (client_id, phone)" in sql
    assert "DO UPDATE SET last_seen_at" in sql


@pytest.mark.asyncio
async def test_resolve_contact_empty_phone_raises() -> None:
    conn = AsyncMock()
    with pytest.raises(ValueError):
        await resolve_contact(conn, uuid4(), "   ")
    conn.fetchrow.assert_not_called()


# ---------------------------------------------------------------------------
# Precedence — pure decision matrix (manual > crm > inferred)
# ---------------------------------------------------------------------------

_T = ContactType
_S = ContactTypeSource


@pytest.mark.parametrize(
    ("current_source", "new_source", "expected_write"),
    [
        (_S.inferred, _S.inferred, True),
        (_S.inferred, _S.crm, True),
        (_S.inferred, _S.manual, True),
        (_S.crm, _S.inferred, False),  # inferred cannot clobber crm
        (_S.crm, _S.crm, True),
        (_S.crm, _S.manual, True),
        (_S.manual, _S.inferred, False),  # inferred cannot clobber manual
        (_S.manual, _S.crm, False),  # crm cannot clobber manual
        (_S.manual, _S.manual, True),
    ],
)
def test_precedence_matrix(
    current_source: ContactTypeSource,
    new_source: ContactTypeSource,
    expected_write: bool,
) -> None:
    decision = _decide_type_write(_T.prospect, current_source, new_source, _T.customer)
    assert decision.write is expected_write


def test_blocked_never_overwritten_except_by_manual() -> None:
    assert _decide_type_write(_T.blocked, _S.manual, _S.crm, _T.customer).write is False
    assert _decide_type_write(_T.blocked, _S.manual, _S.inferred, _T.spam).write is False
    assert _decide_type_write(_T.blocked, _S.manual, _S.manual, _T.customer).write is True


def test_same_type_is_not_a_change() -> None:
    decision = _decide_type_write(_T.customer, _S.crm, _S.crm, _T.customer)
    assert decision.write is True
    assert decision.is_change is False


# ---------------------------------------------------------------------------
# set_contact_type — the single writer, wired to the decision
# ---------------------------------------------------------------------------


def _conn_with_current(contact_type: str, source: str) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "client_id": uuid4(),
        "contact_type": contact_type,
        "contact_type_source": source,
    }
    return conn


@pytest.mark.asyncio
async def test_set_contact_type_blocked_requires_manual() -> None:
    conn = AsyncMock()
    with pytest.raises(ValueError):
        await set_contact_type(conn, uuid4(), ContactType.blocked, ContactTypeSource.crm)
    with pytest.raises(ValueError):
        await set_contact_type(conn, uuid4(), ContactType.blocked, ContactTypeSource.inferred)
    conn.fetchrow.assert_not_called()  # rejected before touching the DB


@pytest.mark.asyncio
async def test_inferred_cannot_clobber_manual() -> None:
    conn = _conn_with_current("customer", "manual")
    applied = await set_contact_type(
        conn, uuid4(), ContactType.spam, ContactTypeSource.inferred, "spam score"
    )
    assert applied is False
    conn.execute.assert_not_called()  # no UPDATE, no event


@pytest.mark.asyncio
async def test_crm_cannot_clobber_manual() -> None:
    conn = _conn_with_current("customer", "manual")
    applied = await set_contact_type(conn, uuid4(), ContactType.vendor, ContactTypeSource.crm)
    assert applied is False
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_type_change_writes_update_and_event() -> None:
    conn = _conn_with_current("unknown", "inferred")
    applied = await set_contact_type(
        conn, uuid4(), ContactType.prospect, ContactTypeSource.inferred, "engaged"
    )
    assert applied is True
    # One UPDATE + one events insert.
    assert conn.execute.await_count == 2
    event_sql = conn.execute.await_args_list[1].args[0]
    assert "contact_type_changed" in event_sql


@pytest.mark.asyncio
async def test_reconfirm_same_type_updates_without_event() -> None:
    """A crm re-confirmation of the same type refreshes the cache TTL
    (contact_type_at) but emits no change event."""
    conn = _conn_with_current("customer", "crm")
    applied = await set_contact_type(conn, uuid4(), ContactType.customer, ContactTypeSource.crm)
    assert applied is True
    assert conn.execute.await_count == 1  # UPDATE only, no event


@pytest.mark.asyncio
async def test_set_contact_type_missing_contact_returns_false() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    applied = await set_contact_type(conn, uuid4(), ContactType.customer, ContactTypeSource.crm)
    assert applied is False
    conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# merge_known_facts — person-scoped keys only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_known_facts_filters_to_person_keys() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = {"known_facts": {"contact_name": "Maria", "zip": "89101"}}
    await merge_known_facts(
        conn,
        uuid4(),
        {
            "contact_name": "Maria",
            "zip": "89101",
            "sqft": 42,  # project-scoped — must be dropped
            "material": "quartz",  # project-scoped — must be dropped
            "address": "",  # empty — must be dropped
        },
    )
    written = conn.fetchrow.call_args.args[2]
    assert written == {"contact_name": "Maria", "zip": "89101"}


@pytest.mark.asyncio
async def test_merge_known_facts_no_person_keys_is_readonly() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = {"known_facts": {"contact_name": "Existing"}}
    result = await merge_known_facts(conn, uuid4(), {"sqft": 42, "material": "granite"})
    assert result == {"contact_name": "Existing"}
    # Read-only path: the SELECT, never an UPDATE.
    assert "UPDATE" not in conn.fetchrow.call_args.args[0]


# ---------------------------------------------------------------------------
# CRM contact-type mapping
# ---------------------------------------------------------------------------


def test_crm_type_mapping() -> None:
    assert from_crm_contact_type(CRMContactType.customer) == ContactType.customer
    assert from_crm_contact_type(CRMContactType.vendor) == ContactType.vendor
    assert from_crm_contact_type(CRMContactType.lead) == ContactType.prospect
    assert from_crm_contact_type(CRMContactType.unknown) == ContactType.unknown


def test_contacts_module_exports_public_api() -> None:
    # Guards against an accidental rename breaking Slice 2/2.5 callers.
    for name in ("resolve_contact", "set_contact_type", "contact_history", "merge_known_facts"):
        assert hasattr(contacts, name)


# ---------------------------------------------------------------------------
# merge_person_facts — schema-driven person scope (Slice 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_person_facts_filters_to_schema_person_keys() -> None:
    from app.models.qualification import default_schema
    from app.services.contacts import merge_person_facts

    conn = AsyncMock()
    conn.fetchrow.return_value = {"known_facts": {}}
    # property_type is person-scoped in the schema but NOT in the fixed
    # PERSON_FACT_KEYS — the schema is the authority, so it must be written.
    await merge_person_facts(
        conn,
        uuid4(),
        {"contact_name": "Maria", "property_type": "commercial",
         "material": "quartz", "scope_size": 40},  # project fields dropped
        default_schema(),
    )
    written = conn.fetchrow.call_args.args[2]
    assert written == {"contact_name": "Maria", "property_type": "commercial"}
