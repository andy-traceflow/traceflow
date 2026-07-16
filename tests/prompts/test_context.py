"""Unified prompt context tests (Slice 4) — the four blocks + cache_control."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.models.client_config import ClientConfig
from app.models.contact import Contact, ContactType
from app.models.qualification import default_schema
from app.prompts.context import build_prompt_context

FIXED_NOW = datetime(2026, 7, 15, 16, 30, tzinfo=UTC)  # a Wednesday, 16:30 UTC


def _config(**overrides: Any) -> ClientConfig:
    base = {
        "client_id": uuid4(),
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ClientConfig(**base)


def _contact(**overrides: Any) -> Contact:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid4(), "client_id": uuid4(), "phone": "+15551112222",
        "first_seen_at": now, "last_seen_at": now, "updated_at": now,
    }
    base.update(overrides)
    return Contact(**base)


# ---------------------------------------------------------------------------
# Business block + cache_control
# ---------------------------------------------------------------------------


def test_business_block_and_cache_control() -> None:
    config = _config(
        brand={"business_name": "Acme Surfaces", "category": "countertop",
               "service_types": ["countertop", "tile"], "tone_of_voice": "warm"},
        service_area_zips=["89101", "89102"],
    )
    ctx = build_prompt_context(config, None, now=FIXED_NOW)
    assert "Acme Surfaces" in ctx.business
    assert "countertop, tile" in ctx.business
    assert "89101" in ctx.business
    # The business block is the first system block and is cached.
    blocks = ctx.system_blocks("RULES")
    assert blocks[0]["text"] == ctx.business
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[-1]["text"].endswith("RULES")


def test_terminology_overrides_in_business_block() -> None:
    config = _config(brand={"terminology_overrides": {"quote": "estimate"}})
    ctx = build_prompt_context(config, None, now=FIXED_NOW)
    assert "estimate" in ctx.business


# ---------------------------------------------------------------------------
# Caller block — omitted for first-time callers
# ---------------------------------------------------------------------------


def test_caller_block_omitted_without_contact() -> None:
    ctx = build_prompt_context(_config(), None, now=FIXED_NOW)
    assert ctx.caller is None


def test_caller_block_omitted_for_brand_new_unknown() -> None:
    fresh = _contact(contact_type=ContactType.unknown, call_count=1, lead_count=0)
    ctx = build_prompt_context(_config(), fresh, now=FIXED_NOW)
    assert ctx.caller is None


def test_caller_block_present_for_returning_contact() -> None:
    contact = _contact(
        name="Maria", contact_type=ContactType.prospect, call_count=3, lead_count=2,
        known_facts={"zip": "89101"}, summary="Priced a quartz kitchen last month.",
        last_seen_at=FIXED_NOW - timedelta(days=20),
    )
    ctx = build_prompt_context(_config(), contact, now=FIXED_NOW)
    assert ctx.caller is not None
    assert "Maria" in ctx.caller
    assert "prospect" in ctx.caller
    assert "89101" in ctx.caller
    assert "quartz kitchen" in ctx.caller
    assert "Days since last contact: 20" in ctx.caller


# ---------------------------------------------------------------------------
# State block — only with schema + state
# ---------------------------------------------------------------------------


def test_state_block_absent_without_schema() -> None:
    ctx = build_prompt_context(_config(), None, now=FIXED_NOW)
    assert ctx.state is None


def test_state_block_shows_captured_and_needed() -> None:
    ctx = build_prompt_context(
        _config(), None, schema=default_schema(),
        state={"service_type": "countertop"}, turn_count=2, now=FIXED_NOW,
    )
    assert ctx.state is not None
    assert "Service: countertop" in ctx.state       # captured
    assert "Turn 3 of at most 8" in ctx.state
    assert "Material" in ctx.state                   # now applicable + still needed


# ---------------------------------------------------------------------------
# Time block
# ---------------------------------------------------------------------------


def test_time_block_reports_local_and_hours_status() -> None:
    config = _config(business_hours={"wed": {"open": "08:00", "close": "17:00"}})
    ctx = build_prompt_context(config, None, timezone="UTC", now=FIXED_NOW)
    assert "Business hours status: open" in ctx.time  # 16:30 Wed is inside 08-17


def test_time_block_closed_outside_hours() -> None:
    config = _config(business_hours={"wed": {"open": "08:00", "close": "15:00"}})
    ctx = build_prompt_context(config, None, timezone="UTC", now=FIXED_NOW)
    assert "Business hours status: closed" in ctx.time  # 16:30 is after 15:00
