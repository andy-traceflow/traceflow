"""Export one client's contacts + leads + transcripts to CSV.

Serves three purposes, built once (Slice 2.5):
  1. The Appendix C contractual off-boarding export promised in every contract.
  2. The migration path when a traceflow-mode client later buys a CRM — export,
     import, flip contact_config.source_of_truth to 'crm', backfill
     crm_external_id.
  3. Backup verification.

One CSV row per lead (a contact with no leads still gets a row), with the
contact's identity + facts and the lead's SMS transcript inlined.

Usage (bash):
    SUPABASE_DB_URL='postgresql://...' python scripts/export_contacts.py <client_id> [out.csv]

Usage (PowerShell):
    $env:SUPABASE_DB_URL = 'postgresql://...'
    python scripts/export_contacts.py <client_id> out.csv

With no output path the CSV is written to stdout.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import sys
from typing import Any
from uuid import UUID

import asyncpg

CSV_COLUMNS = [
    "contact_id",
    "phone",
    "name",
    "contact_type",
    "contact_type_source",
    "crm_external_id",
    "call_count",
    "lead_count",
    "known_facts",
    "summary",
    "lead_id",
    "lead_created_at",
    "qualification_status",
    "classification",
    "service_type",
    "sqft",
    "budget_range",
    "timeframe",
    "outcome",
    "recovered_value",
    "transcript",
]


def _transcript(messages: list[dict[str, Any]]) -> str:
    """Flatten a lead's messages into one readable cell, oldest first."""
    parts = []
    for m in sorted(messages, key=lambda r: r["created_at"]):
        parts.append(f"[{m['created_at']:%Y-%m-%d %H:%M}] {m['direction']}: {m['body']}")
    return "\n".join(parts)


def _jsonify(value: Any) -> str:
    """known_facts arrives as a dict (JSONB codec); render it stably for CSV."""
    if value in (None, "", {}):
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def build_csv(
    contacts: list[dict[str, Any]],
    leads: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> str:
    """Assemble the CSV text from raw rows. Pure — no DB — so it is unit-tested
    offline. Contacts with no leads still emit one (lead-blank) row."""
    leads_by_contact: dict[Any, list[dict[str, Any]]] = {}
    for lead in leads:
        leads_by_contact.setdefault(lead["contact_id"], []).append(lead)
    msgs_by_lead: dict[Any, list[dict[str, Any]]] = {}
    for msg in messages:
        msgs_by_lead.setdefault(msg["lead_id"], []).append(msg)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()

    for contact in sorted(contacts, key=lambda c: (c.get("name") or "", str(c["id"]))):
        base = {
            "contact_id": str(contact["id"]),
            "phone": contact.get("phone"),
            "name": contact.get("name"),
            "contact_type": contact.get("contact_type"),
            "contact_type_source": contact.get("contact_type_source"),
            "crm_external_id": contact.get("crm_external_id"),
            "call_count": contact.get("call_count"),
            "lead_count": contact.get("lead_count"),
            "known_facts": _jsonify(contact.get("known_facts")),
            "summary": contact.get("summary"),
        }
        contact_leads = sorted(
            leads_by_contact.get(contact["id"], []), key=lambda ld: ld["created_at"]
        )
        if not contact_leads:
            writer.writerow(base)
            continue
        for lead in contact_leads:
            writer.writerow(
                {
                    **base,
                    "lead_id": str(lead["id"]),
                    "lead_created_at": lead["created_at"].isoformat(),
                    "qualification_status": lead.get("qualification_status"),
                    "classification": lead.get("classification"),
                    "service_type": lead.get("service_type"),
                    "sqft": lead.get("sqft"),
                    "budget_range": lead.get("budget_range"),
                    "timeframe": lead.get("timeframe"),
                    "outcome": lead.get("outcome"),
                    "recovered_value": lead.get("recovered_value"),
                    "transcript": _transcript(msgs_by_lead.get(lead["id"], [])),
                }
            )
    return buffer.getvalue()


async def export_contacts(conn: Any, client_id: UUID) -> str:
    """Fetch a client's contacts, leads, and messages and render the CSV.

    Uses a service-role connection (this is an admin/off-boarding tool): the
    explicit client_id filter on every query is the tenant boundary.
    """
    contacts = await conn.fetch(
        "SELECT * FROM contacts WHERE client_id = $1 ORDER BY name", client_id
    )
    leads = await conn.fetch(
        """
        SELECT id, contact_id, created_at, qualification_status, classification,
               service_type, sqft, budget_range, timeframe, outcome, recovered_value
        FROM leads
        WHERE client_id = $1 AND contact_id IS NOT NULL
        ORDER BY created_at
        """,
        client_id,
    )
    messages = await conn.fetch(
        "SELECT lead_id, direction, body, created_at FROM messages WHERE client_id = $1",
        client_id,
    )
    return build_csv(
        [dict(r) for r in contacts],
        [dict(r) for r in leads],
        [dict(r) for r in messages],
    )


async def _fetch_csv(dsn: str, client_id: UUID) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )
        return await export_contacts(conn, client_id)
    finally:
        await conn.close()


def main() -> int:
    if len(sys.argv) < 2:
        print("ERROR: usage: python scripts/export_contacts.py <client_id> [out.csv]", file=sys.stderr)
        return 1
    dsn = os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("ERROR: SUPABASE_DB_URL environment variable is required", file=sys.stderr)
        return 1
    try:
        client_id = UUID(sys.argv[1])
    except ValueError:
        print(f"ERROR: invalid client_id: {sys.argv[1]!r}", file=sys.stderr)
        return 1
    out_path = sys.argv[2] if len(sys.argv) > 2 else None

    csv_text = asyncio.run(_fetch_csv(dsn, client_id))

    if out_path:
        with open(out_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(csv_text)
        print(f"Wrote {out_path} ({len(csv_text)} bytes)")
    else:
        sys.stdout.write(csv_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
