"""Twilio missed-call + SMS-reply flow tests.

Covers the X-Twilio-Signature verifier, the SMS sender's no-op guards, greeting
rendering, and the two webhook handlers' orchestration. The DB layer
(set_tenant_context), the classifier (classify_caller / resolve_contact), and
the SMS layer (send_sms) are mocked, so the whole suite runs offline. The
classification tree itself is covered in tests/services/test_classification.py.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.client_config import ClientConfig
from app.models.contact import Contact, ContactType
from app.models.lead import Classification
from app.prompts.intent import Intent
from app.services import dedupe
from app.services.classification import ClassificationResult, Route
from app.services.sms import send_sms
from app.services.twilio_signature import compute_twilio_signature, verify_twilio_signature
from app.webhooks import twilio as twilio_webhook

AUTH_TOKEN = "test-twilio-auth-token"


@pytest.fixture(autouse=True)
def _clear_dedupe():
    dedupe.reset()
    yield
    dedupe.reset()


@pytest.fixture(autouse=True)
def _dev_twilio_settings():
    """Force the middleware's Twilio branch into dev mode (no auth token →
    signature check skipped) so route tests need no real signature."""
    fake = Mock()
    fake.twilio_auth_token = ""
    fake.is_production = False
    with patch("app.services.webhook_signature.get_settings", return_value=fake):
        yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Twilio signature
# ---------------------------------------------------------------------------

def _twilio_sign(token: str, url: str, params: dict[str, str]) -> str:
    """Independent reimplementation of Twilio's signing algorithm."""
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(
        hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()
    ).decode()


def test_signature_accepts_valid():
    url = "https://traceflow.app/webhooks/twilio/missed-call/abc"
    params = {"CallSid": "CA1", "From": "+15551112222"}
    sig = _twilio_sign(AUTH_TOKEN, url, params)
    assert verify_twilio_signature(AUTH_TOKEN, url, params, sig) is True


def test_signature_rejects_tampered_params():
    url = "https://traceflow.app/webhooks/twilio/missed-call/abc"
    params = {"CallSid": "CA1", "From": "+15551112222"}
    sig = _twilio_sign(AUTH_TOKEN, url, params)
    tampered = {"CallSid": "CA1", "From": "+19998887777"}
    assert verify_twilio_signature(AUTH_TOKEN, url, tampered, sig) is False


def test_signature_rejects_wrong_token():
    url = "https://traceflow.app/webhooks/twilio/missed-call/abc"
    params = {"CallSid": "CA1"}
    sig = _twilio_sign(AUTH_TOKEN, url, params)
    assert verify_twilio_signature("wrong-token", url, params, sig) is False


def test_signature_rejects_empty_inputs():
    url = "https://traceflow.app/x"
    assert verify_twilio_signature("", url, {}, "sig") is False
    assert verify_twilio_signature(AUTH_TOKEN, url, {}, "") is False


def test_compute_signature_is_param_order_independent():
    url = "https://traceflow.app/x"
    a = compute_twilio_signature(AUTH_TOKEN, url, {"a": "1", "b": "2"})
    b = compute_twilio_signature(AUTH_TOKEN, url, {"b": "2", "a": "1"})
    assert a == b


# ---------------------------------------------------------------------------
# Greeting rendering
# ---------------------------------------------------------------------------

