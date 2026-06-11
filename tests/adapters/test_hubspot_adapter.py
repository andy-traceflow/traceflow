"""HubSpot adapter tests.

These exercise the pure helpers (canonical → properties translation,
default-property fallback, transform application, creds validation, phone
match/classify) and the push/update/lookup call shapes. The HTTP layer is
mocked so the suite runs offline and deterministically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.adapters.hubspot import HubSpotAdapter
from app.models.client_config import ClientConfig
from app.models.crm_contact import ContactType
from app.models.lead import Lead, QualificationStatus
from app.services.field_mappings import FieldMapping


def _make_lead(**overrides: Any) -> Lead:
    base = {
        "id": uuid4(),
        "client_id": uuid4(),
        "external_id": None,
        "source_system": "twilio_missed_call",
        "contact_name": "Jane Doe",
        "contact_company": "Doe Co",
        "phone": "+15551234567",
        "email": "jane@example.com",
        "service_type": "consult",
        "sqft": 250.0,
        "raw_payload": {},
        "qualification_status": QualificationStatus.qualified,
        "notes": "",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return Lead(**base)


def _make_config(client_id, **overrides: Any) -> ClientConfig:
    base = {
        "client_id": client_id,
        "crm_provider": "hubspot",
        "crm_credentials": {"access_token": "fake-token"},
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ClientConfig(**base)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def test_creds_validation_fails_without_access_token():
    adapter = HubSpotAdapter()
    config = ClientConfig(
        client_id=uuid4(),
        crm_credentials={"api_key": "wrong-shape"},
        ai_period_resets_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    with pytest.raises(ValueError, match="access_token"):
        adapter._creds(config)


# ---------------------------------------------------------------------------
# Canonical → properties
# ---------------------------------------------------------------------------

def test_build_properties_explicit_mapping_and_custom_property():
    adapter = HubSpotAdapter()
    canonical = {
        "contact_name": "Jane Doe",
        "phone": "+15551234567",
        "service_type": "consult",
        "sqft": None,  # no value → skipped even though it has a mapping
    }
    mappings = {
        "contact_name": FieldMapping(
            canonical_field="contact_name",
            external_field="firstname",
            external_field_type="standard",
            transform=None,
        ),
        "service_type": FieldMapping(
            canonical_field="service_type",
            external_field="service_category",  # a custom property on the portal
            external_field_type="custom_property",
            transform={"type": "value_map", "mapping": {"consult": "Consultation"}},
        ),
        "sqft": FieldMapping(
            canonical_field="sqft",
            external_field="project_sqft",
            external_field_type="custom_property",
            transform=None,
        ),
    }

    props = adapter._build_properties(canonical, mappings)
    assert props["firstname"] == "Jane Doe"                 # explicit standard mapping
    assert props["service_category"] == "Consultation"      # custom property, transformed
    assert props["phone"] == "+15551234567"                 # unmapped → default standard property
    assert "project_sqft" not in props                      # None value not sent


def test_build_properties_default_map_for_zero_config_tenant():
    """A tenant with no field mappings still pushes the HubSpot standard props."""
    adapter = HubSpotAdapter()
    canonical = {
        "contact_name": "John Smith",
        "contact_company": "Smith LLC",
        "phone": "+15559998888",
        "email": "john@example.com",
        "service_type": "resurfacing",  # no standard property → not sent without a mapping
        "notes": "",                    # empty → skipped
    }
    props = adapter._build_properties(canonical, {})
    assert props == {
        "firstname": "John Smith",
        "company": "Smith LLC",
        "phone": "+15559998888",
        "email": "john@example.com",
    }


def test_canonical_dict_excludes_external_id():
    """external_id is HubSpot's, not a property we push."""
    lead = _make_lead()
    canonical = HubSpotAdapter._canonical_dict(lead)
    assert "external_id" not in canonical
    expected = {
        "contact_name", "contact_company", "phone", "email", "address",
        "service_type", "sqft", "budget_range", "timeframe", "notes",
    }
    assert expected == set(canonical.keys())


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_check_false_without_creds():
    adapter = HubSpotAdapter()
    config = ClientConfig(
        client_id=uuid4(),
        crm_credentials={},
        ai_period_resets_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert await adapter.health_check(config) is False


@pytest.mark.asyncio
async def test_health_check_false_on_request_error():
    adapter = HubSpotAdapter()
    adapter._request = AsyncMock(side_effect=Exception("network"))  # type: ignore[method-assign]
    assert await adapter.health_check(_make_config(uuid4())) is False


# ---------------------------------------------------------------------------
# push_lead / update_lead
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_push_lead_returns_contact_id():
    adapter = HubSpotAdapter()
    client_id = uuid4()
    lead = _make_lead(client_id=client_id)
    config = _make_config(client_id)

    captured: dict[str, Any] = {}

    async def fake_request(*, token, method, path, json_body=None, params=None, request_timeout=30.0):
        captured.update(method=method, path=path, body=json_body)
        return {"id": "hs-contact-789", "properties": {}}

    adapter._request = fake_request  # type: ignore[assignment]
    with patch("app.adapters.hubspot.resolve_mappings", new=AsyncMock(return_value={})):
        contact_id = await adapter.push_lead(lead, config)

    assert contact_id == "hs-contact-789"
    assert captured["method"] == "POST"
    assert captured["path"] == "/crm/v3/objects/contacts"
    # Default-mapped standard properties are wrapped under "properties".
    assert captured["body"]["properties"]["phone"] == "+15551234567"
    assert captured["body"]["properties"]["firstname"] == "Jane Doe"


@pytest.mark.asyncio
async def test_push_lead_raises_on_api_failure():
    adapter = HubSpotAdapter()
    config = _make_config(uuid4())
    lead = _make_lead(client_id=config.client_id)
    adapter._request = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with patch("app.adapters.hubspot.resolve_mappings", new=AsyncMock(return_value={})):
        with pytest.raises(RuntimeError, match="create contact failed"):
            await adapter.push_lead(lead, config)


@pytest.mark.asyncio
async def test_update_lead_sends_patch_with_default_property():
    adapter = HubSpotAdapter()
    config = _make_config(uuid4())

    captured: dict[str, Any] = {}

    async def fake_request(*, token, method, path, json_body=None, params=None, request_timeout=30.0):
        captured.update(method=method, path=path, body=json_body)
        return {}

    adapter._request = fake_request  # type: ignore[assignment]
    with patch("app.adapters.hubspot.resolve_mappings", new=AsyncMock(return_value={})):
        await adapter.update_lead("contact-42", {"phone": "+15559999999"}, config)

    assert captured["method"] == "PATCH"
    assert captured["path"] == "/crm/v3/objects/contacts/contact-42"
    assert captured["body"] == {"properties": {"phone": "+15559999999"}}


@pytest.mark.asyncio
async def test_update_lead_noop_when_nothing_maps():
    """A canonical field with no mapping and no standard property is skipped."""
    adapter = HubSpotAdapter()
    config = _make_config(uuid4())
    adapter._request = AsyncMock()  # type: ignore[method-assign]
    with patch("app.adapters.hubspot.resolve_mappings", new=AsyncMock(return_value={})):
        await adapter.update_lead("contact-42", {"service_type": "resurfacing"}, config)
    adapter._request.assert_not_called()


# ---------------------------------------------------------------------------
# lookup_by_phone — pre-send CRM classification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_by_phone_none_without_creds():
    adapter = HubSpotAdapter()
    config = ClientConfig(
        client_id=uuid4(),
        crm_credentials={},
        ai_period_resets_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert await adapter.lookup_by_phone("+15551234567", config) is None


@pytest.mark.asyncio
async def test_lookup_by_phone_none_on_empty_result():
    adapter = HubSpotAdapter()
    adapter._request = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    assert await adapter.lookup_by_phone("+15551234567", _make_config(uuid4())) is None


@pytest.mark.asyncio
async def test_lookup_by_phone_maps_customer():
    adapter = HubSpotAdapter()
    adapter._request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "results": [
                {
                    "id": "c1",
                    "properties": {
                        "phone": "+15551234567",
                        "firstname": "Repeat",
                        "lastname": "Client",
                        "lifecyclestage": "customer",
                    },
                }
            ]
        }
    )
    contact = await adapter.lookup_by_phone("+15551234567", _make_config(uuid4()))
    assert contact is not None
    assert contact.external_id == "c1"
    assert contact.name == "Repeat Client"
    assert contact.contact_type == ContactType.customer


