"""Caller-classification tests — pre-send routing decisions.

The CRM lookup and DB are mocked so the suite runs offline. The focus is
the routing tree and its prime directive: every failing or ambiguous path
degrades toward potential_lead so a real lead is never dropped.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.models.client_config import ClientConfig
from app.models.crm_contact import ContactType, CRMContact
from app.models.lead import Classification
from app.services import classification
from app.services.classification import Route, classify_caller
from app.services.spam import SpamRisk


def _make_config(**overrides: Any) -> ClientConfig:
    base = {
        "client_id": uuid4(),
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ClientConfig(**base)


def _conn(active_lead_id: Any = None) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = active_lead_id
    return conn


def _adapter_returning(contact: CRMContact | None) -> Mock:
    adapter = Mock()
    adapter.lookup_by_phone = AsyncMock(return_value=contact)
    return adapter


@pytest.mark.asyncio
async def test_no_phone_defaults_to_potential_lead():
    conn = _conn()
    result = await classify_caller(conn, uuid4(), None, _make_config())
    assert result.route == Route.potential_lead
    assert result.classification == Classification.potential_lead
    assert result.should_text is True
    conn.fetchval.assert_not_called()  # never even queried the DB


@pytest.mark.asyncio
async def test_active_conversation_short_circuits():
    existing = uuid4()
    conn = _conn(active_lead_id=existing)
    result = await classify_caller(conn, uuid4(), "+15551112222", _make_config())
    assert result.route == Route.active_conversation
    assert result.should_text is False
    assert result.existing_lead_id == existing


@pytest.mark.asyncio
async def test_vendor_allowlist_routes_known_non_lead_no_text():
    conn = _conn()
    config = _make_config(vendor_allowlist=["+15551112222"])
    result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.known_non_lead
    assert result.classification == Classification.known_non_lead
    assert result.should_text is False  # text_vendors defaults False


@pytest.mark.asyncio
async def test_vendor_allowlist_texts_when_configured():
    conn = _conn()
    config = _make_config(
        vendor_allowlist=["+15551112222"],
        classification_config={"text_vendors": True},
    )
    result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.known_non_lead
    assert result.should_text is True


@pytest.mark.asyncio
async def test_crm_customer_routes_existing_customer():
    conn = _conn()
    config = _make_config(crm_provider="ghl")
    contact = CRMContact(external_id="c1", name="Repeat Client", contact_type=ContactType.customer)
    with patch.object(classification, "get_adapter", return_value=_adapter_returning(contact)):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.existing_customer
    assert result.classification == Classification.existing_customer
    assert result.should_text is True  # text_existing_customers defaults True
    assert result.contact is contact


@pytest.mark.asyncio
async def test_crm_customer_suppressed_when_opted_out():
    conn = _conn()
    config = _make_config(
        crm_provider="ghl",
        classification_config={"text_existing_customers": False},
    )
    contact = CRMContact(external_id="c1", contact_type=ContactType.customer)
    with patch.object(classification, "get_adapter", return_value=_adapter_returning(contact)):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.existing_customer
    assert result.should_text is False


@pytest.mark.asyncio
async def test_crm_vendor_routes_known_non_lead():
    conn = _conn()
    config = _make_config(crm_provider="ghl")
    contact = CRMContact(external_id="v1", contact_type=ContactType.vendor)
    with patch.object(classification, "get_adapter", return_value=_adapter_returning(contact)):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.known_non_lead
    assert result.should_text is False


@pytest.mark.asyncio
async def test_crm_known_lead_stays_potential_lead():
    conn = _conn()
    config = _make_config(crm_provider="ghl")
    contact = CRMContact(external_id="l1", contact_type=ContactType.lead)
    with patch.object(classification, "get_adapter", return_value=_adapter_returning(contact)):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.potential_lead
    assert result.should_text is True
    assert result.reason == "crm_known_lead"


@pytest.mark.asyncio
async def test_crm_lookup_failure_degrades_to_potential_lead():
    """A raising adapter must never drop a real lead."""
    conn = _conn()
    config = _make_config(crm_provider="ghl")
    adapter = Mock()
    adapter.lookup_by_phone = AsyncMock(side_effect=Exception("CRM down"))
    with patch.object(classification, "get_adapter", return_value=adapter):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.potential_lead
    assert result.should_text is True


@pytest.mark.asyncio
async def test_unknown_crm_provider_degrades_to_potential_lead():
    conn = _conn()
    config = _make_config(crm_provider="salesforce")  # no adapter registered
    with patch.object(classification, "get_adapter", side_effect=ValueError("unknown")):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.potential_lead


@pytest.mark.asyncio
async def test_no_crm_provider_skips_lookup():
    conn = _conn()
    config = _make_config()  # crm_provider is None
    with patch.object(classification, "get_adapter") as mock_get:
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    mock_get.assert_not_called()
    assert result.route == Route.potential_lead


@pytest.mark.asyncio
async def test_crm_lookup_disabled_skips_lookup():
    conn = _conn()
    config = _make_config(
        crm_provider="ghl",
        classification_config={"crm_lookup_enabled": False},
    )
    with patch.object(classification, "get_adapter") as mock_get:
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    mock_get.assert_not_called()
    assert result.route == Route.potential_lead


# ---------------------------------------------------------------------------
# Spam scoring — unknown callers only, never drops a real lead
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_caller_high_risk_routes_spam():
    conn = _conn()
    config = _make_config()  # spam_filtering on, threshold 'moderate', drop silently
    with patch.object(
        classification, "score_spam_risk", new=AsyncMock(return_value=SpamRisk.high)
    ):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.spam
    assert result.classification == Classification.spam
    assert result.should_text is False           # drop_spam_silently defaults True
    assert result.reason == "spam_risk:high"


@pytest.mark.asyncio
async def test_unknown_caller_low_risk_stays_potential_lead():
    conn = _conn()
    config = _make_config()
    with patch.object(
        classification, "score_spam_risk", new=AsyncMock(return_value=SpamRisk.low)
    ):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.potential_lead
    assert result.should_text is True


@pytest.mark.asyncio
async def test_moderate_risk_kept_at_default_threshold():
    """Default 'moderate' threshold has a HIGH floor — a moderate-risk line
    type (e.g. toll-free) is NOT dropped, protecting borderline real leads."""
    conn = _conn()
    config = _make_config()
    with patch.object(
        classification, "score_spam_risk", new=AsyncMock(return_value=SpamRisk.moderate)
    ):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.potential_lead


@pytest.mark.asyncio
async def test_strict_threshold_drops_moderate_risk():
    conn = _conn()
    config = _make_config(classification_config={"spam_risk_threshold": "strict"})
    with patch.object(
        classification, "score_spam_risk", new=AsyncMock(return_value=SpamRisk.moderate)
    ):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.spam


@pytest.mark.asyncio
async def test_spam_not_dropped_silently_still_texts():
    """drop_spam_silently=False tags spam for metrics but keeps the recovery
    text flowing, so a false positive isn't a dropped lead."""
    conn = _conn()
    config = _make_config(classification_config={"drop_spam_silently": False})
    with patch.object(
        classification, "score_spam_risk", new=AsyncMock(return_value=SpamRisk.high)
    ):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.spam
    assert result.should_text is True