def _make_config(**overrides: Any) -> ClientConfig:
    base = {
        "client_id": uuid4(),
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ClientConfig(**base)


def test_render_greeting_uses_template_with_business_name():
    config = _make_config(
        greeting_template="Hey, this is {business_name}. Sorry we missed you!",
        brand={"business_name": "Acme Surfaces"},
    )
    assert (
        twilio_webhook._render_greeting(config)
        == "Hey, this is Acme Surfaces. Sorry we missed you!"
    )


def test_render_greeting_default_when_no_template():
    config = _make_config(brand={"business_name": "Acme Surfaces"})
    assert twilio_webhook._render_greeting(config).startswith(
        "Hi! Thanks for calling Acme Surfaces —"
    )


def test_render_greeting_business_name_fallback():
    config = _make_config()  # no brand configured
    assert twilio_webhook._render_greeting(config).startswith("Hi! Thanks for calling us —")


# ---------------------------------------------------------------------------
# send_sms — no-op guards (never block the leads pipeline)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_sms_noop_without_credentials():
    with patch("app.services.sms.get_settings") as mock_settings:
        mock_settings.return_value.twilio_account_sid = ""
        mock_settings.return_value.twilio_auth_token = ""
        result = await send_sms(to="+15551112222", body="hi", from_number="+15559998888")
    assert result is None


@pytest.mark.asyncio
async def test_send_sms_noop_without_from_number():
    with patch("app.services.sms.get_settings") as mock_settings:
        mock_settings.return_value.twilio_account_sid = "AC123"
        mock_settings.return_value.twilio_auth_token = "tok"
        result = await send_sms(to="+15551112222", body="hi", from_number="")
    assert result is None


@pytest.mark.asyncio
async def test_send_sms_noop_without_recipient():
    with patch("app.services.sms.get_settings") as mock_settings:
        mock_settings.return_value.twilio_account_sid = "AC123"
        mock_settings.return_value.twilio_auth_token = "tok"
        result = await send_sms(to="", body="hi", from_number="+15559998888")
    assert result is None


# ---------------------------------------------------------------------------
# missed_call_webhook route — background processing patched out
# ---------------------------------------------------------------------------

def test_missed_call_schedules_processing(client):
    client_id = uuid4()
    with patch("app.webhooks.twilio._process_missed_call", new=AsyncMock()) as mock_proc:
        resp = client.post(
            f"/webhooks/twilio/missed-call/{client_id}",
            data={"CallSid": "CA-1", "From": "+15551112222", "CallStatus": "no-answer"},
        )
    assert resp.status_code == 200
    mock_proc.assert_called_once()
    args = mock_proc.call_args.args
    assert str(args[0]) == str(client_id)   # client_id threaded to the task
    assert args[1]["CallSid"] == "CA-1"     # full payload threaded to the task


def test_missed_call_dedupes_on_call_sid(client):
    client_id = uuid4()
    with patch("app.webhooks.twilio._process_missed_call", new=AsyncMock()) as mock_proc:
        first = client.post(
            f"/webhooks/twilio/missed-call/{client_id}",
            data={"CallSid": "CA-dup", "From": "+15551112222"},
        )
        second = client.post(
            f"/webhooks/twilio/missed-call/{client_id}",
            data={"CallSid": "CA-dup", "From": "+15551112222"},
        )
    assert first.status_code == 200
    assert second.status_code == 200
    mock_proc.assert_called_once()  # the retry did not schedule a second time


# ---------------------------------------------------------------------------
# _process_missed_call — orchestration (DB + SMS + classifier mocked)
# ---------------------------------------------------------------------------

def _fake_tenant_ctx(conn):
    @asynccontextmanager
    async def _ctx(client_id):
        yield conn

    return _ctx


def _config_row(client_id, **overrides):
    row = {
        "client_id": client_id,
        "twilio_number": "+15559998888",
        "greeting_template": None,
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    row.update(overrides)
    return row


def _contact(contact_type: ContactType = ContactType.unknown, **overrides: Any) -> Contact:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid4(),
        "client_id": uuid4(),
        "phone": "+15551112222",
        "contact_type": contact_type,
        "known_facts": {},
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return Contact(**base)


def _result(
    route: Route,
    classification: Classification,
    *,
    should_text: bool = True,
    contact: Contact | None = None,
    reason: str = "test",
    existing_lead_id: Any = None,
    is_returning: bool = False,
) -> ClassificationResult:
    return ClassificationResult(
        route,
        classification,
        should_text=should_text,
        reason=reason,
        contact=contact if contact is not None else _contact(),
        existing_lead_id=existing_lead_id,
        is_returning=is_returning,
    )


def _potential(contact: Contact | None = None) -> ClassificationResult:
    return _result(Route.potential_lead, Classification.potential_lead, contact=contact)


@pytest.mark.asyncio
async def test_process_missed_call_falls_back_to_template_greeting():
    """No AI result → static template greeting; ai_generated False, cap untouched."""
    client_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), {"id": uuid4()}]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.webhooks.twilio.classify_caller", new=AsyncMock(return_value=_potential())),
        patch("app.webhooks.twilio.generate_greeting", new=AsyncMock(return_value=None)),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM-1"})) as mock_sms,
    ):
        await twilio_webhook._process_missed_call(
            client_id, {"CallSid": "CA-9", "From": "+15551112222"}
        )

    mock_sms.assert_called_once()
    assert mock_sms.call_args.kwargs["to"] == "+15551112222"
    assert mock_sms.call_args.kwargs["from_number"] == "+15559998888"

    calls = conn.execute.call_args_list
    sqls = [c.args[0] for c in calls]
    assert any("INSERT INTO messages" in s for s in sqls)
    assert any("greeting_sms_sent" in s for s in sqls)
    assert not any("UPDATE client_configs" in s for s in sqls)  # template path does not bill AI

    msg_call = next(c for c in calls if "INSERT INTO messages" in c.args[0])
    assert msg_call.args[4] is False   # ai_generated
    assert msg_call.args[5] is None    # prompt_version


@pytest.mark.asyncio
async def test_process_missed_call_skips_sms_without_twilio_number():
    client_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id, twilio_number=None), {"id": uuid4()}]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.webhooks.twilio.classify_caller", new=AsyncMock(return_value=_potential())),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock()) as mock_sms,
    ):
        await twilio_webhook._process_missed_call(
            client_id, {"CallSid": "CA-9", "From": "+15551112222"}
        )

    mock_sms.assert_not_called()


@pytest.mark.asyncio
async def test_process_missed_call_records_failure_when_sms_fails():
    client_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), {"id": uuid4()}]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.webhooks.twilio.classify_caller", new=AsyncMock(return_value=_potential())),
        patch("app.webhooks.twilio.generate_greeting", new=AsyncMock(return_value=None)),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value=None)),
    ):
        await twilio_webhook._process_missed_call(
            client_id, {"CallSid": "CA-9", "From": "+15551112222"}
        )

    insert_sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("greeting_sms_failed" in s for s in insert_sqls)
    assert not any("INSERT INTO messages" in s for s in insert_sqls)


