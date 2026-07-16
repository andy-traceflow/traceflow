"""Config-driven qualification schema.

Qualification used to be hardcoded — a literal tool schema and a fixed field
list in prompts/qualifier.py, with `leads` columns for every field and nowhere
to put a client-specific one (material, project stage, property type). This
model makes the field set a per-client config row: what to ask, in what order,
how much each field is worth, whether it's a person- or project-scoped fact, and
which canonical `leads` column (if any) it maps to.

The model owns termination too: the AI no longer decides when the conversation
is done — services/qualification.py computes it deterministically from the
captured state, the weights, and the turn budget.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


class FieldScope(StrEnum):
    """Where a captured field lives after the conversation."""

    person = "person"    # durable → merges into contacts.known_facts
    project = "project"  # dies with the lead


# The real, writable canonical columns on `leads`. A field's maps_to must be one
# of these; anything else lands in leads.qualification_data (non-canonical).
LEAD_COLUMNS: frozenset[str] = frozenset(
    {"contact_name", "service_type", "sqft", "budget_range", "timeframe", "address"}
)

# Canonical columns whose DB CHECK constrains the allowed values. An enum field
# mapping to one of these must not offer an option the database would reject.
DB_CHECK_OPTIONS: dict[str, frozenset[str]] = {
    "budget_range": frozenset({"<5k", "5k-15k", "15k-50k", "50k+"}),
    "timeframe": frozenset({"asap", "this_month", "this_quarter", "researching"}),
}


class QualField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    type: Literal["string", "number", "enum", "boolean"]
    options: list[str] | None = None                 # required when type == 'enum'
    unit: str | None = None                           # 'sqft' | 'linear_ft' | 'rooms'
    scope: FieldScope = FieldScope.project
    required: bool = True
    weight: int = 10                                  # completeness weight
    ask: str                                          # the natural-language question
    depends_on: dict[str, list[str]] | None = None    # {'service_type': ['countertop']}
    disqualify_if: dict[str, Any] | None = None       # {'lt': 10}
    hard_gate: Literal["service_area"] | None = None
    maps_to: str | None = None                        # canonical leads column; else qualification_data

    @model_validator(mode="after")
    def _check_field(self) -> QualField:
        if self.type == "enum" and not self.options:
            raise ValueError(f"enum field {self.key!r} requires options")
        if self.maps_to is not None and self.maps_to not in LEAD_COLUMNS:
            raise ValueError(
                f"field {self.key!r} maps_to {self.maps_to!r}, not a canonical leads column"
            )
        if self.maps_to in DB_CHECK_OPTIONS and self.options:
            bad = set(self.options) - DB_CHECK_OPTIONS[self.maps_to]
            if bad:
                raise ValueError(
                    f"field {self.key!r} options {sorted(bad)} violate the DB CHECK "
                    f"on {self.maps_to!r}"
                )
        return self


class QualificationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[QualField]
    min_score_to_qualify: int = 60
    max_turns: int = 8
    max_questions_per_message: int = 1
    ask_budget: bool = False

    @model_validator(mode="after")
    def _check_schema(self) -> QualificationSchema:
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate field keys in qualification schema")
        keyset = set(keys)
        for field in self.fields:
            if field.depends_on:
                for dep_key in field.depends_on:
                    if dep_key not in keyset:
                        raise ValueError(
                            f"field {field.key!r} depends_on unknown key {dep_key!r}"
                        )
        return self

    def by_key(self) -> dict[str, QualField]:
        return {f.key: f for f in self.fields}


# ---------------------------------------------------------------------------
# Default schema — what every new surface-contractor tenant starts with.
# Kept in sync with the seed in migrations/020_add_qualification_schema.sql.
# service_type options are a sensible default; services/qualification.get_schema
# overrides them from brand.service_types at runtime when the client has them.
# ---------------------------------------------------------------------------
DEFAULT_QUALIFICATION_SCHEMA_DICT: dict[str, Any] = {
    "min_score_to_qualify": 60,
    "max_turns": 8,
    "max_questions_per_message": 1,
    "ask_budget": False,
    "fields": [
        {
            "key": "contact_name", "label": "Name", "type": "string", "scope": "person",
            "required": True, "weight": 10, "maps_to": "contact_name",
            "ask": "Can I grab your name so I can let the team know who to reach out to?",
        },
        {
            "key": "zip", "label": "ZIP code", "type": "string", "scope": "person",
            "required": True, "weight": 15, "maps_to": "address", "hard_gate": "service_area",
            "ask": "What ZIP code is the project in? Want to make sure it's in our service area.",
        },
        {
            "key": "service_type", "label": "Service", "type": "enum", "scope": "project",
            "required": True, "weight": 20, "maps_to": "service_type",
            "options": ["countertop", "flooring", "tile", "cabinets", "backsplash", "other"],
            "ask": "What kind of work are you looking to have done?",
        },
        {
            "key": "material", "label": "Material", "type": "enum", "scope": "project",
            "required": True, "weight": 15,
            "options": ["quartz", "granite", "quartzite", "marble", "porcelain",
                        "lvp", "tile", "concrete", "unsure"],
            "depends_on": {"service_type": ["countertop", "flooring", "tile"]},
            "ask": "Do you have a material in mind (quartz, granite, tile, etc.), or still deciding?",
        },
        {
            "key": "scope_size", "label": "Size", "type": "number", "scope": "project",
            "required": True, "weight": 15, "unit": "sqft", "maps_to": "sqft",
            "disqualify_if": {"lt": 10},
            "ask": "Roughly how many square feet is the project?",
        },
        {
            "key": "timeframe", "label": "Timeframe", "type": "enum", "scope": "project",
            "required": True, "weight": 15, "maps_to": "timeframe",
            "options": ["asap", "this_month", "this_quarter", "researching"],
            "ask": "When are you hoping to get this done?",
        },
        {
            "key": "project_stage", "label": "Stage", "type": "enum", "scope": "project",
            "required": True, "weight": 10,
            "options": ["pricing", "have_measurements", "ready_to_schedule"],
            "ask": "Where are you in the process — just pricing it out, have measurements, or ready to schedule?",
        },
        {
            "key": "budget_range", "label": "Budget", "type": "enum", "scope": "project",
            "required": False, "weight": 0, "maps_to": "budget_range",
            "options": ["<5k", "5k-15k", "15k-50k", "50k+"],
            "ask": "Do you have a budget range in mind?",
        },
        {
            "key": "property_type", "label": "Property", "type": "enum", "scope": "person",
            "required": False, "weight": 0,
            "options": ["residential", "commercial", "new_construction", "remodel"],
            "ask": "Is this a residential or commercial property?",
        },
        {
            "key": "tear_out_needed", "label": "Tear-out", "type": "boolean", "scope": "project",
            "required": False, "weight": 0,
            "ask": "Is there existing material that needs to be torn out first?",
        },
    ],
}


def default_schema() -> QualificationSchema:
    """The validated default schema (a fresh instance each call)."""
    return QualificationSchema(**DEFAULT_QUALIFICATION_SCHEMA_DICT)
