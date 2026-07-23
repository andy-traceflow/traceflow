"""Lead model contract tests.

Regression cover for the 2026-07-21 prod incident: a lead row with
`notes = NULL` (the normal state for leads created by the missed-call and
inbound-SMS paths, which never set notes) raised

    ValidationError: notes — Input should be a valid string [input_value=None]

inside `Lead(**dict(lead_row))` in webhooks/twilio.py::_process_sms_reply,
500-ing every inbound SMS reply. Callers never got past the first
qualification question.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.models.lead import Lead

NOW = datetime.now(UTC)


def _row(**overrides: object) -> dict[str, object]:
    """Minimal lead row as asyncpg hands it back."""
    return {
        "id": uuid4(),
        "client_id": uuid4(),
        "source_system": "twilio_sms_inbound",
        "raw_payload": {},
        "created_at": NOW,
        "updated_at": NOW,
        **overrides,
    }


def test_null_notes_coerces_to_empty_string() -> None:
    """The exact prod failure: notes is NULL in Postgres."""
    lead = Lead(**_row(notes=None))
    assert lead.notes == ""


def test_missing_notes_defaults_to_empty_string() -> None:
    lead = Lead(**_row())
    assert lead.notes == ""


def test_real_notes_pass_through_unchanged() -> None:
    lead = Lead(**_row(notes="Wants a quote for 400 sqft."))
    assert lead.notes == "Wants a quote for 400 sqft."


def test_notes_is_always_a_str_for_crm_adapters() -> None:
    """The adapters push lead.notes straight into GHL/HubSpot/Monday payloads,
    so it must never be None regardless of what the row held."""
    for value in (None, "", "text"):
        assert isinstance(Lead(**_row(notes=value)).notes, str)