@pytest.mark.asyncio
async def test_process_missed_call_aborts_without_client_config():
    client_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [None]  # client_configs lookup finds nothing

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock()) as mock_sms,
    ):
        await twilio_webhook._process_missed_call(
            client_id, {"CallSid": "CA-9", "From": "+15551112222"}
        )

    mock_sms.assert_not_called()
    conn.execute.assert_not_called()  # no lead, no events written


@pytest.mark.asyncio
async def test_process_missed_call_ai_greeting_records_version_and_bills_cap():
    client_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), {"id": uuid4()}]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.webhooks.twilio.classify_caller", new=AsyncMock(return_value=_potential())),
        patch(
            "app.webhooks.twilio.generate_greeting",
            new=AsyncMock(return_value=("AI greeting!", "v1")),
        ),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM-1"})) as mock_sms,
    ):
        await twilio_webhook._process_missed_call(
            client_id, {"CallSid": "CA-9", "From": "+15551112222"}
        )

    assert mock_sms.call_args.kwargs["body"] == "AI greeting!"  # AI text is the SMS body

    calls = conn.execute.call_args_list
    sqls = [c.args[0] for c in calls]
    assert any("UPDATE client_configs" in s for s in sqls)  # AI interaction billed to the cap

    msg_call = next(c for c in calls if "INSERT INTO messages" in c.args[0])
    assert msg_call.args[4] is True             # ai_generated
    assert msg_call.args[5] == "greeting:v1"    # prompt_version


@pytest.mark.asyncio
async def test_process_missed_call_bumps_contact_call_count():
    client_id = uuid4()
    contact = _contact()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), {"id": uuid4()}]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.webhooks.twilio.classify_caller", new=AsyncMock(return_value=_potential(contact))),
        patch("app.webhooks.twilio.generate_greeting", new=AsyncMock(return_value=None)),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM-1"})),
    ):
        await twilio_webhook._process_missed_call(
            client_id, {"CallSid": "CA-9", "From": "+15551112222"}
        )

    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("UPDATE contacts SET call_count = call_count + 1" in s for s in sqls)


# ---------------------------------------------------------------------------
# _process_missed_call — classification routing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_missed_call_active_conversation_records_no_new_lead():
    """A caller with a fresh open lead is logged as a repeat call, not re-greeted."""
    client_id = uuid4()
    existing_lead = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id)]  # only the config lookup
    active = _result(
        Route.active_conversation,
        Classification.potential_lead,
        should_text=False,
        existing_lead_id=existing_lead,
    )

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.webhooks.twilio.classify_caller", new=AsyncMock(return_value=active)),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock()) as mock_sms,
    ):
        await twilio_webhook._process_missed_call(
            client_id, {"CallSid": "CA-9", "From": "+15551112222"}
        )

    mock_sms.assert_not_called()
    assert conn.fetchrow.call_count == 1  # config only — no lead INSERT
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("missed_call_during_active_conversation" in s for s in sqls)
    assert not any("INSERT INTO leads" in s for s in sqls)


@pytest.mark.asyncio
async def test_process_missed_call_resumed_conversation_reuses_lead():
    """A stale-open lead is resumed: the same lead is reused (no new INSERT) and
    the caller is re-greeted."""
    client_id = uuid4()
    existing_lead = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id)]  # config only; lead reused
    resumed = _result(
        Route.resumed_conversation,
        Classification.potential_lead,
        should_text=True,
        existing_lead_id=existing_lead,
        is_returning=True,
        reason="resumed_conversation",
    )

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.webhooks.twilio.classify_caller", new=AsyncMock(return_value=resumed)),
        patch("app.webhooks.twilio.generate_greeting", new=AsyncMock(return_value=None)),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM-r"})) as mock_sms,
    ):
        await twilio_webhook._process_missed_call(
            client_id, {"CallSid": "CA-9", "From": "+15551112222"}
        )

    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("conversation_resumed" in s for s in sqls)
    assert not any("INSERT INTO leads" in s for s in sqls)  # reused — no duplicate
    mock_sms.assert_called_once()  # re-greeted


@pytest.mark.asyncio
async def test_process_missed_call_returning_contact_seeds_facts():
    """A returning contact gets a NEW lead pre-populated with person-scoped facts."""
    client_id = uuid4()
    contact = _contact(
        ContactType.prospect, known_facts={"contact_name": "Maria", "address": "1 Elm St"}
    )
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), {"id": uuid4()}]
    returning = _result(
        Route.returning_contact,
        Classification.potential_lead,
        should_text=True,
        contact=contact,
        is_returning=True,
        reason="returning_contact",
    )

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.webhooks.twilio.classify_caller", new=AsyncMock(return_value=returning)),
        patch("app.webhooks.twilio.generate_greeting", new=AsyncMock(return_value=None)),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM"})),
    ):
        await twilio_webhook._process_missed_call(
            client_id, {"CallSid": "CA-9", "From": "+15551112222"}
        )

    lead_insert = next(
        c for c in conn.fetchrow.call_args_list if "INSERT INTO leads" in c.args[0]
    )
    assert lead_insert.args[8] == "Maria"      # contact_name seeded from known_facts
    assert lead_insert.args[9] == "1 Elm St"   # address seeded from known_facts
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("returning_contact" in s for s in sqls)


