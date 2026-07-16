"""Caller-classification tests — the pre-send routing tree over contacts.

The contact layer (resolve_contact / set_contact_type / resolve_contact_type)
and the CRM/spam lookups are patched, so the suite runs offline and each test
drives the tree by contact state. The resolver's own internals are covered in
tests/services/test_contact_resolver.py.

Prime directive under test: every failing or ambiguous path degrades toward
potential_lead so a real lead is never dropped; known callers cost no lookups;
a known prospect is never spam-scored.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.models.client_config import ClientConfig
from app.models.contact import Contact, ContactType, ContactTypeSource
from app.models.lead import Classification
from app.services import classification
from app.services.classification import Route, classify_caller
from app.services.contacts import TypeResolution
from app.services.spam import SpamRisk

PHONE = "+15551112222"


def _make_config(**overrides: Any) -> ClientConfig:
    base = {
        "client_id": uuid4(),
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ClientConfig(**base)


def _make_contact(
    contact_type: ContactType = ContactType.unknown,
    source: ContactTypeSource = ContactTypeSource.inferred,
    type_at: datetime | None = None,
    known_facts: dict[str, Any] | None = None,
) -> Contact:
    now = datetime.now(UTC)
    return Contact(
        id=uuid4(),
        client_id=uuid4(),
        phone=PHONE,
        contact_type=contact_type,
        contact_type_source=source,
        contact_type_at=type_at,
        known_facts=known_facts or {},
        first_seen_at=now,
        last_seen_at=now,
        updated_at=now,
    )


def _conn(open_lead: dict[str, Any] | None = None, last_lead_at: datetime | None = None) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchrow.return_value = open_lead  # the open-lead query
    conn.fetchval.return_value = last_lead_at  # the returning last-lead query
    return conn


def _patches(
    contact: Contact,
    *,
    resolution: TypeResolution | None = None,
    spam_risk: SpamRisk | None = None,
) -> ExitStack:
    """Patch the contact-layer seams classify_caller depends on."""
    stack = ExitStack()
    set_type = AsyncMock(return_value=True)
    stack.enter_context(
        patch.object(classification, "resolve_contact", new=AsyncMock(return_value=contact))
    )
    stack.enter_context(patch.object(classification, "set_contact_type", new=set_type))
    if resolution is not None:
        stack.enter_context(
            patch.object(
                classification, "resolve_contact_type", new=AsyncMock(return_value=resolution)
            )
        )
    if spam_risk is not None:
        stack.enter_context(
            patch.object(classification, "score_spam_risk", new=AsyncMock(return_value=spam_risk))
        )
    stack.set_type = set_type  # type: ignore[attr-defined]
    return stack


def _res(ctype: ContactType, source: ContactTypeSource = ContactTypeSource.crm) -> TypeResolution:
    return TypeResolution(ctype, source, "crm", from_cache=False)


# ---------------------------------------------------------------------------
# Step 1 — no phone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_phone_defaults_to_potential_lead() -> None:
    conn = _conn()
    result = await classify_caller(conn, uuid4(), None, _make_config())
    assert result.route == Route.potential_lead
    assert result.contact is None
    assert result.should_text is True
    conn.fetchrow.assert_not_called()


# ---------------------------------------------------------------------------
# Step 3 — hard drops
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_contact_is_dropped() -> None:
    contact = _make_contact(ContactType.blocked, ContactTypeSource.manual)
    with _patches(contact):
        result = await classify_caller(_conn(), uuid4(), PHONE, _make_config())
    assert result.route == Route.spam
    assert result.should_text is False
    assert result.reason == "blocked_contact"


@pytest.mark.asyncio
async def test_cached_spam_contact_is_dropped_without_rescore() -> None:
    contact = _make_contact(ContactType.spam, ContactTypeSource.inferred, type_at=datetime.now(UTC))
    with _patches(contact):
        result = await classify_caller(_conn(), uuid4(), PHONE, _make_config())
    assert result.route == Route.spam
    assert result.reason == "cached_spam"
    assert result.should_text is False


@pytest.mark.asyncio
async def test_spam_rescore_when_window_elapsed() -> None:
    """With rescore_spam_after_days set and elapsed, a spam contact is re-scored;
    a clean score revives them as a prospect (potential_lead)."""
    contact = _make_contact(
        ContactType.spam, ContactTypeSource.inferred, type_at=datetime.now(UTC) - timedelta(days=40)
    )
    config = _make_config(classification_config={"rescore_spam_after_days": 30})
    with _patches(contact, resolution=_res(ContactType.spam, ContactTypeSource.inferred), spam_risk=SpamRisk.low):
        result = await classify_caller(_conn(), uuid4(), PHONE, config)
    assert result.route == Route.potential_lead


# ---------------------------------------------------------------------------
# Step 3b — vendor allowlist (authoritative, live)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vendor_allowlist_routes_known_non_lead_no_text() -> None:
    contact = _make_contact()
    config = _make_config(vendor_allowlist=[PHONE])
    with _patches(contact):
        result = await classify_caller(_conn(), uuid4(), PHONE, config)
    assert result.route == Route.known_non_lead
    assert result.classification == Classification.known_non_lead
    assert result.should_text is False  # text_vendors defaults False


@pytest.mark.asyncio
async def test_vendor_allowlist_matches_across_formatting() -> None:
    """A hand-entered allowlist entry in a different format still matches."""
    contact = _make_contact()
    config = _make_config(vendor_allowlist=["+1 (555) 111-2222"])
    with _patches(contact):
        result = await classify_caller(_conn(), uuid4(), PHONE, config)
    assert result.route == Route.known_non_lead


@pytest.mark.asyncio
async def test_vendor_allowlist_texts_when_configured() -> None:
    contact = _make_contact()
    config = _make_config(vendor_allowlist=[PHONE], classification_config={"text_vendors": True})
    with _patches(contact):
        result = await classify_caller(_conn(), uuid4(), PHONE, config)
    assert result.should_text is True


# ---------------------------------------------------------------------------
# Step 4 — cached known type skips lookups (the cost win)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cached_customer_skips_resolver() -> None:
    contact = _make_contact(ContactType.customer, ContactTypeSource.crm, type_at=datetime.now(UTC))
    config = _make_config(crm_provider="ghl")
    resolver = AsyncMock()
    with _patches(contact), patch.object(classification, "resolve_contact_type", new=resolver):
        result = await classify_caller(_conn(), uuid4(), PHONE, config)
    assert result.route == Route.existing_customer
    assert result.reason == "cached_contact_type"
    resolver.assert_not_called()  # no CRM lookup for a fresh cached type


@pytest.mark.asyncio
async def test_cached_vendor_skips_resolver() -> None:
    contact = _make_contact(ContactType.vendor, ContactTypeSource.crm, type_at=datetime.now(UTC))
    resolver = AsyncMock()
    with _patches(contact), patch.object(classification, "resolve_contact_type", new=resolver):
        result = await classify_caller(_conn(), uuid4(), PHONE, _make_config(crm_provider="ghl"))
    assert result.route == Route.known_non_lead
    resolver.assert_not_called()


@pytest.mark.asyncio
async def test_stale_cached_customer_falls_through_to_resolver() -> None:
    """An expired cached type re-checks the resolver (does not blindly trust)."""
    contact = _make_contact(
        ContactType.customer, ContactTypeSource.crm, type_at=datetime.now(UTC) - timedelta(days=60)
    )
    with _patches(contact, resolution=_res(ContactType.customer)):
        result = await classify_caller(_conn(), uuid4(), PHONE, _make_config(crm_provider="ghl"))
    assert result.route == Route.existing_customer
    assert result.reason == "resolved_customer"


# ---------------------------------------------------------------------------
# Step 5 — open lead: active vs resumed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_open_lead_is_active_conversation() -> None:
    lead_id = uuid4()
    contact = _make_contact(ContactType.prospect)
    conn = _conn(open_lead={"id": lead_id, "last_activity": datetime.now(UTC) - timedelta(hours=2)})
    with _patches(contact):
        result = await classify_caller(conn, uuid4(), PHONE, _make_config())
    assert result.route == Route.active_conversation
    assert result.should_text is False
    assert result.existing_lead_id == lead_id


@pytest.mark.asyncio
async def test_stale_open_lead_resumes_and_reuses() -> None:
    lead_id = uuid4()
    contact = _make_contact(ContactType.prospect)
    conn = _conn(
        open_lead={"id": lead_id, "last_activity": datetime.now(UTC) - timedelta(hours=400)}
    )
    with _patches(contact):
        result = await classify_caller(conn, uuid4(), PHONE, _make_config())
    assert result.route == Route.resumed_conversation
    assert result.should_text is True
    assert result.existing_lead_id == lead_id  # reused (reuse_lead_on_resume default True)
    assert result.is_returning is True


@pytest.mark.asyncio
async def test_stale_open_lead_no_reuse_drops_lead_id() -> None:
    contact = _make_contact(ContactType.prospect)
    conn = _conn(
        open_lead={"id": uuid4(), "last_activity": datetime.now(UTC) - timedelta(hours=400)}
    )
    config = _make_config(conversation_config={"reuse_lead_on_resume": False})
    with _patches(contact):
        result = await classify_caller(conn, uuid4(), PHONE, config)
    assert result.route == Route.resumed_conversation
    assert result.existing_lead_id is None  # a fresh lead will be created


# ---------------------------------------------------------------------------
# Step 6 — type resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_customer_routes_existing_customer() -> None:
    contact = _make_contact()
    with _patches(contact, resolution=_res(ContactType.customer)):
        result = await classify_caller(_conn(), uuid4(), PHONE, _make_config(crm_provider="ghl"))
    assert result.route == Route.existing_customer
    assert result.should_text is True  # text_existing_customers default True


@pytest.mark.asyncio
async def test_resolver_vendor_routes_known_non_lead() -> None:
    contact = _make_contact()
    with _patches(contact, resolution=_res(ContactType.vendor)):
        result = await classify_caller(_conn(), uuid4(), PHONE, _make_config(crm_provider="ghl"))
    assert result.route == Route.known_non_lead


# ---------------------------------------------------------------------------
# Step 7 — spam scoring, unknown only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_high_risk_routes_spam() -> None:
    contact = _make_contact(ContactType.unknown)
    with _patches(contact, resolution=_res(ContactType.unknown, ContactTypeSource.inferred), spam_risk=SpamRisk.high):
        result = await classify_caller(_conn(), uuid4(), PHONE, _make_config())
    assert result.route == Route.spam
    assert result.should_text is False  # drop_spam_silently default True


@pytest.mark.asyncio
async def test_unknown_low_risk_becomes_potential_lead_and_promotes() -> None:
    contact = _make_contact(ContactType.unknown)
    with _patches(
        contact, resolution=_res(ContactType.unknown, ContactTypeSource.inferred), spam_risk=SpamRisk.low
    ) as p:
        result = await classify_caller(_conn(), uuid4(), PHONE, _make_config())
    assert result.route == Route.potential_lead
    # unknown → prospect promotion on first engagement.
    assert any(
        call.args[2] == ContactType.prospect for call in p.set_type.await_args_list  # type: ignore[attr-defined]
    )


@pytest.mark.asyncio
async def test_prospect_is_never_spam_scored() -> None:
    contact = _make_contact(ContactType.prospect)
    score = AsyncMock(return_value=SpamRisk.high)
    with _patches(contact, resolution=_res(ContactType.prospect, ContactTypeSource.inferred)), patch.object(
        classification, "score_spam_risk", new=score
    ):
        result = await classify_caller(_conn(last_lead_at=None), uuid4(), PHONE, _make_config())
    score.assert_not_called()
    assert result.route == Route.potential_lead


@pytest.mark.asyncio
async def test_spam_filtering_disabled_skips_scoring() -> None:
    contact = _make_contact(ContactType.unknown)
    score = AsyncMock(return_value=SpamRisk.high)
    config = _make_config(classification_config={"spam_filtering_enabled": False})
    with _patches(contact, resolution=_res(ContactType.unknown, ContactTypeSource.inferred)), patch.object(
        classification, "score_spam_risk", new=score
    ):
        result = await classify_caller(_conn(), uuid4(), PHONE, config)
    score.assert_not_called()
    assert result.route == Route.potential_lead


@pytest.mark.asyncio
async def test_spam_lookup_failure_degrades_to_potential_lead() -> None:
    contact = _make_contact(ContactType.unknown)
    with _patches(contact, resolution=_res(ContactType.unknown, ContactTypeSource.inferred)), patch.object(
        classification, "score_spam_risk", new=AsyncMock(side_effect=Exception("twilio down"))
    ):
        result = await classify_caller(_conn(), uuid4(), PHONE, _make_config())
    assert result.route == Route.potential_lead


# ---------------------------------------------------------------------------
# Step 8 — returning prospect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returning_prospect_within_window() -> None:
    contact = _make_contact(ContactType.prospect, known_facts={"contact_name": "Maria"})
    conn = _conn(open_lead=None, last_lead_at=datetime.now(UTC) - timedelta(days=10))
    with _patches(contact, resolution=_res(ContactType.prospect, ContactTypeSource.inferred)):
        result = await classify_caller(conn, uuid4(), PHONE, _make_config())
    assert result.route == Route.returning_contact
    assert result.is_returning is True
    assert result.should_text is True


@pytest.mark.asyncio
async def test_returning_prospect_outside_window_is_potential_lead() -> None:
    contact = _make_contact(ContactType.prospect)
    conn = _conn(open_lead=None, last_lead_at=datetime.now(UTC) - timedelta(days=200))
    with _patches(contact, resolution=_res(ContactType.prospect, ContactTypeSource.inferred)):
        result = await classify_caller(conn, uuid4(), PHONE, _make_config())
    assert result.route == Route.potential_lead
    assert result.is_returning is False