@pytest.mark.asyncio
async def test_lookup_by_phone_classifies_vendor_hint():
    """A vendor/supplier hint in lifecyclestage/type wins over customer."""
    adapter = HubSpotAdapter()
    adapter._request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "results": [
                {
                    "id": "v1",
                    "properties": {
                        "phone": "+15551234567",
                        "lifecyclestage": "customer",
                        "type": "Preferred Supplier",
                    },
                }
            ]
        }
    )
    contact = await adapter.lookup_by_phone("+15551234567", _make_config(uuid4()))
    assert contact is not None
    assert contact.contact_type == ContactType.vendor


@pytest.mark.asyncio
async def test_lookup_by_phone_rejects_fuzzy_nonmatch():
    """HubSpot's broad search matches name/email too; a different phone is
    rejected so a real lead is never routed to the wrong disposition."""
    adapter = HubSpotAdapter()
    adapter._request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "results": [
                {"id": "x1", "properties": {"phone": "+19998887777", "lifecyclestage": "customer"}}
            ]
        }
    )
    assert await adapter.lookup_by_phone("+15551234567", _make_config(uuid4())) is None


# ---------------------------------------------------------------------------
# fetch_recovered_value — confirmed recovered-revenue readback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_recovered_value_parses_total_revenue():
    adapter = HubSpotAdapter()
    adapter._request = AsyncMock(  # type: ignore[method-assign]
        return_value={"id": "c1", "properties": {"total_revenue": "4500.00"}}
    )
    value = await adapter.fetch_recovered_value("c1", _make_config(uuid4()))
    assert value == Decimal("4500.00")