@pytest.mark.asyncio
async def test_process_missed_call_suppresses_greeting_for_known_non_lead():
    """A vendor (known_non_lead, should_text False) still gets a lead row +
    suppression event, but no AI greeting and no SMS."""
    client_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), {"id": uuid4()}]
    suppressed = _result(
        Route.known_non_lead,
        Classification.known_non_lead,
        should_text=False,
        reason="vendor_allowlist",
    )

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.webhooks.twilio.classify_caller", new=AsyncMock(return_value=suppressed)),
        patch("app.webhooks.twilio.generate_greeting", new=AsyncMock()) as mock_greet,
        patch("app.webhooks.twilio.send_sms", new=AsyncMock()) as mock_sms,
    ):
        await twilio_webhook._process_missed_call(
            client_id, {"CallSid": "CA-9", "From": "+15551112222"}
        )

    mock_sms.assert_not_called()
    mock_greet.assert_not_called()  # no AI billed for a suppressed greeting

    lead_insert = next(
        c for c in conn.fetchrow.call_args_list if "INSERT INTO leads" in c.args[0]
    )
    assert lead_insert.args[6] == "known_non_lead"  # classification persisted

    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("greeting_suppressed" in s for s in sqls)
    assert not any("INSERT INTO messages" in s for s in sqls)


@pytest.mark.asyncio
async def test_process_missed_call_alerts_on_existing_customer():
    """An existing customer reaching voicemail alerts the business and (with
    should_text True) is still texted."""
    client_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), {"id": uuid4()}]

    contact = _contact(ContactType.customer, name="Repeat Client")
    existing = _result(
        Route.existing_customer,
        Classification.existing_customer,
        should_text=True,
        contact=contact,
        reason="resolved_customer",
    )
    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.webhooks.twilio.classify_caller", new=AsyncMock(return_value=existing)),
        patch("app.webhooks.twilio.alert_existing_customer", new=AsyncMock(return_value=True)) as mock_alert,
        patch("app.webhooks.twilio.generate_greeting", new=AsyncMock(return_value=None)),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM-1"})) as mock_sms,
    ):
        await twilio_webhook._process_missed_call(
            client_id, {"CallSid": "CA-9", "From": "+15551112222"}
        )

    mock_alert.assert_awaited_once()
    assert "Repeat Client" in mock_alert.call_args.kwargs["summary"]
    mock_sms.assert_called_once()  # still texted when should_text is True


@pytest.mark.asyncio
async def test_process_missed_call_alerts_existing_customer_even_when_text_suppressed():
    """text_existing_customers=False suppresses the caller SMS, but a known
    customer at voicemail is a priority service event — the business is still
    alerted."""
    client_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), {"id": uuid4()}]

    contact = _contact(ContactType.customer, name="Repeat Client")
    suppressed = _result(
        Route.existing_customer,
        Classification.existing_customer,
        should_text=False,
        contact=contact,
        reason="resolved_customer",
    )
    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.webhooks.twilio.classify_caller", new=AsyncMock(return_value=suppressed)),
        patch("app.webhooks.twilio.alert_existing_customer", new=AsyncMock(return_value=True)) as mock_alert,
        patch("app.webhooks.twilio.send_sms", new=AsyncMock()) as mock_sms,
    ):
        await twilio_webhook._process_missed_call(
            client_id, {"CallSid": "CA-9", "From": "+15551112222"}
        )

    mock_alert.assert_awaited_once()  # business alerted regardless
    mock_sms.assert_not_called()      # caller not texted
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("greeting_suppressed" in s for s in sqls)


# ---------------------------------------------------------------------------
# sms_reply_webhook route + _process_sms_reply orchestration
# ---------------------------------------------------------------------------

def _message_row(direction: str, body: str) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "client_id": uuid4(),
        "lead_id": uuid4(),
        "direction": direction,
        "channel": "sms",
        "body": body,
        "ai_generated": False,
        "prompt_version": None,
        "raw_payload": None,
        "created_at": datetime.now(UTC),
    }


