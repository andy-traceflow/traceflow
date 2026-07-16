"""Deterministic qualification loop control.

The model NEVER decides when the conversation is done. This service owns the
loop: it merges the captured state, resolves which fields are applicable
(depends_on), computes the completeness score (repurposed qualification_score)
and a separate value_score, enforces hard gates (service-area, disqualify_if),
and decides termination — all deterministically, no AI. The qualifier prompt
only produces the next question and records extractions.

Two scores, never blended: `qualification_score` is completeness (how much
required info we have); `value_score` estimates the job's worth. A fully
captured $700 backsplash is 100% complete and near-zero value.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.models.client_config import ClientConfig
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.qualification import (
    FieldScope,
    QualField,
    QualificationSchema,
    default_schema,
)

logger = logging.getLogger(__name__)

_ZIP_RE = re.compile(r"\b(\d{5})\b")

# Deterministic value inputs (no AI).
_BUDGET_POINTS = {"<5k": 5, "5k-15k": 15, "15k-50k": 30, "50k+": 40}
_BUDGET_MIDPOINTS = {"<5k": 2500, "5k-15k": 10000, "15k-50k": 32500, "50k+": 75000}
_TIMEFRAME_POINTS = {"asap": 20, "this_month": 15, "this_quarter": 8, "researching": 0}


class TerminationReason(StrEnum):
    qualified = "qualified"
    disqualified = "disqualified"
    needs_review = "needs_review"


@dataclass(frozen=True)
class GateFailure:
    field: str
    reason: str


def get_schema(config: ClientConfig) -> QualificationSchema:
    """The client's qualification schema, or the default when empty/invalid.

    service_type options are overridden from brand.service_types at runtime so
    the enum matches the client's actual offerings without duplicating them
    into the schema row.
    """
    raw = config.qualification_schema
    schema: QualificationSchema
    if raw:
        try:
            schema = QualificationSchema(**raw)
        except Exception as e:
            logger.warning("invalid qualification_schema — using default", exc_info=e)
            schema = default_schema()
    else:
        schema = default_schema()

    brand_types = config.service_types
    if brand_types:
        for field in schema.fields:
            if field.key == "service_type" and field.type == "enum":
                field.options = list(brand_types)
    return schema


def field_value(field: QualField, state: dict[str, Any]) -> Any:
    """The captured value for a field — its canonical column if it maps to one,
    else its own key in the merged state."""
    if field.maps_to:
        return state.get(field.maps_to)
    return state.get(field.key)


def merge_state(lead: Lead, contact: Contact | None) -> dict[str, Any]:
    """The captured state: canonical lead columns + lead.qualification_data +
    the contact's person-scoped known_facts. Lead values win over stale facts.
    Empty values are dropped so 'captured' means genuinely present."""
    state: dict[str, Any] = {
        "contact_name": lead.contact_name,
        "service_type": lead.service_type,
        "sqft": lead.sqft,
        "budget_range": lead.budget_range,
        "timeframe": lead.timeframe,
        "address": lead.address,
    }
    state.update(lead.qualification_data or {})
    if contact is not None:
        for key, value in (contact.known_facts or {}).items():
            state.setdefault(key, value)
    return {k: v for k, v in state.items() if v not in (None, "")}


def applicable_fields(schema: QualificationSchema, state: dict[str, Any]) -> list[QualField]:
    """Fields whose depends_on conditions are satisfied by the current state."""
    by_key = schema.by_key()
    result: list[QualField] = []
    for field in schema.fields:
        if field.depends_on:
            satisfied = True
            for dep_key, allowed in field.depends_on.items():
                dep_field = by_key.get(dep_key)
                dep_val = field_value(dep_field, state) if dep_field else state.get(dep_key)
                if dep_val not in allowed:
                    satisfied = False
                    break
            if not satisfied:
                continue
        result.append(field)
    return result


def missing_required(schema: QualificationSchema, state: dict[str, Any]) -> list[QualField]:
    """Applicable required fields not yet captured, highest-weight first."""
    missing = [
        f
        for f in applicable_fields(schema, state)
        if f.required and field_value(f, state) in (None, "")
    ]
    return sorted(missing, key=lambda f: f.weight, reverse=True)


def askable_fields(schema: QualificationSchema, state: dict[str, Any]) -> list[QualField]:
    """The fields the model may ask for this turn. Missing required fields,
    minus the budget field when ask_budget is off (budget is inferred, not
    asked)."""
    return [
        f
        for f in missing_required(schema, state)
        if not (f.maps_to == "budget_range" and not schema.ask_budget)
    ]


def completeness_score(schema: QualificationSchema, state: dict[str, Any]) -> int:
    """Captured required weight / applicable required weight × 100."""
    required = [f for f in applicable_fields(schema, state) if f.required]
    total = sum(f.weight for f in required)
    if total == 0:
        return 100
    captured = sum(f.weight for f in required if field_value(f, state) not in (None, ""))
    return round(captured / total * 100)


def value_score(schema: QualificationSchema, state: dict[str, Any], config: ClientConfig) -> int:
    """A deterministic 0–100 estimate of the job's value. No AI, never blended
    with completeness. Larger jobs, bigger budgets, sooner timelines, and
    commercial/new-construction work score higher; a configured VIP value
    threshold floors a big-budget lead high."""
    score = 0
    sqft = _as_number(state.get("sqft"))
    if sqft:
        score += min(int(sqft / 5), 30)
    score += _BUDGET_POINTS.get(state.get("budget_range"), 0)
    score += _TIMEFRAME_POINTS.get(state.get("timeframe"), 0)
    if state.get("property_type") in ("commercial", "new_construction"):
        score += 15
    if state.get("service_type"):
        score += 5

    threshold = config.vip_value_threshold
    est = _BUDGET_MIDPOINTS.get(state.get("budget_range"))
    if threshold and est and est >= float(threshold):
        score = max(score, 85)

    return max(0, min(score, 100))


def check_hard_gates(
    schema: QualificationSchema,
    state: dict[str, Any],
    config: ClientConfig,
) -> GateFailure | None:
    """A hard disqualification (not a score input): service-area zip mismatch or
    a disqualify_if floor. Returns the first failure, or None."""
    for field in applicable_fields(schema, state):
        value = field_value(field, state)
        if value in (None, ""):
            continue
        if field.hard_gate == "service_area" and config.service_area_zips:
            zip_code = _extract_zip(value)
            if zip_code is None or zip_code not in config.service_area_zips:
                return GateFailure(field.key, "out_of_service_area")
        if field.disqualify_if and _fails_disqualify(value, field.disqualify_if):
            return GateFailure(field.key, "below_threshold")
    return None


def should_terminate(
    schema: QualificationSchema,
    state: dict[str, Any],
    turn_count: int,
) -> TerminationReason | None:
    """Deterministic termination (gates are checked separately by the caller):
    qualified once completeness clears the bar, needs_review at the turn budget,
    otherwise keep going."""
    if completeness_score(schema, state) >= schema.min_score_to_qualify:
        return TerminationReason.qualified
    if turn_count >= schema.max_turns:
        return TerminationReason.needs_review
    return None


def split_extracted(
    schema: QualificationSchema,
    extracted: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Split raw tool extractions (keyed by field key) into:
      canonical     — {leads column: value} for a validated LeadUpdate
      qualification_data — {field key: value} for the non-canonical JSONB
      person        — {field key: value} to merge into contacts.known_facts
    Unknown keys and empty values are dropped."""
    by_key = schema.by_key()
    canonical: dict[str, Any] = {}
    qualification_data: dict[str, Any] = {}
    person: dict[str, Any] = {}
    for key, value in extracted.items():
        field = by_key.get(key)
        if field is None or value in (None, ""):
            continue
        if field.maps_to:
            canonical[field.maps_to] = value
        else:
            qualification_data[key] = value
        if field.scope == FieldScope.person:
            person[key] = value
    return canonical, qualification_data, person


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_zip(value: Any) -> str | None:
    match = _ZIP_RE.search(str(value))
    return match.group(1) if match else None


def _fails_disqualify(value: Any, condition: dict[str, Any]) -> bool:
    """Evaluate a disqualify_if condition. Supports lt/lte/gt/gte/eq/in."""
    for op, target in condition.items():
        num = _as_number(value)
        if op in ("lt", "lte", "gt", "gte") and num is None:
            continue
        if op == "lt" and num < target:
            return True
        if op == "lte" and num <= target:
            return True
        if op == "gt" and num > target:
            return True
        if op == "gte" and num >= target:
            return True
        if op == "eq" and value == target:
            return True
        if op == "in" and value in target:
            return True
    return False
