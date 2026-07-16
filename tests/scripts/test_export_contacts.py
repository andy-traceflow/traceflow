"""Contact export CSV (Slice 2.5) — the off-boarding / backup / migration tool."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from scripts.export_contacts import build_csv, export_contacts

T0 = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)


def _rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    c1, c2 = uuid4(), uuid4()
    lead1 = uuid4()
    contacts = [
        {
            "id": c1, "phone": "+17025178074", "name": "Maria Lopez",
            "contact_type": "customer", "contact_type_source": "crm",
            "crm_external_id": "hs-1", "call_count": 3, "lead_count": 1,
            "known_facts": {"contact_name": "Maria Lopez", "zip": "89101"},
            "summary": "Repeat kitchen client.",
        },
        {
            "id": c2, "phone": "+17025550100", "name": "No Leads Yet",
            "contact_type": "prospect", "contact_type_source": "inferred",
            "crm_external_id": None, "call_count": 1, "lead_count": 0,
            "known_facts": {}, "summary": None,
        },
    ]
    leads = [
        {
            "id": lead1, "contact_id": c1, "created_at": T0,
            "qualification_status": "qualified", "classification": "potential_lead",
            "service_type": "countertop", "sqft": 42.0, "budget_range": "15k-50k",
            "timeframe": "this_month", "outcome": "won", "recovered_value": 18500,
        },
    ]
    messages = [
        {"lead_id": lead1, "direction": "outbound", "body": "Sorry we missed you!", "created_at": T0},
        {"lead_id": lead1, "direction": "inbound", "body": "Need a quote", "created_at": T0},
    ]
    return contacts, leads, messages


def _parse(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text)))


def test_build_csv_one_row_per_lead() -> None:
    contacts, leads, messages = _rows()
    rows = _parse(build_csv(contacts, leads, messages))
    # Two contacts: one with a lead, one lead-less → 2 rows total.
    assert len(rows) == 2
    maria = next(r for r in rows if r["name"] == "Maria Lopez")
    assert maria["contact_type"] == "customer"
    assert maria["qualification_status"] == "qualified"
    assert maria["service_type"] == "countertop"
    assert maria["recovered_value"] == "18500"


def test_build_csv_includes_transcript_ordered() -> None:
    contacts, leads, messages = _rows()
    rows = _parse(build_csv(contacts, leads, messages))
    maria = next(r for r in rows if r["name"] == "Maria Lopez")
    # Oldest-first, both directions present.
    assert "outbound: Sorry we missed you!" in maria["transcript"]
    assert "inbound: Need a quote" in maria["transcript"]


def test_build_csv_leadless_contact_still_emitted() -> None:
    contacts, leads, messages = _rows()
    rows = _parse(build_csv(contacts, leads, messages))
    leadless = next(r for r in rows if r["name"] == "No Leads Yet")
    assert leadless["lead_id"] == ""          # blank lead columns
    assert leadless["contact_type"] == "prospect"


def test_build_csv_known_facts_serialized() -> None:
    contacts, leads, messages = _rows()
    rows = _parse(build_csv(contacts, leads, messages))
    maria = next(r for r in rows if r["name"] == "Maria Lopez")
    assert '"zip": "89101"' in maria["known_facts"]


@pytest.mark.asyncio
async def test_export_contacts_queries_and_builds() -> None:
    contacts, leads, messages = _rows()
    conn = AsyncMock()
    conn.fetch.side_effect = [contacts, leads, messages]  # contacts, leads, messages
    csv_text = await export_contacts(conn, uuid4())
    assert conn.fetch.await_count == 3
    assert "Maria Lopez" in csv_text
    assert csv_text.splitlines()[0].startswith("contact_id,phone,name")