def _lead_row(lead_id: Any, **overrides: Any) -> dict[str, Any]:
    """A full leads row — the handler builds a Lead from it (SELECT * / RETURNING *)."""
    now = datetime.now(UTC)
    row: dict[str, Any] = {
        "id": lead_id,
        "client_id": uuid4(),
        "external_id": None,
        "source_system": "twilio_missed_call",
        "contact_id": uuid4(),
        "contact_name": None,
        "contact_company": None,
        "phone": "+15551112222",
        "email": None,
        "address": None,
        "service_type": None,
        "sqft": None,
        "budget_range": None,
        "timeframe": None,
        "qualification_status": "unqualified",
        "qualification_score": None,
        "value_score": None,
        "qualification_data": {},
        "classification": "potential_lead",
        "outcome": "open",
        "recovered_value": None,
        "outcome_source": None,
        "outcome_recorded_at": None,
        "notes": "",
        "raw_payload": {},
        "last_inbound_at": None,
        "last_outbound_at": None,
        "turn_count": 0,
        "created_at": now,
        "qualified_at": None,
        "pushed_to_crm_at": None,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def _patch_resolve(contact: Contact | None = None):
    return patch(
        "app.webhooks.twilio.resolve_contact",
        new=AsyncMock(return_value=contact if contact is not None else _contact()),
    )


def test_sms_reply_schedules_processing(client):
    client_id = uuid4()
    with patch("app.webhooks.twilio._process_sms_reply", new=AsyncMock()) as mock_proc:
        resp = client.post(
            f"/webhooks/twilio/sms-reply/{client_id}",
            data={"From": "+15551112222", "Body": "hello", "MessageSid": "MM-route-1"},
        )
    assert resp.status_code == 200
    mock_proc.assert_called_once()


def test_sms_reply_dedupes_on_message_sid(client):
    client_id = uuid4()
    with patch("app.webhooks.twilio._process_sms_reply", new=AsyncMock()) as mock_proc:
        first = client.post(
            f"/webhooks/twilio/sms-reply/{client_id}",
            data={"From": "+15551112222", "Body": "hi", "MessageSid": "MM-dup"},
        )
        second = client.post(
            f"/webhooks/twilio/sms-reply/{client_id}",
            data={"From": "+15551112222", "Body": "hi again", "MessageSid": "MM-dup"},
        )
    assert first.status_code == 200
    assert second.status_code == 200
    mock_proc.assert_called_once()  # the retry did not schedule a second time


@pytest.mark.asyncio
async def test_process_sms_reply_runs_qualifier_and_applies_fields():
    client_id = uuid4()
    lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), _lead_row(lead_id)]
    conn.fetchval.return_value = False
    conn.fetch.return_value = [
        _message_row("outbound", "Hi, sorry we missed you!"),
        _message_row("inbound", "I need countertops"),
    ]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(),
        patch("app.webhooks.twilio.classify_intent", new=AsyncMock(return_value=Intent.sales)),
        patch(
            "app.webhooks.twilio.qualifier_turn",
            new=AsyncMock(return_value=("What's your zip?", {"service_type": "countertop"}, "v1")),
        ),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM-2"})) as mock_sms,
    ):
        await twilio_webhook._process_sms_reply(
            client_id,
            {"From": "+15551112222", "Body": "I need countertops", "MessageSid": "MM-1"},
        )

    assert mock_sms.call_args.kwargs["body"] == "What's your zip?"

    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("INSERT INTO messages" in s and "inbound" in s for s in sqls)  # inbound saved
    assert any("UPDATE leads SET service_type" in s for s in sqls)            # extracted field applied
    assert any("UPDATE client_configs" in s for s in sqls)                    # AI interaction billed
    assert any("qualifier_turn" in s for s in sqls)                           # turn event recorded


@pytest.mark.asyncio
async def test_process_sms_reply_cold_inbound_opens_lead():
    """No open lead + a real contact → open a NEW lead and qualify. The old
    silent-drop of an inbound SMS with no active lead is gone."""
    client_id = uuid4()
    new_lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _config_row(client_id),
        None,  # no open lead
        _lead_row(new_lead_id),  # the freshly-opened lead (INSERT ... RETURNING)
    ]
    conn.fetchval.return_value = False
    conn.fetch.return_value = [_message_row("inbound", "Do you do quartz counters?")]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(),
        patch("app.webhooks.twilio.classify_intent", new=AsyncMock(return_value=Intent.sales)),
        patch(
            "app.webhooks.twilio.qualifier_turn",
            new=AsyncMock(return_value=("What's your zip?", {}, "v1")),
        ),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM"})) as mock_sms,
    ):
        await twilio_webhook._process_sms_reply(
            client_id,
            {"From": "+15551112222", "Body": "Do you do quartz counters?", "MessageSid": "MM-cold"},
        )

    # A lead was opened and the qualifier ran (a reply was sent).
    assert any("INSERT INTO leads" in c.args[0] for c in conn.fetchrow.call_args_list)
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("inbound_sms_lead_opened" in s for s in sqls)
    mock_sms.assert_called()


