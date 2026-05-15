"""Monday adapter tests.

These exercise the pure helpers (item-name formatting, canonical →
column-values translation, transform application). The GraphQL roundtrip
is mocked so the suite runs offline and deterministically.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.adapters.monday import MondayAdapter
from app.models.client_config import ClientConfig
from app.models.lead import Lead, QualificationStatus
from app.services.field_mappings import FieldMapping


def _make_lead(**overrides: Any) -> Lead:
    base = {
        "id": uuid4(),
        "client_id": uuid4(),
        "external_id": "EXT-100",
        "source_system": "shopify",
        "contact_name": "Jane Doe",
        "contact_company": "Doe Co",
        "phone": "+15551234567",
        "email": "jane@example.com",
        "service_type": "consult",
        "sqft": 250.0,
        "raw_payload": {},
        "qualification_status": QualificationStatus.unqualified,
        "notes": "",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return Lead(**base)


def _make_config(client_id, **overrides: Any) -> ClientConfig:
    base = {
        "client_id": client_id,
        "crm_provider": "monday",
        "crm_credentials": {"api_key": "fake-key", "board_id": "999"},
        "ai_period_resets_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return ClientConfig(**base)


# ---------------------------------------------------------------------------
# Item name formatting
# ---------------------------------------------------------------------------

def test_item_name_with_company():
    adapter = MondayAdapter()
    lead = _make_lead(contact_name="Jane Doe", contact_company="Doe Co", external_id="ORD-7")
    assert adapter._format_item_name(lead) == "Jane Doe / Doe Co / ORD-7"


def test_item_name_without_company():
    adapter = MondayAdapter()
    lead = _make_lead(contact_name="Jane Doe", contact_company=None, external_id="ORD-7")
    assert adapter._format_item_name(lead) == "Jane Doe / ORD-7"


def test_item_name_with_unknown_contact():
    adapter = MondayAdapter()
    lead = _make_lead(contact_name=None, contact_company="Doe Co", external_id="ORD-7")
    assert adapter._format_item_name(lead) == "Unknown Contact / Doe Co / ORD-7"


def test_item_name_falls_back_to_lead_id_prefix():
    adapter = MondayAdapter()
    lead = _make_lead(contact_name="Jane Doe", external_id=None, contact_company=None)
    name = adapter._format_item_name(lead)
    assert name.startswith("Jane Doe / ")
    # ref is the first 8 chars of the lead.id
    assert len(name.split(" / ")[-1]) == 8


# ---------------------------------------------------------------------------
# Canonical → column values
# ---------------------------------------------------------------------------

def test_build_parent_columns_with_value_map_transform():
    adapter = MondayAdapter()
    lead = _make_lead(service_type="consult", sqft=200.0)
    config = _make_config(lead.client_id)
    board_id = str(config.crm_credentials["board_id"])

    # Prime the cache with parent columns for two fields
    adapter._column_cache[board_id] = {
        "parent": {"service_type": "status_col_id", "sqft": "num_col_id"},
        "subitem": {},
        "subitem_board_id": None,
    }

    mappings = {
        "service_type": FieldMapping(
            canonical_field="service_type",
            external_field="Service",
            external_field_type="column",
            transform={"type": "value_map", "mapping": {"consult": "Consultation"}},
        ),
        "sqft": FieldMapping(
            canonical_field="sqft",
            external_field="Square Feet",
            external_field_type="column",
            transform=None,
        ),
    }

    columns = adapter._build_parent_columns(lead, mappings, board_id)
    assert columns["status_col_id"] == "Consultation"   # transformed
    assert columns["num_col_id"] == "200.0"             # str-serialized


def test_canonical_dict_includes_all_known_fields():
    lead = _make_lead()
    canonical = MondayAdapter._canonical_dict(lead)
    expected_keys = {
        "contact_name", "contact_company", "phone", "email", "address",
        "service_type", "sqft", "budget_range", "timeframe", "notes", "external_id",
    }
    assert expected_keys.issubset(canonical.keys())


# ---------------------------------------------------------------------------
# Health check + creds
# ---------------------------------------------------------------------------

def test_creds_validation_fails_without_required_keys():
    adapter = MondayAdapter()
    config = ClientConfig(
        client_id=uuid4(),
        crm_credentials={"api_key": "only-the-key"},
        ai_period_resets_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ValueError, match="board_id"):
        adapter._creds(config)


@pytest.mark.asyncio
async def test_health_check_returns_false_on_request_error():
    adapter = MondayAdapter()
    adapter._request = AsyncMock(side_effect=Exception("network"))  # type: ignore[method-assign]
    config = _make_config(uuid4())
    assert await adapter.health_check(config) is False


@pytest.mark.asyncio
async def test_push_lead_returns_external_id():
    """Mock the HTTP layer; verify the adapter assembles the right call shape."""
    adapter = MondayAdapter()
    client_id = uuid4()
    lead = _make_lead(client_id=client_id, contact_name="Jane Doe", external_id="ORD-7")
    config = _make_config(client_id)

    # Mock column discovery + GraphQL ops
    async def fake_request(api_key, query, variables):
        if "boards(ids" in query:
            return {
                "data": {
                    "boards": [{
                        "columns": [
                            {"id": "name_col", "title": "Name", "type": "name", "settings_str": "{}"},
                        ],
                    }],
                }
            }
        if "create_item" in query:
            return {"data": {"create_item": {"id": "monday-item-123"}}}
        return {"data": {}}

    adapter._request = fake_request  # type: ignore[assignment]
    # No field mappings configured → no parent columns to set
    from unittest.mock import patch
    with patch("app.adapters.monday.resolve_mappings", new=AsyncMock(return_value={})):
        external_id = await adapter.push_lead(lead, config)
    assert external_id == "monday-item-123"
