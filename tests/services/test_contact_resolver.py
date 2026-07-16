"""Contact source-of-truth resolver (Slice 2.5).

Covers auto/crm/traceflow mode resolution, cache-first behavior in crm mode,
graceful degradation on a lookup miss, and the write-back guards (off by
default; manual-only when on). The DB and CRM are mocked so it runs offline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.models.client_config import ClientConfig
from app.models.contact import Contact, ContactType, ContactTypeSource
from app.models.crm_contact import ContactType as CRMType
from app.models.crm_contact import CRMContact
from app.services import contacts
from app.services.contacts import (
    maybe_write_back_contact_type,
    resolve_contact_type,
    resolve_mode,
)

PHONE = "+15551112222"


def _config(**overrides: Any) -> ClientConfig:
    base = {
        "client_id": uuid4(),
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ClientConfig(**base)


def _crm_config(**overrides: Any) -> ClientConfig:
    """A config whose CRM is actually usable (registered adapter + credentials)."""
    return _config(crm_provider="ghl", crm_credentials={"token": "x"}, **overrides)


def _contact(
    contact_type: ContactType = ContactType.unknown,
    source: ContactTypeSource = ContactTypeSource.inferred,
    type_at: datetime | None = None,
) -> Contact:
    now = datetime.now(UTC)
    return Contact(
        id=uuid4(),
        client_id=uuid4(),
        phone=PHONE,
        contact_type=contact_type,
        contact_type_source=source,
        contact_type_at=type_at,
        first_seen_at=now,
        last_seen_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# resolve_mode
# ---------------------------------------------------------------------------


def test_auto_resolves_to_crm_when_crm_usable() -> None:
    assert resolve_mode(_crm_config()) == "crm"


def test_auto_resolves_to_traceflow_without_crm() -> None:
    assert resolve_mode(_config()) == "traceflow"


def test_auto_resolves_to_traceflow_when_adapter_missing() -> None:
    # ServiceTitan/Salesforce have no registered adapter → traceflow, no special case.
    assert resolve_mode(_config(crm_provider="salesforce", crm_credentials={"k": 1})) == "traceflow"


def test_auto_resolves_to_traceflow_without_credentials() -> None:
    assert resolve_mode(_config(crm_provider="ghl")) == "traceflow"


def test_explicit_traceflow_stays_traceflow_even_with_crm() -> None:
    cfg = _crm_config(contact_config={"source_of_truth": "traceflow"})
    assert resolve_mode(cfg) == "traceflow"


def test_explicit_crm_degrades_to_traceflow_without_adapter() -> None:
    cfg = _config(
        crm_provider="salesforce", crm_credentials={"k": 1},
        contact_config={"source_of_truth": "crm"},
    )
    assert resolve_mode(cfg) == "traceflow"


# ---------------------------------------------------------------------------
# resolve_contact_type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_traceflow_mode_reads_local_no_network() -> None:
    contact = _contact(ContactType.customer, ContactTypeSource.manual)
    lookup = AsyncMock()
    with patch.object(contacts, "_safe_crm_lookup", new=lookup):
        res = await resolve_contact_type(AsyncMock(), _config(), contact, PHONE)
    lookup.assert_not_called()
    assert res.mode == "traceflow"
    assert res.contact_type == ContactType.customer


@pytest.mark.asyncio
async def test_crm_mode_cache_hit_avoids_network() -> None:
    contact = _contact(ContactType.customer, ContactTypeSource.crm, type_at=datetime.now(UTC))
    lookup = AsyncMock()
    with patch.object(contacts, "_safe_crm_lookup", new=lookup):
        res = await resolve_contact_type(AsyncMock(), _crm_config(), contact, PHONE)
    lookup.assert_not_called()
    assert res.from_cache is True
    assert res.contact_type == ContactType.customer


@pytest.mark.asyncio
async def test_crm_mode_cache_expiry_triggers_lookup() -> None:
    contact = _contact(
        ContactType.customer, ContactTypeSource.crm, type_at=datetime.now(UTC) - timedelta(days=60)
    )
    crm = CRMContact(external_id="c1", contact_type=CRMType.customer)
    with (
        patch.object(contacts, "_safe_crm_lookup", new=AsyncMock(return_value=crm)) as lookup,
        patch.object(contacts, "set_contact_type", new=AsyncMock(return_value=True)) as set_type,
    ):
        res = await resolve_contact_type(AsyncMock(), _crm_config(), contact, PHONE)
    lookup.assert_awaited_once()
    set_type.assert_awaited_once()  # persisted with source='crm'
    assert res.contact_type == ContactType.customer
    assert res.from_cache is False


@pytest.mark.asyncio
async def test_crm_lookup_maps_lead_to_prospect() -> None:
    contact = _contact()
    crm = CRMContact(external_id="l1", contact_type=CRMType.lead)
    with (
        patch.object(contacts, "_safe_crm_lookup", new=AsyncMock(return_value=crm)),
        patch.object(contacts, "set_contact_type", new=AsyncMock(return_value=True)),
    ):
        res = await resolve_contact_type(AsyncMock(), _crm_config(), contact, PHONE)
    assert res.contact_type == ContactType.prospect


@pytest.mark.asyncio
async def test_crm_lookup_failure_degrades_to_local_row() -> None:
    contact = _contact(ContactType.prospect, ContactTypeSource.inferred)
    with (
        patch.object(contacts, "_safe_crm_lookup", new=AsyncMock(return_value=None)),
        patch.object(contacts, "set_contact_type", new=AsyncMock()) as set_type,
    ):
        res = await resolve_contact_type(AsyncMock(), _crm_config(), contact, PHONE)
    assert res.contact_type == ContactType.prospect  # fell through to the local row
    set_type.assert_not_called()


@pytest.mark.asyncio
async def test_crm_mode_lookup_disabled_skips_network() -> None:
    contact = _contact(ContactType.unknown)
    cfg = _crm_config(classification_config={"crm_lookup_enabled": False})
    lookup = AsyncMock()
    with patch.object(contacts, "_safe_crm_lookup", new=lookup):
        res = await resolve_contact_type(AsyncMock(), cfg, contact, PHONE)
    lookup.assert_not_called()
    assert res.contact_type == ContactType.unknown


# ---------------------------------------------------------------------------
# Write-back — off by default, manual-only when on
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_back_never_attempted_when_flag_off() -> None:
    contact = _contact(ContactType.customer, ContactTypeSource.manual)
    with patch.object(contacts, "_crm_write_back", new=AsyncMock(return_value=True)) as wb:
        attempted = await maybe_write_back_contact_type(AsyncMock(), _crm_config(), contact)
    assert attempted is False
    wb.assert_not_called()


@pytest.mark.asyncio
async def test_write_back_skips_inferred_type_when_flag_on() -> None:
    contact = _contact(ContactType.customer, ContactTypeSource.inferred)
    cfg = _crm_config(contact_config={"crm_write_back_contact_type": True})
    with patch.object(contacts, "_crm_write_back", new=AsyncMock(return_value=True)) as wb:
        attempted = await maybe_write_back_contact_type(AsyncMock(), cfg, contact)
    assert attempted is False
    wb.assert_not_called()  # only manual classifications are eligible


@pytest.mark.asyncio
async def test_write_back_attempted_for_manual_type_when_flag_on() -> None:
    contact = _contact(ContactType.vendor, ContactTypeSource.manual)
    cfg = _crm_config(contact_config={"crm_write_back_contact_type": True})
    with patch.object(contacts, "_crm_write_back", new=AsyncMock(return_value=True)) as wb:
        attempted = await maybe_write_back_contact_type(AsyncMock(), cfg, contact)
    assert attempted is True
    wb.assert_awaited_once()