# ---------------------------------------------------------------------------
# Terminal-lead resume (no lead-splitting) + inbound-only turn budget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_sms_reply_resumes_terminal_lead_within_window():
    """A terminal lead texted back inside the window RESUMES the same lead:
    flip to qualifying, emit lead_resumed, skip the intent gate, no new lead."""
    client_id = uuid4()
    lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _config_row(client_id),
        _lead_row(
            lead_id,
            qualification_status="needs_review",
            within_resume_window=True,
            turn_count=6,
            service_type="tile",
        ),
    ]
    conn.fetchval.return_value = False
    conn.fetch.return_value = [
        _message_row("inbound", "Tile"),
        _message_row("outbound", "What ZIP?"),
        _message_row("inbound", "89145"),
    ]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(),
        patch("app.webhooks.twilio.classify_intent", new=AsyncMock()) as mock_intent,
        patch(
            "app.webhooks.twilio.qualifier_turn",
            new=AsyncMock(return_value=("How many square feet?", {}, "v1")),
        ),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM-r"})) as mock_sms,
    ):
        await twilio_webhook._process_sms_reply(
            client_id, {"From": "+15551112222", "Body": "89145", "MessageSid": "MM-res"}
        )

    mock_intent.assert_not_awaited()  # resume skips the intent gate entirely
    assert not any("INSERT INTO leads" in c.args[0] for c in conn.fetchrow.call_args_list)
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("UPDATE leads SET qualification_status = 'qualifying'" in s for s in sqls)
    assert any("lead_resumed" in s for s in sqls)
    assert not any("intent_classified" in s for s in sqls)
    mock_sms.assert_called()


@pytest.mark.asyncio
async def test_process_sms_reply_opens_new_lead_outside_window():
    """A terminal lead beyond the window is a genuinely new inquiry → open a
    fresh lead and run the intent gate; no resume."""
    client_id = uuid4()
    old_lead_id = uuid4()
    new_lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _config_row(client_id),
        _lead_row(old_lead_id, qualification_status="qualified", within_resume_window=False),
        _lead_row(new_lead_id),
    ]
    conn.fetchval.return_value = False
    conn.fetch.return_value = [_message_row("inbound", "New bathroom project")]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(),
        patch("app.webhooks.twilio.classify_intent", new=AsyncMock(return_value=Intent.sales)) as mock_intent,
        patch(
            "app.webhooks.twilio.qualifier_turn",
            new=AsyncMock(return_value=("What kind of work?", {}, "v1")),
        ),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM-n"})),
    ):
        await twilio_webhook._process_sms_reply(
            client_id,
            {"From": "+15551112222", "Body": "New bathroom project", "MessageSid": "MM-new"},
        )

    mock_intent.assert_awaited()  # a fresh unqualified lead runs the gate
    assert any("INSERT INTO leads" in c.args[0] for c in conn.fetchrow.call_args_list)
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("inbound_sms_lead_opened" in s for s in sqls)
    assert not any("lead_resumed" in s for s in sqls)


@pytest.mark.asyncio
async def test_process_sms_reply_resume_skips_intent_gate():
    """Resume goes straight to the qualifier: no intent classification, a
    qualifier_turn event is recorded. Also proves resume works from a
    'qualified' terminal status, not just needs_review."""
    client_id = uuid4()
    lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _config_row(client_id),
        _lead_row(
            lead_id,
            qualification_status="qualified",
            within_resume_window=True,
            service_type="tile",
        ),
    ]
    conn.fetchval.return_value = False
    conn.fetch.return_value = [_message_row("inbound", "actually one more thing")]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(),
        patch("app.webhooks.twilio.classify_intent", new=AsyncMock()) as mock_intent,
        patch(
            "app.webhooks.twilio.qualifier_turn",
            new=AsyncMock(return_value=("Sure — what's your ZIP?", {}, "v1")),
        ),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM-s"})),
    ):
        await twilio_webhook._process_sms_reply(
            client_id,
            {"From": "+15551112222", "Body": "actually one more thing", "MessageSid": "MM-skip"},
        )

    mock_intent.assert_not_awaited()
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert not any("intent_classified" in s for s in sqls)
    assert any("qualifier_turn" in s for s in sqls)


@pytest.mark.asyncio
async def test_process_sms_reply_turn_budget_counts_inbound_only():
    """turn_count counts both directions, but only CUSTOMER replies count toward
    max_turns. 5 inbound of 11 messages must NOT force needs_review, even though
    the old turn_count+1 (=12) logic would have."""
    client_id = uuid4()
    lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _config_row(client_id),
        _lead_row(lead_id, qualification_status="qualifying", turn_count=11),
    ]
    conn.fetchval.return_value = False
    conn.fetch.return_value = (
        [_message_row("inbound", f"in {i}") for i in range(5)]
        + [_message_row("outbound", f"out {i}") for i in range(6)]
    )

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(),
        patch("app.webhooks.twilio.classify_intent", new=AsyncMock()) as mock_intent,
        patch(
            "app.webhooks.twilio.qualifier_turn",
            new=AsyncMock(return_value=("What's your ZIP?", {}, "v1")),
        ),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM-b"})) as mock_sms,
    ):
        await twilio_webhook._process_sms_reply(
            client_id, {"From": "+15551112222", "Body": "in 5", "MessageSid": "MM-budget"}
        )

    mock_intent.assert_not_awaited()  # already qualifying → no intent gate
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert not any("needs_review" in s for s in sqls)
    assert mock_sms.call_args.kwargs["body"] == "What's your ZIP?"


