"""Deterministic qualification loop control (Slice 3).

Scoring, depends_on gating, hard gates, termination, and the completeness-vs-
value separation — all pure, no AI, no DB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.client_config import ClientConfig
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.qualification import QualificationSchema, default_schema
from app.services.qualification import (
    TerminationReason,
    applicable_fields,
    check_hard_gates,
    completeness_score,
    get_schema,
    merge_state,
    missing_required,
    should_terminate,
    split_extracted,
    value_score,
)


def _config(**overrides: Any) -> ClientConfig:
    base = {
        "client_id": uuid4(),
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ClientConfig(**base)


def _lead(**overrides: Any) -> Lead:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid4(),
        "client_id": uuid4(),
        "source_system": "twilio_missed_call",
        "raw_payload": {},
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return Lead(**base)


def _contact(known_facts: dict[str, Any] | None = None) -> Contact:
    now = datetime.now(UTC)
    return Contact(
        id=uuid4(), client_id=uuid4(), phone="+15551112222",
        known_facts=known_facts or {}, first_seen_at=now, last_seen_at=now, updated_at=now,
    )


# ---------------------------------------------------------------------------
# Schema model validation
# ---------------------------------------------------------------------------


def test_default_schema_is_valid() -> None:
    schema = default_schema()
    assert any(f.key == "project_stage" for f in schema.fields)  # highest-signal field kept


def test_duplicate_keys_rejected() -> None:
    with pytest.raises(ValidationError):
        QualificationSchema(
            fields=[
                {"key": "x", "label": "X", "type": "string", "ask": "?"},
                {"key": "x", "label": "X2", "type": "string", "ask": "?"},
            ]
        )


def test_enum_without_options_rejected() -> None:
    with pytest.raises(ValidationError):
        QualificationSchema(fields=[{"key": "m", "label": "M", "type": "enum", "ask": "?"}])


def test_bad_maps_to_rejected() -> None:
    with pytest.raises(ValidationError):
        QualificationSchema(
            fields=[{"key": "m", "label": "M", "type": "string", "ask": "?", "maps_to": "nonsense"}]
        )


def test_budget_options_violating_db_check_rejected() -> None:
    with pytest.raises(ValidationError):
        QualificationSchema(
            fields=[
                {"key": "budget_range", "label": "B", "type": "enum", "maps_to": "budget_range",
                 "ask": "?", "options": ["cheap", "expensive"]}  # not the DB's allowed set
            ]
        )


def test_depends_on_unknown_key_rejected() -> None:
    with pytest.raises(ValidationError):
        QualificationSchema(
            fields=[
                {"key": "m", "label": "M", "type": "string", "ask": "?",
                 "depends_on": {"ghost": ["x"]}}
            ]
        )


# ---------------------------------------------------------------------------
# get_schema
# ---------------------------------------------------------------------------


def test_get_schema_defaults_when_empty() -> None:
    schema = get_schema(_config())
    assert schema.min_score_to_qualify == 60


def test_get_schema_falls_back_on_invalid() -> None:
    schema = get_schema(_config(qualification_schema={"fields": "not-a-list"}))
    assert schema.min_score_to_qualify == 60  # default


def test_get_schema_overrides_service_type_options_from_brand() -> None:
    schema = get_schema(_config(brand={"service_types": ["pools", "decks"]}))
    service = schema.by_key()["service_type"]
    assert service.options == ["pools", "decks"]


# ---------------------------------------------------------------------------
# merge_state
# ---------------------------------------------------------------------------


def test_merge_state_combines_sources_lead_wins() -> None:
    lead = _lead(contact_name="Lead Name", service_type="countertop",
                 qualification_data={"material": "quartz"})
    contact = _contact({"contact_name": "Stale Name", "zip": "89101"})
    state = merge_state(lead, contact)
    assert state["contact_name"] == "Lead Name"   # lead wins over stale fact
    assert state["material"] == "quartz"          # from qualification_data
    assert state["zip"] == "89101"                # from known_facts


# ---------------------------------------------------------------------------
# applicable_fields / missing_required — depends_on
# ---------------------------------------------------------------------------


def test_material_hidden_until_service_type_matches() -> None:
    schema = default_schema()
    assert "material" not in {f.key for f in applicable_fields(schema, {})}
    with_ct = applicable_fields(schema, {"service_type": "countertop"})
    assert "material" in {f.key for f in with_ct}


def test_missing_required_sorted_by_weight_desc() -> None:
    schema = default_schema()
    missing = missing_required(schema, {})
    weights = [f.weight for f in missing]
    assert weights == sorted(weights, reverse=True)
    assert missing[0].key == "service_type"  # weight 20, highest


# ---------------------------------------------------------------------------
# Scoring — completeness vs value, never blended
# ---------------------------------------------------------------------------


def test_completeness_empty_is_zero() -> None:
    assert completeness_score(default_schema(), {}) == 0


def test_completeness_partial() -> None:
    # service_type captured → material becomes applicable; denominator = 100,
    # captured = service_type(20) → 20%.
    assert completeness_score(default_schema(), {"service_type": "countertop"}) == 20


def test_value_score_is_deterministic_and_unblended() -> None:
    schema = default_schema()
    state = {"sqft": 100, "budget_range": "15k-50k", "timeframe": "this_month",
             "service_type": "countertop"}
    # 20 (sqft) + 30 (budget) + 15 (timeframe) + 5 (service) = 70.
    assert value_score(schema, state, _config()) == 70
    # A tiny job is complete but low value — the two never merge.
    tiny = {"sqft": 12, "service_type": "backsplash", "budget_range": "<5k"}
    assert value_score(schema, tiny, _config()) < 30


def test_value_score_vip_threshold_floors_big_budget() -> None:
    schema = default_schema()
    state = {"budget_range": "50k+"}
    assert value_score(schema, state, _config(vip_value_threshold=20000)) == 85


# ---------------------------------------------------------------------------
# Hard gates
# ---------------------------------------------------------------------------


def test_out_of_area_zip_is_a_gate_failure() -> None:
    schema = default_schema()
    config = _config(service_area_zips=["89101", "89102"])
    fail = check_hard_gates(schema, {"address": "89999", "service_type": "countertop"}, config)
    assert fail is not None and fail.reason == "out_of_service_area"


def test_in_area_zip_passes() -> None:
    config = _config(service_area_zips=["89101"])
    assert check_hard_gates(default_schema(), {"address": "89101"}, config) is None


def test_disqualify_if_below_threshold() -> None:
    fail = check_hard_gates(default_schema(), {"sqft": 5}, _config())
    assert fail is not None and fail.field == "scope_size"


# ---------------------------------------------------------------------------
# Termination — code owns it
# ---------------------------------------------------------------------------


def test_terminate_qualified_at_min_score() -> None:
    schema = default_schema()
    # Enough required weight to clear 60%.
    state = {"service_type": "countertop", "address": "89101", "sqft": 40,
             "timeframe": "asap", "contact_name": "Al"}
    assert should_terminate(schema, state, turn_count=3) == TerminationReason.qualified


def test_terminate_needs_review_at_max_turns() -> None:
    schema = default_schema()
    assert should_terminate(schema, {}, turn_count=8) == TerminationReason.needs_review


def test_no_termination_mid_conversation() -> None:
    assert should_terminate(default_schema(), {"contact_name": "Al"}, turn_count=2) is None


# ---------------------------------------------------------------------------
# split_extracted
# ---------------------------------------------------------------------------


def test_split_extracted_routes_by_maps_to_and_scope() -> None:
    schema = default_schema()
    canonical, qual_data, person = split_extracted(
        schema,
        {
            "contact_name": "Maria",   # person + maps_to contact_name
            "service_type": "countertop",  # project + maps_to service_type
            "material": "quartz",      # project, no maps_to → qualification_data
            "property_type": "commercial",  # person, no maps_to
            "ghost": "ignored",        # unknown key
            "scope_size": 40,          # maps_to sqft
        },
    )
    assert canonical == {"contact_name": "Maria", "service_type": "countertop", "sqft": 40}
    assert qual_data == {"material": "quartz", "property_type": "commercial"}
    assert person == {"contact_name": "Maria", "property_type": "commercial"}
