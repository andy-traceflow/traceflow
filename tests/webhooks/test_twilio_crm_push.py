"""Tests for the automatic crm_push step in the Twilio qualifier flow.

`_maybe_push_to_crm` is the crm_push stage (workflow-schema Section 3): when
the qualifier moves a lead to a qualified state, it lands in the client's CRM.
These tests mock the tenant DB context and the adapter so they run offline and
assert the graceful-degradation contract — no CRM, unknown provider, already
pushed, and push failure must never raise into the caller.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.client_config import ClientConfig
from app.webhooks.twilio import _maybe_push_to_crm


class _FakeConn:
    """Records the SQL it was asked to run; returns a preset lead row."""

    def __init__(self, lead_row: dict[str, Any] | None) -> None:
        self._lead_row = lead_row
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return self._lead_row

    async def execute(self, query: str, *args: Any) -> None:
        self.executed.append((query, args))


def _ctx_factory(conn: _FakeConn):
    @contextlib.asynccontextmanager
    async def _ctx(_client_id):
        yield conn

    return _ctx


def _lead_row(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": uuid4(),
        "client_id": uuid4(),
        "external_id": None,
        "crm_external_id": None,
        "source_system": "twilio_missed_call",
        "contact_name": "Jane Doe",
        "contact_company": None,
        "phone": "+15551234567",
        "email": None,
        "address": None,
        "service_type": "countertops",
        "sqft": None,
        "budget_range": "15k-50k",
        "timeframe": "asap",
        "qualification_status": "qualified",
        "qualification_score": None,
        "classification": "potential_lead",
        "notes": "",
        "raw_payload": {},
        "created_at": datetime.now(UTC),
        "qualified_at": None,
        "pushed_to_crm_at": None,
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return base


def _config(client_id, provider: str | None = "hubspot") -> ClientConfig:
    return ClientConfig(
        client_id=client_id,
        crm_provider=provider,
        ai_period_resets_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_no_crm_provider_is_noop():
    client_id = uuid4()
    ctx = MagicMock()
    with patch("app.webhooks.twilio.set_tenant_context", ctx), \
         patch("app.webhooks.twilio.get_adapter") as get_adapter:
        await _maybe_push_to_crm(client_id, uuid4(), _config(client_id, provider=None))
    get_adapter.assert_not_called()
    ctx.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_provider_is_noop():
    client_id = uuid4()
    ctx = MagicMock()
    with patch("app.webhooks.twilio.set_tenant_context", ctx), \
         patch("app.webhooks.twilio.get_adapter", side_effect=ValueError("Unknown CRM provider: nope")):
        await _maybe_push_to_crm(client_id, uuid4(), _config(client_id, provider="nope"))
    ctx.assert_not_called()  # degraded out before touching the DB


@pytest.mark.asyncio
async def test_happy_path_pushes_and_records():
    client_id = uuid4()
    lead_id = uuid4()
    conn = _FakeConn(_lead_row(id=lead_id, client_id=client_id, external_id=None))
    adapter = MagicMock()
    adapter.push_lead = AsyncMock(return_value="hs-contact-1")

    with patch("app.webhooks.twilio.set_tenant_context", _ctx_factory(conn)), \
         patch("app.webhooks.twilio.get_adapter", return_value=adapter):
        await _maybe_push_to_crm(client_id, lead_id, _config(client_id))

    adapter.push_lead.assert_awaited_once()
    sql = " ".join(q for q, _ in conn.executed)
    assert "UPDATE leads SET crm_external_id" in sql
    assert "crm_pushed" in sql
    assert "crm_push_failed" not in sql


@pytest.mark.asyncio
async def test_missed_call_lead_with_source_external_id_still_pushes():
    """Regression: a missed-call lead carries the Twilio CallSid in external_id
    but no crm_external_id — it must still push (the old guard on external_id
    skipped every missed-call lead). See migration 026."""
    client_id = uuid4()
    lead_id = uuid4()
    conn = _FakeConn(
        _lead_row(
            id=lead_id, client_id=client_id,
            external_id="CAe9a01d90da9294fbaac79ad3835141b6",  # CallSid
            crm_external_id=None,
        )
    )
    adapter = MagicMock()
    adapter.push_lead = AsyncMock(return_value="hs-contact-42")

    with patch("app.webhooks.twilio.set_tenant_context", _ctx_factory(conn)), \
         patch("app.webhooks.twilio.get_adapter", return_value=adapter):
        await _maybe_push_to_crm(client_id, lead_id, _config(client_id))

    adapter.push_lead.assert_awaited_once()
    sql = " ".join(q for q, _ in conn.executed)
    assert "UPDATE leads SET crm_external_id" in sql
    assert "crm_pushed" in sql


@pytest.mark.asyncio
async def test_already_pushed_is_noop():
    client_id = uuid4()
    lead_id = uuid4()
    conn = _FakeConn(_lead_row(id=lead_id, client_id=client_id, crm_external_id="EXT-EXISTS"))
    adapter = MagicMock()
    adapter.push_lead = AsyncMock(return_value="should-not-be-called")

    with patch("app.webhooks.twilio.set_tenant_context", _ctx_factory(conn)), \
         patch("app.webhooks.twilio.get_adapter", return_value=adapter):
        await _maybe_push_to_crm(client_id, lead_id, _config(client_id))

    adapter.push_lead.assert_not_called()
    assert conn.executed == []  # nothing written for an already-synced lead


@pytest.mark.asyncio
async def test_push_failure_records_event_and_does_not_raise():
    client_id = uuid4()
    lead_id = uuid4()
    conn = _FakeConn(_lead_row(id=lead_id, client_id=client_id, external_id=None))
    adapter = MagicMock()
    adapter.push_lead = AsyncMock(side_effect=RuntimeError("hubspot 500"))

    with patch("app.webhooks.twilio.set_tenant_context", _ctx_factory(conn)), \
         patch("app.webhooks.twilio.get_adapter", return_value=adapter):
        await _maybe_push_to_crm(client_id, lead_id, _config(client_id))

    sql = " ".join(q for q, _ in conn.executed)
    assert "crm_push_failed" in sql
    assert "UPDATE leads SET crm_external_id" not in sql  # lead row left untouched