@pytest.mark.asyncio
async def test_process_sms_reply_resumed_lead_qualifies():
    """A resumed lead that completes qualification updates the SAME lead to
    'qualified' and pushes to CRM once — no split, no duplicate record."""
    client_id = uuid4()
    lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _config_row(client_id),
        _lead_row(
            lead_id,
            qualification_status="needs_review",
            within_resume_window=True,
            service_type="tile",
            address="89145",
            sqft=250,
            timeframe="asap",
        ),
    ]
    conn.fetchval.return_value = False
    conn.fetch.return_value = [_message_row("inbound", "250")]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(),
        patch("app.webhooks.twilio.classify_intent", new=AsyncMock()),
        patch(
            # Seeded fields already total 65% (service+zip+sqft+timeframe) ≥ 60,
            # so the resumed turn qualifies without any new extraction.
            "app.webhooks.twilio.qualifier_turn",
            new=AsyncMock(return_value=("Got it!", {}, "v1")),
        ),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM-q"})),
        patch("app.webhooks.twilio._maybe_push_to_crm", new=AsyncMock()) as mock_crm,
        patch("app.webhooks.twilio.summarize_conversation", new=AsyncMock(return_value=None)),
    ):
        await twilio_webhook._process_sms_reply(
            client_id, {"From": "+15551112222", "Body": "250", "MessageSid": "MM-q"}
        )

    # Same lead reused (no INSERT), moved to qualified, pushed once.
    assert not any("INSERT INTO leads" in c.args[0] for c in conn.fetchrow.call_args_list)
    status_update = next(
        c for c in conn.execute.call_args_list
        if "UPDATE leads SET qualification_status = $2" in c.args[0]
    )
    assert status_update.args[2] == "qualified"
    mock_crm.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_sms_reply_drops_blocked_contact():
    """A blocked (or spam) contact texting in gets no lead, no reply, no spend."""
    client_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id)]  # config only

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(_contact(ContactType.blocked)),
        patch("app.webhooks.twilio.qualifier_turn", new=AsyncMock()) as mock_qual,
        patch("app.webhooks.twilio.send_sms", new=AsyncMock()) as mock_sms,
    ):
        await twilio_webhook._process_sms_reply(
            client_id, {"From": "+15551112222", "Body": "buy now", "MessageSid": "MM-blk"}
        )

    mock_qual.assert_not_awaited()
    mock_sms.assert_not_called()
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("inbound_sms_dropped" in s for s in sqls)
    assert not any("INSERT INTO leads" in s for s in sqls)


@pytest.mark.asyncio
async def test_process_sms_reply_flags_needs_review_when_qualifier_unavailable():
    client_id = uuid4()
    lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), _lead_row(lead_id)]
    conn.fetch.return_value = [_message_row("inbound", "hello")]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(),
        patch("app.webhooks.twilio.classify_intent", new=AsyncMock(return_value=Intent.sales)),
        patch("app.webhooks.twilio.qualifier_turn", new=AsyncMock(return_value=None)),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock()) as mock_sms,
    ):
        await twilio_webhook._process_sms_reply(
            client_id, {"From": "+15551112222", "Body": "hello", "MessageSid": "MM-3"}
        )

    mock_sms.assert_not_called()
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("needs_review" in s for s in sqls)
    assert any("qualifier_unavailable" in s for s in sqls)


@pytest.mark.asyncio
async def test_process_sms_reply_alerts_owner_on_vip_keyword():
    client_id = uuid4()
    lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        _config_row(client_id, vip_keywords=["emergency"]),
        _lead_row(lead_id),
    ]
    conn.fetchval.return_value = False  # not yet alerted
    conn.fetch.return_value = [_message_row("inbound", "we have an emergency leak")]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(),
        patch("app.webhooks.twilio.classify_intent", new=AsyncMock(return_value=Intent.sales)),
        patch(
            "app.webhooks.twilio.qualifier_turn",
            new=AsyncMock(return_value=("On it!", {}, "v1")),
        ),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM-2"})),
        patch("app.webhooks.twilio.alert_owner", new=AsyncMock(return_value=True)) as mock_alert,
    ):
        await twilio_webhook._process_sms_reply(
            client_id,
            {"From": "+15551112222", "Body": "we have an emergency leak", "MessageSid": "MM-vip"},
        )

    mock_alert.assert_awaited_once()
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("owner_alert_sent" in s for s in sqls)


