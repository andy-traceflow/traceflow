"""Lead qualifier prompt tests.

Covers system-prompt rendering, message-history mapping, and
qualifier_turn with the Anthropic client mocked — no real API calls.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.models.client_config import ClientConfig
from app.models.message import Message
from app.prompts import qualifier


def _make_config(**overrides: Any) -> ClientConfig:
    base = {
        "client_id": uuid4(),
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ClientConfig(**base)


def _msg(direction: str, body: str) -> Message:
    return Message(
        id=uuid4(),
        client_id=uuid4(),
        lead_id=uuid4(),
        direction=direction,
        channel="sms",
        body=body,
        created_at=datetime.now(UTC),
    )


def _text_block(text: str) -> Mock:
    return Mock(type="text", text=text)


def _tool_block(tool_input: dict[str, Any]) -> Mock:
    block = Mock(type="tool_use", input=tool_input)
    block.name = "update_lead"  # 'name' is a reserved Mock kwarg — set it after
    return block


def _fake_response(*blocks: Mock) -> Mock:
    resp = Mock()
    resp.content = list(blocks)
    return resp


# ---------------------------------------------------------------------------
# System prompt rendering
# ---------------------------------------------------------------------------

def test_render_system_fills_business_context():
    config = _make_config(
        brand={
            "business_name": "Acme Surfaces",
            "category": "countertop",
            "tone_of_voice": "warm",
            "service_types": ["countertop", "flooring"],
        },
        service_area_zips=["89101"],
        vip_keywords=["commercial", "rush"],
    )
    system = qualifier._render_system(config, "v1")
    assert "Acme Surfaces" in system
    assert "countertop" in system
    assert "warm" in system
    assert "89101" in system
    assert "commercial" in system  # vip keyword rendered


def test_render_system_handles_empty_optionals():
    system = qualifier._render_system(_make_config(), "v1")
    assert "our team" in system      # business_name fallback
    assert "update_lead" in system   # core instruction still present


# ---------------------------------------------------------------------------
# Message-history mapping
# ---------------------------------------------------------------------------

def test_to_api_messages_maps_roles_and_drops_leading_assistant():
    history = [
        _msg("outbound", "Hi, sorry we missed you!"),  # the greeting — assistant
        _msg("inbound", "I need new countertops"),
        _msg("outbound", "Great — how many sqft?"),
        _msg("inbound", "About 40"),
    ]
    msgs = qualifier._to_api_messages(history)
    assert len(msgs) == 3  # leading greeting dropped
    assert msgs[0] == {"role": "user", "content": "I need new countertops"}
    assert msgs[1] == {"role": "assistant", "content": "Great — how many sqft?"}
    assert msgs[-1] == {"role": "user", "content": "About 40"}


def test_to_api_messages_empty_when_only_assistant():
    assert qualifier._to_api_messages([_msg("outbound", "Hi!")]) == []


# ---------------------------------------------------------------------------
# qualifier_turn (Anthropic client mocked)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_qualifier_turn_none_without_api_key():
    with patch("app.prompts.qualifier.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = ""
        result = await qualifier.qualifier_turn(_make_config(), [_msg("inbound", "hello")])
    assert result is None


@pytest.mark.asyncio
async def test_qualifier_turn_none_with_no_user_message():
    # History is only an assistant turn → nothing for the model to respond to.
    with patch("app.prompts.qualifier.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await qualifier.qualifier_turn(_make_config(), [_msg("outbound", "Hi!")])
    assert result is None


@pytest.mark.asyncio
async def test_qualifier_turn_returns_text_extracted_and_version():
    history = [_msg("inbound", "I need 40 sqft of countertop, this month")]
    response = _fake_response(
        _text_block("Got it! What's your zip code?"),
        _tool_block({"service_type": "countertop", "sqft": 40, "timeframe": "this_month"}),
    )
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(return_value=response)

    with (
        patch("app.prompts.qualifier.get_settings") as mock_settings,
        patch("app.prompts.qualifier.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await qualifier.qualifier_turn(_make_config(), history)

    assert result is not None
    reply, extracted, version = result
    assert reply == "Got it! What's your zip code?"
    assert extracted == {"service_type": "countertop", "sqft": 40, "timeframe": "this_month"}
    assert version == "v1"
    assert mock_client.messages.create.call_args.kwargs["model"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_qualifier_turn_none_on_api_error():
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(side_effect=Exception("api down"))
    with (
        patch("app.prompts.qualifier.get_settings") as mock_settings,
        patch("app.prompts.qualifier.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await qualifier.qualifier_turn(_make_config(), [_msg("inbound", "hello")])
    assert result is None