@pytest.mark.asyncio
async def test_spam_filtering_disabled_skips_scoring():
    conn = _conn()
    config = _make_config(classification_config={"spam_filtering_enabled": False})
    mock_score = AsyncMock(return_value=SpamRisk.high)
    with patch.object(classification, "score_spam_risk", new=mock_score):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    mock_score.assert_not_called()
    assert result.route == Route.potential_lead


@pytest.mark.asyncio
async def test_spam_lookup_none_degrades_to_potential_lead():
    """No signal (Lookup unavailable) → never spam."""
    conn = _conn()
    config = _make_config()
    with patch.object(
        classification, "score_spam_risk", new=AsyncMock(return_value=None)
    ):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.potential_lead


@pytest.mark.asyncio
async def test_spam_lookup_failure_degrades_to_potential_lead():
    """A raising lookup is swallowed by _safe_spam_score → potential_lead."""
    conn = _conn()
    config = _make_config()
    with patch.object(
        classification,
        "score_spam_risk",
        new=AsyncMock(side_effect=Exception("twilio down")),
    ):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    assert result.route == Route.potential_lead


@pytest.mark.asyncio
async def test_crm_known_caller_is_never_spam_scored():
    """A CRM match short-circuits before spam scoring — known callers are
    never scored as spam, even a high-risk line type."""
    conn = _conn()
    config = _make_config(crm_provider="ghl")
    contact = CRMContact(external_id="c1", contact_type=ContactType.customer)
    mock_score = AsyncMock(return_value=SpamRisk.high)
    with (
        patch.object(classification, "get_adapter", return_value=_adapter_returning(contact)),
        patch.object(classification, "score_spam_risk", new=mock_score),
    ):
        result = await classify_caller(conn, uuid4(), "+15551112222", config)
    mock_score.assert_not_called()
    assert result.route == Route.existing_customer