# ---------------------------------------------------------------------------
# Intent gate — first-reply routing off the sales track
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_sms_reply_existing_customer_intent_routes_support_touch():
    """First reply reads as an existing customer → alert the owner, mark
    support_touch, and skip the qualifier entirely."""
    client_id = uuid4()
    lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), _lead_row(lead_id)]
    conn.fetch.return_value = [_message_row("inbound", "you did my kitchen last year")]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(),
        patch(
            "app.webhooks.twilio.classify_intent",
            new=AsyncMock(return_value=Intent.existing_customer),
        ),
        patch("app.webhooks.twilio.alert_existing_customer", new=AsyncMock(return_value=True)) as mock_alert,
        patch("app.webhooks.twilio.qualifier_turn", new=AsyncMock()) as mock_qual,
        patch("app.webhooks.twilio.send_sms", new=AsyncMock()) as mock_sms,
    ):
        await twilio_webhook._process_sms_reply(
            client_id,
            {"From": "+15551112222", "Body": "you did my kitchen last year", "MessageSid": "MM-ec"},
        )

    mock_alert.assert_awaited_once()      # owner alerted
    mock_qual.assert_not_awaited()        # qualifier skipped
    mock_sms.assert_not_called()          # caller not texted by a bot
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("support_touch" in s for s in sqls)
    assert any("intent_classified" in s for s in sqls)


@pytest.mark.asyncio
async def test_process_sms_reply_non_lead_intent_marks_contact():
    client_id = uuid4()
    lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), _lead_row(lead_id)]
    conn.fetch.return_value = [_message_row("inbound", "Hi, I sell countertop slabs wholesale")]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(),
        patch("app.webhooks.twilio.classify_intent", new=AsyncMock(return_value=Intent.non_lead)),
        patch("app.webhooks.twilio.qualifier_turn", new=AsyncMock()) as mock_qual,
        patch("app.webhooks.twilio.send_sms", new=AsyncMock()) as mock_sms,
    ):
        await twilio_webhook._process_sms_reply(
            client_id,
            {"From": "+15551112222", "Body": "I sell slabs wholesale", "MessageSid": "MM-nl"},
        )

    mock_qual.assert_not_awaited()
    mock_sms.assert_not_called()
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("non_lead_contact" in s for s in sqls)
    assert any("intent_classified" in s for s in sqls)


@pytest.mark.asyncio
async def test_process_sms_reply_spam_intent_marks_spam_no_reply():
    client_id = uuid4()
    lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), _lead_row(lead_id)]
    conn.fetch.return_value = [_message_row("inbound", "WIN A FREE CRUISE text STOP to opt out")]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(),
        patch("app.webhooks.twilio.classify_intent", new=AsyncMock(return_value=Intent.spam)),
        patch("app.webhooks.twilio.qualifier_turn", new=AsyncMock()) as mock_qual,
        patch("app.webhooks.twilio.send_sms", new=AsyncMock()) as mock_sms,
    ):
        await twilio_webhook._process_sms_reply(
            client_id,
            {"From": "+15551112222", "Body": "WIN A FREE CRUISE", "MessageSid": "MM-spam"},
        )

    mock_qual.assert_not_awaited()
    mock_sms.assert_not_called()          # never reply to spam
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("'spam'" in s for s in sqls)


@pytest.mark.asyncio
async def test_process_sms_reply_ambiguous_intent_sends_clarifier():
    """A thin first reply gets ONE clarifying question; the lead stays
    'unqualified' so the gate re-runs on the next inbound."""
    client_id = uuid4()
    lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), _lead_row(lead_id)]
    conn.fetch.return_value = [_message_row("inbound", "hi")]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(),
        patch("app.webhooks.twilio.classify_intent", new=AsyncMock(return_value=Intent.ambiguous)),
        patch("app.webhooks.twilio.qualifier_turn", new=AsyncMock()) as mock_qual,
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM-amb"})) as mock_sms,
    ):
        await twilio_webhook._process_sms_reply(
            client_id, {"From": "+15551112222", "Body": "hi", "MessageSid": "MM-amb"}
        )

    mock_qual.assert_not_awaited()
    assert mock_sms.call_args.kwargs["body"] == twilio_webhook.INTENT_CLARIFIER
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("intent_classified" in s for s in sqls)
    assert not any("qualifying" in s for s in sqls)  # NOT promoted — gate re-runs


@pytest.mark.asyncio
async def test_process_sms_reply_intent_unavailable_degrades_to_qualifier():
    """classify_intent returns None (no key / outage) → proceed to the
    qualifier so a real lead is never dropped."""
    client_id = uuid4()
    lead_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [_config_row(client_id), _lead_row(lead_id)]
    conn.fetchval.return_value = False
    conn.fetch.return_value = [_message_row("inbound", "I need new countertops")]

    with (
        patch("app.webhooks.twilio.set_tenant_context", new=_fake_tenant_ctx(conn)),
        _patch_resolve(),
        patch("app.webhooks.twilio.classify_intent", new=AsyncMock(return_value=None)),
        patch(
            "app.webhooks.twilio.qualifier_turn",
            new=AsyncMock(return_value=("What's your zip?", {}, "v1")),
        ),
        patch("app.webhooks.twilio.send_sms", new=AsyncMock(return_value={"sid": "SM-deg"})) as mock_sms,
    ):
        await twilio_webhook._process_sms_reply(
            client_id,
            {"From": "+15551112222", "Body": "I need new countertops", "MessageSid": "MM-deg"},
        )

    # The qualifier ran: its reply was sent and the lead was promoted.
    assert mock_sms.call_args.kwargs["body"] == "What's your zip?"
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("qualifying" in s for s in sqls)  # promoted despite no classifier
