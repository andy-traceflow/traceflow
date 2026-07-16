"""Rolling conversation summary tests (Slice 4)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.models.client_config import ClientConfig
from app.models.contact import Contact, ContactType, ContactTypeSource
from app.models.lead import Lead
from app.models.message import Message
from app.models.qualification import default_schema
from app.prompts import summarize


def _config(**overrides: Any) -> ClientConfig:
    base = {"client_id": uuid4(), "ai_period_resets_at": datetime.now(UTC), "updated_at": datetime.now(UTC)}
    base.update(overrides)
    return ClientConfig(**base)


def _contact(contact_type: ContactType = ContactType.unknown) -> Contact:
    now = datetime.now(UTC)
    return Contact(
        id=uuid4(), client_id=uuid4(), phone="+15551112222", contact_type=contact_type,
        first_seen_at=now, last_seen_at=now, updated_at=now,
    )


def _lead() -> Lead:
    now = datetime.now(UTC)
    return Lead(id=uuid4(), client_id=uuid4(), source_system="x", raw_payload={},
                created_at=now, updated_at=now)


def _msg(direction: str, body: str) -> Message:
    return Message(id=uuid4(), client_id=uuid4(), lead_id=uuid4(),
                   direction=direction, channel="sms", body=body, created_at=datetime.now(UTC))


def _tool_response(summary: str, person_facts: dict[str, Any]) -> Mock:
    block = Mock(type="tool_use", input={"summary": summary, "person_facts": person_facts})
    block.name = "record_summary"
    resp = Mock()
    resp.content = [block]
    return resp


# ---------------------------------------------------------------------------
# summarize_conversation (client mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_none_without_api_key() -> None:
    with patch("app.prompts.summarize.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = ""
        result = await summarize.summarize_conversation(
            _config(), _contact(), _lead(), [_msg("inbound", "hi")]
        )
    assert result is None


@pytest.mark.asyncio
async def test_summarize_returns_summary_and_person_facts() -> None:
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(
        return_value=_tool_response("Homeowner pricing a quartz kitchen.", {"zip": "89101"})
    )
    with (
        patch("app.prompts.summarize.get_settings") as mock_settings,
        patch("app.prompts.summarize.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await summarize.summarize_conversation(
            _config(), _contact(), _lead(), [_msg("inbound", "quartz kitchen, 89101")]
        )
    assert result == ("Homeowner pricing a quartz kitchen.", {"zip": "89101"})


@pytest.mark.asyncio
async def test_summarize_none_on_error() -> None:
    mock_client = Mock()
    mock_client.messages.create = AsyncMock(side_effect=Exception("down"))
    with (
        patch("app.prompts.summarize.get_settings") as mock_settings,
        patch("app.prompts.summarize.get_anthropic_client", return_value=mock_client),
    ):
        mock_settings.return_value.anthropic_api_key = "sk-ant-test"
        result = await summarize.summarize_conversation(
            _config(), _contact(), _lead(), [_msg("inbound", "hi")]
        )
    assert result is None


# ---------------------------------------------------------------------------
# persist_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_writes_summary_and_promotes_unknown() -> None:
    conn = AsyncMock()
    contact = _contact(ContactType.unknown)
    with patch("app.prompts.summarize.set_contact_type", new=AsyncMock()) as set_type:
        await summarize.persist_summary(
            conn, contact, "A durable summary.", {"zip": "89101"}, default_schema()
        )
    # Summary written.
    assert any("UPDATE contacts SET summary" in c.args[0] for c in conn.execute.await_args_list)
    # Promoted unknown → prospect.
    assert set_type.await_args.args[2] == ContactType.prospect
    assert set_type.await_args.args[3] == ContactTypeSource.inferred


@pytest.mark.asyncio
async def test_persist_does_not_repromote_known_contact() -> None:
    conn = AsyncMock()
    contact = _contact(ContactType.customer)
    with patch("app.prompts.summarize.set_contact_type", new=AsyncMock()) as set_type:
        await summarize.persist_summary(conn, contact, "s", {}, default_schema())
    set_type.assert_not_called()  # a customer is not demoted/re-typed
