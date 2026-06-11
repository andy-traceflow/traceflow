"""GoHighLevel adapter tests.

These exercise the pure helpers (canonical → contact-body translation,
transform application, creds validation) and the push/update call shapes.
The HTTP layer is mocked so the suite runs offline and deterministically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.adapters.ghl import GoHighLevelAdapter
from app.models.client_config import ClientConfig
from app.models.crm_contact import ContactType
from app.models.lead import Lead, QualificationStatus
from app.services.field_mappings import FieldMapping


def _make_lead(**overrides: Any) -> Lead:
    base = {
        "id": uuid4(),
        "client_id": uuid4(),
        "external_id": "EXT-100",
        "source_system": "twilio_missed_call",
        "contact_name": "Jane Doe",
        "contact_company": "Doe Co",
        "phone": "+15551234567",
        "email": "jane@example.com",
        "service_type": "consult",
        "sqft": 250.0,
        "raw_payload": {},
        "qualification_status": QualificationStatus.unqualified,
        "notes": "",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return Lead(**base)


def _make_config(client_id, **overrides: Any) -> ClientConfig:
    base = {
        "client_id": client_id,
        "crm_provider": "ghl",
        "crm_credentials": {"api_key": "fake-key", "location_id": "loc-123"},
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ClientConfig(**base)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def test_creds_validation_fails_without_location_id():
    adapter = GoHighLevelAdapter()
    config = ClientConfig(
        client_id=uuid4(),
        crm_credentials={"api_key": "only-the-key"},
        ai_period_resets_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    with pytest.raises(ValueError, match="location_id"):
        adapter._creds(config)


# ---------------------------------------------------------------------------
# Canonical → contact body
# ---------------------------------------------------------------------------

def test_build_contact_body_standard_and_custom():
    adapter = GoHighLevelAdapter()
    canonical = {
        "contact_name": "Jane Doe",
        "phone": "+15551234567",
        "service_type": "consult",
        "sqft": None,  # no value → skipped even though mapped
    }
    mappings = {
        "contact_name": FieldMapping(
            canonical_field="contact_name",
            external_field="name",
            external_field_type="standard",
            transform=None,
        ),
        "phone": FieldMapping(
            canonical_field="phone",
            external_field="phone",
            external_field_type="standard",
            transform=None,
        ),
        "service_type": FieldMapping(
            canonical_field="service_type",
            external_field="cf_service_id",
            external_field_type="custom_field",
            transform={"type": "value_map", "mapping": {"consult": "Consultation"}},
        ),
        "sqft": FieldMapping(
            canonical_field="sqft",
            external_field="cf_sqft_id",
            external_field_type="custom_field",
            transform=None,
        ),
    }

    body = adapter._build_contact_body(canonical, mappings)
    assert body["name"] == "Jane Doe"                       # standard → top-level
    assert body["phone"] == "+15551234567"
    assert body["customFields"] == [                        # custom → array, transformed
        {"id": "cf_service_id", "field_value": "Consultation"}
    ]
    assert "sqft" not in body                               # None value not sent


def test_build_contact_body_empty_when_no_mappings():
    adapter = GoHighLevelAdapter()
    assert adapter._build_contact_body({"phone": "+1555"}, {}) == {}


def test_canonical_dict_includes_all_known_fields():
    lead = _make_lead()
    canonical = GoHighLevelAdapter._canonical_dict(lead)
    expected_keys = {
        "contact_name", "contact_company", "phone", "email", "address",
        "service_type", "sqft", "budget_range", "timeframe", "notes", "external_id",
    }
    assert expected_keys.issubset(canonical.keys())


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_check_false_without_creds():
    adapter = GoHighLevelAdapter()
    config = ClientConfig(
        client_id=uuid4(),
        crm_credentials={},
        ai_period_resets_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert await adapter.health_check(config) is False


@pytest.mark.asyncio
async def test_health_check_false_on_request_error():
    adapter = GoHighLevelAdapter()
    adapter._request = AsyncMock(side_effect=Exception("network"))  # type: ignore[method-assign]
    assert await adapter.health_check(_make_config(uuid4())) is False


# ---------------------------------------------------------------------------
# push_lead / update_lead
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_push_lead_returns_contact_id():
    """Mock the HTTP layer; verify the adapter assembles the right call shape."""
    adapter = GoHighLevelAdapter()
    client_id = uuid4()
    lead = _make_lead(client_id=client_id)
    config = _make_config(client_id)

    captured: dict[str, Any] = {}

    async def fake_request(*, api_key, method, path, json_body=None):
        captured.update(method=method, path=path, body=json_body)
        return {"contact": {"id": "ghl-contact-789"}}

    adapter._request = fake_request  # type: ignore[assignment]
    # No field mappings configured → body carries only locationId.
    with patch("app.adapters.ghl.resolve_mappings", new=AsyncMock(return_value={})):
        contact_id = await adapter.push_lead(lead, config)

    assert contact_id == "ghl-contact-789"
    assert captured["method"] == "POST"
    assert captured["path"] == "/contacts/"
    assert captured["body"]["locationId"] == "loc-123"


@pytest.mark.asyncio
async def test_push_lead_raises_on_api_failure():
    adapter = GoHighLevelAdapter()
    config = _make_config(uuid4())
    lead = _make_lead(client_id=config.client_id)
    adapter._request = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with patch("app.adapters.ghl.resolve_mappings", new=AsyncMock(return_value={})):
        with pytest.raises(RuntimeError, match="create contact failed"):
            await adapter.push_lead(lead, config)


@pytest.mark.asyncio
async def test_update_lead_sends_put_with_mapped_fields():
    adapter = GoHighLevelAdapter()
    config = _make_config(uuid4())

    captured: dict[str, Any] = {}

    async def fake_request(*, api_key, method, path, json_body=None):
        captured.update(method=method, path=path, body=json_body)
        return {}

    adapter._request = fake_request  # type: ignore[assignment]
    mappings = {
        "phone": FieldMapping(
            canonical_field="phone",
            external_field="phone",
            external_field_type="standard",
            transform=None,
        )
    }
    with patch("app.adapters.ghl.resolve_mappings", new=AsyncMock(return_value=mappings)):
        await adapter.update_lead("contact-42", {"phone": "+15559999999"}, config)

    assert captured["method"] == "PUT"
    assert captured["path"] == "/contacts/contact-42"
    assert captured["body"] == {"phone": "+15559999999"}


@pytest.mark.asyncio
async def test_update_lead_noop_when_nothing_mapped():
    adapter = GoHighLevelAdapter()
    config = _make_config(uuid4())
    adapter._request = AsyncMock()  # type: ignore[method-assign]
    with patch("app.adapters.ghl.resolve_mappings", new=AsyncMock(return_value={})):
        await adapter.update_lead("contact-42", {"phone": "+1555"}, config)
    adapter._request.assert_not_called()


# ---------------------------------------------------------------------------
# lookup_by_phone — pre-send CRM classification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_by_phone_none_without_creds():
    """No usable credentials → None (never raises), so the caller proceeds as a lead."""
    adapter = GoHighLevelAdapter()
    config = ClientConfig(
        client_id=uuid4(),
        crm_credentials={},
        ai_period_resets_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert await adapter.lookup_by_phone("+15551234567", config) is None


@pytest.mark.asyncio
async def test_lookup_by_phone_none_on_empty_result():
    adapter = GoHighLevelAdapter()
    adapter._request = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert await adapter.lookup_by_phone("+15551234567", _make_config(uuid4())) is None


@pytest.mark.asyncio
async def test_lookup_by_phone_maps_customer():
    adapter = GoHighLevelAdapter()
    adapter._request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "contacts": [
                {
                    "id": "c1",
                    "phone": "+15551234567",
                    "type": "customer",
                    "firstName": "Repeat",
                    "lastName": "Client",
                    "tags": [],
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
async def test_lookup_by_phone_vendor_tag_overrides_type():
    """A vendor/supplier tag wins over the GHL `type` field."""
    adapter = GoHighLevelAdapter()
    adapter._request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "contacts": [
                {"id": "v1", "phone": "+15551234567", "type": "customer", "tags": ["Preferred Supplier"]}
            ]
        }
    )
    contact = await adapter.lookup_by_phone("+15551234567", _make_config(uuid4()))
    assert contact is not None
    assert contact.contact_type == ContactType.vendor


@pytest.mark.asyncio
async def test_lookup_by_phone_rejects_fuzzy_nonmatch():
    """GHL's query search is fuzzy; a contact whose phone differs is rejected
    so a real lead is never routed to the wrong disposition."""
    adapter = GoHighLevelAdapter()
    adapter._request = AsyncMock(  # type: ignore[method-assign]
        return_value={"contacts": [{"id": "x1", "phone": "+19998887777", "type": "customer"}]}
    )
    assert await adapter.lookup_by_phone("+15551234567", _make_config(uuid4())) is None


# ---------------------------------------------------------------------------
# fetch_recovered_value — confirmed recovered-revenue readback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_recovered_value_sums_won_opportunities():
    adapter = GoHighLevelAdapter()
    adapter._request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "opportunities": [
                {"id": "o1", "status": "won", "monetaryValue": "4500.00"},
                {"id": "o2", "status": "won", "monetaryValue": 1500},
                {"id": "o3", "status": "open", "monetaryValue": "9999"},  # not booked
                {"id": "o4", "status": "lost", "monetaryValue": "1234"},
                {"id": "o5", "status": "won", "monetaryValue": None},  # no value set
            ]
        }
    )
    value = await adapter.fetch_recovered_value("c1", _make_config(uuid4()))
    assert value == Decimal("6000.00")
    # the search is scoped to the contact and asks for won server-side too
    params = adapter._request.await_args.kwargs["params"]
    assert params["contact_id"] == "c1"
    assert params["location_id"] == "loc-123"
    assert params["status"] == "won"


@pytest.mark.asyncio
async def test_fetch_recovered_value_none_when_nothing_won():
    adapter = GoHighLevelAdapter()
    adapter._request = AsyncMock(  # type: ignore[method-assign]
        return_value={"opportunities": [{"id": "o1", "status": "open", "monetaryValue": "500"}]}
    )
    assert await adapter.fetch_recovered_value("c1", _make_config(uuid4())) is None

    adapter._request = AsyncMock(return_value={"opportunities": []})  # type: ignore[method-assign]
    assert await adapter.fetch_recovered_value("c1", _make_config(uuid4())) is None


@pytest.mark.asyncio
async def test_fetch_recovered_value_none_without_creds():
    adapter = GoHighLevelAdapter()
    config = ClientConfig(
        client_id=uuid4(),
        crm_credentials={},
        ai_period_resets_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert await adapter.fetch_recovered_value("c1", config) is None


@pytest.mark.asyncio
async def test_fetch_recovered_value_none_on_request_failure():
    adapter = GoHighLevelAdapter()
    adapter._request = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert await adapter.fetch_recovered_value("c1", _make_config(uuid4())) is None


def test_ghl_parse_money_rejects_garbage():
    assert GoHighLevelAdapter._parse_money("not-a-number") is None
    assert GoHighLevelAdapter._parse_money(None) is None
    assert GoHighLevelAdapter._parse_money("") is None
    assert GoHighLevelAdapter._parse_money(0) is None
    assert GoHighLevelAdapter._parse_money("1200.50") == Decimal("1200.50")