@pytest.mark.asyncio
async def test_fetch_recovered_value_none_when_zero_or_missing():
    adapter = HubSpotAdapter()
    adapter._request = AsyncMock(return_value={"id": "c1", "properties": {"total_revenue": "0"}})  # type: ignore[method-assign]
    assert await adapter.fetch_recovered_value("c1", _make_config(uuid4())) is None

    adapter._request = AsyncMock(return_value={"id": "c1", "properties": {}})  # type: ignore[method-assign]
    assert await adapter.fetch_recovered_value("c1", _make_config(uuid4())) is None


@pytest.mark.asyncio
async def test_fetch_recovered_value_none_without_creds():
    adapter = HubSpotAdapter()
    config = ClientConfig(
        client_id=uuid4(),
        crm_credentials={},
        ai_period_resets_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert await adapter.fetch_recovered_value("c1", config) is None


@pytest.mark.asyncio
async def test_fetch_recovered_value_none_on_request_failure():
    adapter = HubSpotAdapter()
    adapter._request = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert await adapter.fetch_recovered_value("c1", _make_config(uuid4())) is None


def test_parse_money_rejects_garbage():
    assert HubSpotAdapter._parse_money("not-a-number") is None
    assert HubSpotAdapter._parse_money(None) is None
    assert HubSpotAdapter._parse_money("") is None
    assert HubSpotAdapter._parse_money("1200.50") == Decimal("1200.50")
