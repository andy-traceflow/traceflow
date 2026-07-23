"""Lead qualifier prompt tests (config-driven, Slice 3).

Covers dynamic tool-schema generation from a client schema, the v2 system
prompt's captured/missing-fields injection and turn budget, message-history
mapping, and qualifier_turn with the Anthropic client mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.models.client_config import ClientConfig
from app.models.message import Message
from app.models.qualification import QualificationSchema, default_schema
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
        id=uuid4(), client_id=uuid4(), lead_id=uuid4(),
        direction=direction, channel="sms", body=body, created_at=datetime.now(UTC),
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
# build_update_lead_tool — generated from the client schema
# ---------------------------------------------------------------------------


def test_tool_generated_from_schema_has_no_status_field() -> None:
    tool = qualifier.build_update_lead_tool(default_schema())
    props = tool["input_schema"]["properties"]
    assert "qualification_status" not in props  # code owns termination now
    assert "contact_name" in props and "material" in props


def test_tool_enum_fields_carry_options() -> None:
    tool = qualifier.build_update_lead_tool(default_schema())
    material = tool["input_schema"]["properties"]["material"]
    assert material["type"] == "string"
    assert "quartz" in material["enum"]


def test_tool_reflects_custom_client_schema() -> None:
    schema = QualificationSchema(
        fields=[
            {"key": "pool_shape", "label": "Pool shape", "type": "enum",
             "options": ["kidney", "rectangle", "freeform"], "ask": "What shape?"},
        ]
    )
    tool = qualifier.build_update_lead_tool(schema)
    assert tool["input_schema"]["properties"]["pool_shape"]["enum"] == [
        "kidney", "rectangle", "freeform"
    ]


# ---------------------------------------------------------------------------
# v2 system is context content blocks (business cached)
# ---------------------------------------------------------------------------


def test_v2_system_is_cached_content_blocks() -> None:
    system = qualifier._build_system(
        _make_config(), "v2", default_schema(), state={}, turn_count=2,
        contact=None, timezone="America/Los_Angeles",
    )
    assert isinstance(system, list)
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    # The rules block references the update_lead tool and the "Still needed" list.
    joined = " ".join(b["text"] for b in system)
    assert "Still needed" in joined
    assert "update_lead" in joined


def test_v1_system_is_a_plain_string() -> None:
    system = qualifier._build_system(
        _make_config(brand={"business_name": "Acme"}), "v1", default_schema(),
        state={}, turn_count=0, contact=None, timezone="America/Los_Angeles",
    )
    assert isinstance(system, str)
    assert "Acme" in system


# ---------------------------------------------------------------------------
# Message-history mapping
# ---------------------------------------------------------------------------


def test_to_api_messages_maps_roles_and_drops_leading_assistant() -> None:
    history = [
        _msg("outbound", "Hi, sorry we missed you!"),
        _msg("inbound", "I need new countertops"),
        _msg("outbound", "Great — how many sqft?"),
        _msg("inbound", "About 40"),
    ]
    msgs = qualifier._to_api_messages(history)
    assert len(msgs) == 3
    assert msgs[0] == {"role": "user", "content": "I need new countertops"}
    assert msgs[-1] == {"role": "user", "content": "About 40"}


# ---------------------------------------------------------------------------
# qualifier_turn (Anthropic client mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qualifier_turn_none_without_api_key() -> None:
    with patch("app.prompts.qualifier.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = ""
        result = await qualifier.qualifier_turn(
            _make_config(), [_msg("inbound", "hi")], default_schema(), {}, 1
        )
    assert result is None


@pytest.mark.asyncio
async def test_qualifier_turn_returns_text_extracted_and_version() -> None:
    history = [_msg("inbound", "I need 40 sqft of countertop, this month")]
    response = _fake_response(
        _text_block("Got it! What's your zip code?"),
        _tool_block({"service_type": "countertop", "scope_size": 40, "timeframe": "this_month"}),
    )
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(return_value=response)

    with (
        patch("app.prompts.qualifier.get_settings") as mock_settings,
        patch("app.prompts.qualifier.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await qualifier.qualifier_turn(
            _make_config(), history, default_schema(), {}, 1
        )

    assert result is not None
    reply, extracted, version = result
    assert reply == "Got it! What's your zip code?"
    assert extracted == {"service_type": "countertop", "scope_size": 40, "timeframe": "this_month"}
    assert version == "v2"  # default is now v2
    assert mock_client.messages.create.call_args.kwargs["model"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_qualifier_turn_none_on_api_error() -> None:
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(side_effect=Exception("api down"))
    with (
        patch("app.prompts.qualifier.get_settings") as mock_settings,
        patch("app.prompts.qualifier.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await qualifier.qualifier_turn(
            _make_config(), [_msg("inbound", "hi")], default_schema(), {}, 1
        )
    assert result is None


# ---------------------------------------------------------------------------
# Tool-use continuation loop
#
# Regression cover for the 2026-07-22 prod incident: the model returned ONLY an
# `update_lead` tool_use block (no text) — normal Messages API behavior — so the
# turn extracted the ZIP but produced no reply. Nothing was sent, the session
# didn't terminate, and the caller waited forever.
# ---------------------------------------------------------------------------


async def _run_turn(mock_client: Mock, history: list[Message] | None = None):
    with (
        patch("app.prompts.qualifier.get_settings") as mock_settings,
        patch("app.prompts.qualifier.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        return await qualifier.qualifier_turn(
            _make_config(),
            history or [_msg("inbound", "89145")],
            default_schema(),
            {},
            3,
        )


@pytest.mark.asyncio
async def test_tool_use_without_text_continues_until_reply() -> None:
    """The exact prod failure: tool_use with no text must not dead-end."""
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(
        side_effect=[
            _fake_response(_tool_block({"zip": "89145"})),          # tool only
            _fake_response(_text_block("Thanks! Roughly how many sqft?")),
        ]
    )

    result = await _run_turn(mock_client)

    assert result is not None
    reply, extracted, _ = result
    assert reply == "Thanks! Roughly how many sqft?", "no reply sent — caller left hanging"
    assert extracted == {"zip": "89145"}, "extraction from the first round must survive"
    assert mock_client.messages.create.await_count == 2


@pytest.mark.asyncio
async def test_continuation_sends_tool_result_for_every_tool_use() -> None:
    """The follow-up request must echo the assistant turn and answer each
    tool_use id — the API rejects a partial tool_result set."""
    tool = _tool_block({"zip": "89145"})
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(
        side_effect=[_fake_response(tool), _fake_response(_text_block("ok"))]
    )

    await _run_turn(mock_client)

    second_call = mock_client.messages.create.await_args_list[1].kwargs["messages"]
    assert second_call[-2]["role"] == "assistant"
    results = second_call[-1]
    assert results["role"] == "user"
    assert [b["type"] for b in results["content"]] == ["tool_result"]
    assert results["content"][0]["tool_use_id"] == tool.id


@pytest.mark.asyncio
async def test_text_alongside_tool_use_does_not_loop() -> None:
    """The common case: text + tool_use in one response. Looping again would
    send the caller a second, duplicate SMS."""
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(
        return_value=_fake_response(
            _text_block("Great choice! What ZIP is the project in?"),
            _tool_block({"service_type": "flooring"}),
        )
    )

    result = await _run_turn(mock_client)

    assert result is not None
    reply, extracted, _ = result
    assert reply == "Great choice! What ZIP is the project in?"
    assert extracted == {"service_type": "flooring"}
    assert mock_client.messages.create.await_count == 1


@pytest.mark.asyncio
async def test_tool_only_responses_stop_at_round_cap() -> None:
    """A model that never writes text must not spin forever."""
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(
        return_value=_fake_response(_tool_block({"zip": "89145"}))
    )

    result = await _run_turn(mock_client)

    assert result is not None
    reply, extracted, _ = result
    assert reply == ""
    assert extracted == {"zip": "89145"}
    assert mock_client.messages.create.await_count == qualifier._MAX_TOOL_ROUNDS
