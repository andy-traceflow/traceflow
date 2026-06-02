"""Intent-classification prompt tests.

Covers system-prompt rendering, history mapping, and classify_intent with
the Anthropic client mocked — no real API calls. The prime directive under
test: any failure or unparseable response returns None so the caller
degrades toward the qualifier and never drops a real lead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.models.client_config import ClientConfig
from app.models.message import Message
from app.prompts import intent
from app.prompts.intent import Intent


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
    block.name = "classify_intent"  # 'name' is a reserved Mock kwarg — set it after
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
    )
    system = intent._render_system(config, "v1")
    assert "Acme Surfaces" in system
    assert "countertop" in system
    assert "89101" in system
    assert "classify_intent" in system  # tool instruction present


def test_render_system_handles_empty_optionals():
    system = intent._render_system(_make_config(), "v1")
    assert "our team" in system        # business_name fallback
    assert "the local area" in system  # service_area fallback


def test_render_system_unknown_version_falls_back_to_default():
    system = intent._render_system(_make_config(), "v999")
    assert "classify_intent" in system


# ---------------------------------------------------------------------------
# Message-history mapping
# ---------------------------------------------------------------------------

def test_to_api_messages_drops_leading_assistant():
    history = [
        _msg("outbound", "Hi, sorry we missed you!"),  # greeting — assistant
        _msg("inbound", "Do you do commercial jobs?"),
    ]
    msgs = intent._to_api_messages(history)
    assert len(msgs) == 1
    assert msgs[0] == {"role": "user", "content": "Do you do commercial jobs?"}


def test_to_api_messages_keeps_clarifier_context():
    # The ambiguous re-run case: greeting, thin reply, our clarifier, real reply.
    history = [
        _msg("outbound", "Hi, sorry we missed you!"),
        _msg("inbound", "hi"),
        _msg("outbound", "Quote or existing job?"),
        _msg("inbound", "Need a quote for new counters"),
    ]
    msgs = intent._to_api_messages(history)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[-1]["content"] == "Need a quote for new counters"


def test_to_api_messages_empty_when_only_assistant():
    assert intent._to_api_messages([_msg("outbound", "Hi!")]) == []


# ---------------------------------------------------------------------------
# classify_intent (Anthropic client mocked)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_classify_intent_none_without_api_key():
    with patch("app.prompts.intent.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = ""
        result = await intent.classify_intent(_make_config(), [_msg("inbound", "hi")])
    assert result is None


@pytest.mark.asyncio
async def test_classify_intent_none_with_no_user_message():
    with patch("app.prompts.intent.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await intent.classify_intent(_make_config(), [_msg("outbound", "Hi!")])
    assert result is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sales", Intent.sales),
        ("existing_customer", Intent.existing_customer),
        ("non_lead", Intent.non_lead),
        ("spam", Intent.spam),
        ("ambiguous", Intent.ambiguous),
    ],
)
@pytest.mark.asyncio
async def test_classify_intent_parses_each_label(raw: str, expected: Intent):
    response = _fake_response(_tool_block({"intent": raw, "reason": "because"}))
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(return_value=response)
    with (
        patch("app.prompts.intent.get_settings") as mock_settings,
        patch("app.prompts.intent.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await intent.classify_intent(_make_config(), [_msg("inbound", "hello")])
    assert result == expected
    assert mock_client.messages.create.call_args.kwargs["model"] == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_classify_intent_forces_the_tool():
    response = _fake_response(_tool_block({"intent": "sales"}))
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(return_value=response)
    with (
        patch("app.prompts.intent.get_settings") as mock_settings,
        patch("app.prompts.intent.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        await intent.classify_intent(_make_config(), [_msg("inbound", "hello")])
    tool_choice = mock_client.messages.create.call_args.kwargs["tool_choice"]
    assert tool_choice == {"type": "tool", "name": "classify_intent"}


@pytest.mark.asyncio
async def test_classify_intent_none_on_unknown_label():
    response = _fake_response(_tool_block({"intent": "maybe_later"}))
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(return_value=response)
    with (
        patch("app.prompts.intent.get_settings") as mock_settings,
        patch("app.prompts.intent.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await intent.classify_intent(_make_config(), [_msg("inbound", "hello")])
    assert result is None


@pytest.mark.asyncio
async def test_classify_intent_none_when_no_tool_block():
    response = _fake_response(_text_block("I think this is sales"))  # no tool call
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(return_value=response)
    with (
        patch("app.prompts.intent.get_settings") as mock_settings,
        patch("app.prompts.intent.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await intent.classify_intent(_make_config(), [_msg("inbound", "hello")])
    assert result is None


@pytest.mark.asyncio
async def test_classify_intent_none_on_api_error():
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(side_effect=Exception("api down"))
    with (
        patch("app.prompts.intent.get_settings") as mock_settings,
        patch("app.prompts.intent.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await intent.classify_intent(_make_config(), [_msg("inbound", "hello")])
    assert result is None
