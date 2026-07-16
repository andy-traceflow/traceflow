"""Missed-call greeting prompt tests.

Covers variant selection (neutral vs returning), the static customer/vendor
acks, and generate_greeting with the Anthropic client mocked — runs offline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.models.client_config import ClientConfig
from app.models.contact import Contact
from app.prompts import greeting


def _make_config(**overrides: Any) -> ClientConfig:
    base = {
        "client_id": uuid4(),
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ClientConfig(**base)


def _contact(name: str | None = "Maria", **overrides: Any) -> Contact:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid4(), "client_id": uuid4(), "phone": "+15551112222",
        "name": name, "first_seen_at": now, "last_seen_at": now, "updated_at": now,
    }
    base.update(overrides)
    return Contact(**base)


def _fake_response(text: str) -> Mock:
    resp = Mock()
    resp.content = [Mock(type="text", text=text)]
    return resp


# ---------------------------------------------------------------------------
# Variant selection — never guess a name
# ---------------------------------------------------------------------------


def test_returning_selected_for_recognized_named_caller() -> None:
    contact = _contact(name="Maria", call_count=2)
    assert greeting._select_version(_make_config(), contact, is_returning=True) == "returning_v1"


def test_neutral_when_name_missing() -> None:
    contact = _contact(name=None, call_count=5, crm_external_id="hs-1")
    # No name → never the returning variant (never guess a name).
    assert greeting._select_version(_make_config(), contact, is_returning=True) == "neutral_v1"


def test_neutral_when_recognition_disabled() -> None:
    contact = _contact(name="Maria")
    config = _make_config(conversation_config={"recognize_returning_callers": False})
    assert greeting._select_version(config, contact, is_returning=True) == "neutral_v1"


def test_returning_for_crm_linked_contact_even_first_call() -> None:
    contact = _contact(name="Maria", crm_external_id="hs-1")
    assert greeting._select_version(_make_config(), contact, is_returning=False) == "returning_v1"


# ---------------------------------------------------------------------------
# Static acks
# ---------------------------------------------------------------------------


def test_customer_ack_uses_template() -> None:
    config = _make_config(
        existing_customer_template="Hey! {business_name} here, someone will call you back.",
        brand={"business_name": "Acme"},
    )
    assert "Acme" in greeting.render_customer_ack(config)


def test_customer_ack_default_when_unset() -> None:
    config = _make_config(brand={"business_name": "Acme"})
    assert greeting.render_customer_ack(config).startswith("Hi! Thanks for calling Acme")


def test_vendor_ack_default_is_minimal() -> None:
    config = _make_config(brand={"business_name": "Acme"})
    assert "Acme" in greeting.render_vendor_ack(config)


# ---------------------------------------------------------------------------
# generate_greeting (Anthropic client mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_greeting_none_without_api_key() -> None:
    with patch("app.prompts.greeting.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = ""
        result = await greeting.generate_greeting(_make_config())
    assert result is None


@pytest.mark.asyncio
async def test_generate_greeting_returns_text_and_version() -> None:
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(return_value=_fake_response("  Hi from Acme!  "))

    with (
        patch("app.prompts.greeting.get_settings") as mock_settings,
        patch("app.prompts.greeting.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await greeting.generate_greeting(_make_config())

    assert result == ("Hi from Acme!", "neutral_v1")  # first-timer → neutral
    kwargs = mock_client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-4-5"
    # System is content blocks; the business block is cached.
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_generate_greeting_returning_variant_for_known_caller() -> None:
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(return_value=_fake_response("Hi Maria!"))
    contact = _contact(name="Maria", call_count=2)

    with (
        patch("app.prompts.greeting.get_settings") as mock_settings,
        patch("app.prompts.greeting.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await greeting.generate_greeting(_make_config(), contact, is_returning=True)

    assert result == ("Hi Maria!", "returning_v1")
    # The caller block is present so the model can greet by name.
    system_text = " ".join(b["text"] for b in mock_client.messages.create.call_args.kwargs["system"])
    assert "Maria" in system_text


@pytest.mark.asyncio
async def test_generate_greeting_none_on_api_error() -> None:
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(side_effect=Exception("api down"))

    with (
        patch("app.prompts.greeting.get_settings") as mock_settings,
        patch("app.prompts.greeting.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await greeting.generate_greeting(_make_config())

    assert result is None


@pytest.mark.asyncio
async def test_generate_greeting_none_on_empty_text() -> None:
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(return_value=_fake_response("   "))

    with (
        patch("app.prompts.greeting.get_settings") as mock_settings,
        patch("app.prompts.greeting.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await greeting.generate_greeting(_make_config())

    assert result is None
