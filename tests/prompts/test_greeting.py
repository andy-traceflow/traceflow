"""Missed-call greeting prompt tests.

Covers template rendering (pure) and generate_greeting with the Anthropic
client mocked — no real API calls, runs offline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.models.client_config import ClientConfig
from app.prompts import greeting


def _make_config(**overrides: Any) -> ClientConfig:
    base = {
        "client_id": uuid4(),
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ClientConfig(**base)


def _fake_response(text: str) -> Mock:
    """Stand-in for an Anthropic Messages response carrying one text block."""
    resp = Mock()
    resp.content = [Mock(type="text", text=text)]
    return resp


# ---------------------------------------------------------------------------
# Template rendering (pure)
# ---------------------------------------------------------------------------

def test_render_prompt_fills_business_context():
    config = _make_config(
        brand={
            "business_name": "Acme Surfaces",
            "category": "countertop",
            "tone_of_voice": "casual",
        },
        service_area_zips=["89101", "89102"],
    )
    prompt = greeting._render_prompt(config, "v1")
    assert "Acme Surfaces" in prompt
    assert "countertop" in prompt
    assert "casual" in prompt
    assert "89101" in prompt


def test_render_prompt_uses_fallbacks():
    prompt = greeting._render_prompt(_make_config(), "v1")
    assert "our team" in prompt        # no business_name configured
    assert "the local area" in prompt  # no service-area ZIPs


def test_render_prompt_unknown_version_falls_back_to_default():
    prompt = greeting._render_prompt(_make_config(), "v99")
    assert "SMS" in prompt  # rendered the default template, did not raise


def test_service_area_truncates_to_first_three_zips():
    config = _make_config(service_area_zips=["1", "2", "3", "4", "5"])
    assert greeting._service_area(config) == "1, 2, 3"


# ---------------------------------------------------------------------------
# generate_greeting (Anthropic client mocked)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_greeting_none_without_api_key():
    with patch("app.prompts.greeting.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = ""
        result = await greeting.generate_greeting(_make_config())
    assert result is None


@pytest.mark.asyncio
async def test_generate_greeting_returns_text_and_version():
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(return_value=_fake_response("  Hi from Acme!  "))

    with (
        patch("app.prompts.greeting.get_settings") as mock_settings,
        patch("app.prompts.greeting.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await greeting.generate_greeting(_make_config())

    assert result == ("Hi from Acme!", "v1")  # text stripped, version resolved
    assert mock_client.messages.create.call_args.kwargs["model"] == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_generate_greeting_none_on_api_error():
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
async def test_generate_greeting_none_on_empty_text():
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(return_value=_fake_response("   "))

    with (
        patch("app.prompts.greeting.get_settings") as mock_settings,
        patch("app.prompts.greeting.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await greeting.generate_greeting(_make_config())

    assert result is None
