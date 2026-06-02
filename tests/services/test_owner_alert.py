"""Owner alert system tests — VIP triggers and alert dispatch."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.models.client_config import ClientConfig
from app.services import owner_alert


def _make_config(**overrides: Any) -> ClientConfig:
    base = {
        "client_id": uuid4(),
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ClientConfig(**base)


# ---------------------------------------------------------------------------
# find_vip_reason — keyword trigger
# ---------------------------------------------------------------------------

def test_keyword_trigger_matches():
    config = _make_config(vip_keywords=["emergency", "commercial"])
    reason = owner_alert.find_vip_reason(
        config, text="we have an emergency leak", budget_range=None
    )
    assert reason is not None
    assert "emergency" in reason


def test_keyword_trigger_is_case_insensitive():
    config = _make_config(vip_keywords=["Emergency"])
    assert owner_alert.find_vip_reason(
        config, text="THIS IS AN EMERGENCY", budget_range=None
    ) is not None


def test_no_keyword_no_match():
    config = _make_config(vip_keywords=["emergency"])
    assert owner_alert.find_vip_reason(config, text="just a normal job", budget_range=None) is None


# ---------------------------------------------------------------------------
# find_vip_reason — value trigger (conservative range floor)
# ---------------------------------------------------------------------------

def test_value_trigger_fires_when_floor_meets_threshold():
    config = _make_config(vip_value_threshold=10_000)
    # 15k-50k has a floor of $15k, which is >= the $10k threshold.
    reason = owner_alert.find_vip_reason(config, text="", budget_range="15k-50k")
    assert reason is not None
    assert "15k-50k" in reason


def test_value_trigger_silent_when_floor_below_threshold():
    config = _make_config(vip_value_threshold=10_000)
    # 5k-15k has a floor of $5k — below the $10k threshold, so no alert.
    assert owner_alert.find_vip_reason(config, text="", budget_range="5k-15k") is None


def test_value_trigger_silent_without_threshold():
    config = _make_config()  # vip_value_threshold is None
    assert owner_alert.find_vip_reason(config, text="", budget_range="50k+") is None


def test_find_vip_reason_none_with_no_signals():
    config = _make_config(vip_keywords=["emergency"], vip_value_threshold=50_000)
    assert owner_alert.find_vip_reason(config, text="hello", budget_range="5k-15k") is None


# ---------------------------------------------------------------------------
# alert_owner — dispatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alert_owner_dispatches_email_and_sms():
    config = _make_config(
        owner_alert_emails=["owner@example.com"],
        owner_alert_phones=["+15559990000"],
        twilio_number="+15551112222",
    )
    with (
        patch(
            "app.services.owner_alert.notify_owner_vip",
            new=AsyncMock(return_value=True),
        ) as mock_email,
        patch(
            "app.services.owner_alert.send_sms",
            new=AsyncMock(return_value={"sid": "SM-9"}),
        ) as mock_sms,
    ):
        delivered = await owner_alert.alert_owner(
            config, lead_summary="Jane Doe — countertop", reason="VIP keyword match: emergency"
        )

    assert delivered is True
    mock_email.assert_awaited_once()
    mock_sms.assert_awaited_once()
    assert mock_sms.call_args.kwargs["to"] == "+15559990000"


@pytest.mark.asyncio
async def test_alert_owner_false_when_nothing_delivered():
    config = _make_config(owner_alert_emails=["owner@example.com"])  # no owner phones
    with patch(
        "app.services.owner_alert.notify_owner_vip",
        new=AsyncMock(return_value=False),
    ):
        delivered = await owner_alert.alert_owner(config, lead_summary="x", reason="y")
    assert delivered is False


# ---------------------------------------------------------------------------
# alert_existing_customer — known customer reached voicemail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_existing_customer_alert_emails_explicit_contact():
    """A contact that looks like an email is emailed, not texted."""
    config = _make_config(existing_customer_alert_contact="ops@example.com")
    with (
        patch("app.services.owner_alert.send_email", new=AsyncMock(return_value=True)) as mock_email,
        patch("app.services.owner_alert.send_sms", new=AsyncMock()) as mock_sms,
    ):
        delivered = await owner_alert.alert_existing_customer(config, summary="Repeat client called")
    assert delivered is True
    mock_email.assert_awaited_once()
    assert mock_email.call_args.kwargs["to"] == ["ops@example.com"]
    mock_sms.assert_not_called()


@pytest.mark.asyncio
async def test_existing_customer_alert_texts_explicit_phone_contact():
    config = _make_config(
        existing_customer_alert_contact="+15557778888",
        twilio_number="+15551112222",
    )
    with (
        patch("app.services.owner_alert.send_email", new=AsyncMock()) as mock_email,
        patch("app.services.owner_alert.send_sms", new=AsyncMock(return_value={"sid": "SM-1"})) as mock_sms,
    ):
        delivered = await owner_alert.alert_existing_customer(config, summary="x")
    assert delivered is True
    mock_sms.assert_awaited_once()
    assert mock_sms.call_args.kwargs["to"] == "+15557778888"
    mock_email.assert_not_called()


@pytest.mark.asyncio
async def test_existing_customer_alert_phone_contact_without_twilio_number():
    """A phone target but no twilio_number can't deliver → False, no send attempted."""
    config = _make_config(existing_customer_alert_contact="+15557778888")  # no twilio_number
    with patch("app.services.owner_alert.send_sms", new=AsyncMock()) as mock_sms:
        delivered = await owner_alert.alert_existing_customer(config, summary="x")
    assert delivered is False
    mock_sms.assert_not_called()


@pytest.mark.asyncio
async def test_existing_customer_alert_falls_back_to_owner_channels():
    """No explicit contact → reuse the standard owner alert email + SMS lists."""
    config = _make_config(
        owner_alert_emails=["owner@example.com"],
        owner_alert_phones=["+15559990000"],
        twilio_number="+15551112222",
    )
    with (
        patch("app.services.owner_alert.send_email", new=AsyncMock(return_value=True)) as mock_email,
        patch("app.services.owner_alert.send_sms", new=AsyncMock(return_value={"sid": "SM-2"})) as mock_sms,
    ):
        delivered = await owner_alert.alert_existing_customer(config, summary="x")
    assert delivered is True
    mock_email.assert_awaited_once()
    mock_sms.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_customer_alert_false_when_nothing_configured():
    config = _make_config()  # no explicit contact, no owner channels
    with (
        patch("app.services.owner_alert.send_email", new=AsyncMock(return_value=False)),
        patch("app.services.owner_alert.send_sms", new=AsyncMock(return_value=None)),
    ):
        delivered = await owner_alert.alert_existing_customer(config, summary="x")
    assert delivered is False
