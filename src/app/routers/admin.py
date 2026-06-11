"""Founder-only admin endpoints — the Retool admin app talks to these.

Auth: a bearer token verified by middleware.auth.verify_admin_token
(HS256 against ADMIN_JWT_SECRET). Cross-tenant by design — admin ops
operate across clients and are not RLS-scoped. The DB access goes
through get_service_connection (bypasses RLS) for the same reason.

Most Retool panels read/write Postgres directly with admin credentials;
only operations that need application logic (re-push to CRM via the
registered adapter) live here.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.adapters.registry import get_adapter
from app.db import get_service_connection
from app.middleware.auth import verify_admin_token
from app.models.client_config import ClientConfig
from app.models.lead import Lead, LeadOutcome, OutcomeSource
from app.services.audit import record_audit_event

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(verify_admin_token)],
)


@router.post("/leads/{lead_id}/repush")
async def repush_lead(lead_id: UUID) -> dict[str, Any]:
    """Re-sync a lead to its client's CRM.

    If the lead has no external_id, runs the adapter's push_lead — same
    code path as the original push, just invoked manually (handles the
    "original push failed" case). If the lead already has an external_id,
    runs update_lead with the current canonical fields so the CRM record
    reflects the latest qualifier extractions. Never creates a duplicate
    CRM record.
    """
    async with get_service_connection() as conn:
        lead_row = await conn.fetchrow("SELECT * FROM leads WHERE id = $1", lead_id)
        if lead_row is None:
            raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found")
        lead = Lead(**dict(lead_row))

        config_row = await conn.fetchrow(
            "SELECT * FROM client_configs WHERE client_id = $1", lead.client_id
        )
        if config_row is None:
            raise HTTPException(
                status_code=400,
                detail=f"No client_config for lead's client {lead.client_id}",
            )
        config = ClientConfig(**dict(config_row))

    if not config.crm_provider:
        raise HTTPException(status_code=400, detail="Client has no crm_provider configured")

    try:
        adapter = get_adapter(config.crm_provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # The adapter call goes out to an external CRM API — do it outside
    # the DB transaction, then re-open a service connection to commit
    # the resulting external_id + pushed_to_crm_at.
    if lead.external_id is None:
        new_external_id = await adapter.push_lead(lead, config)
        action = "push"
    else:
        await adapter.update_lead(lead.external_id, _canonical_updates(lead), config)
        new_external_id = lead.external_id
        action = "update"

    async with get_service_connection() as conn:
        await conn.execute(
            "UPDATE leads SET external_id = $1, pushed_to_crm_at = NOW() WHERE id = $2",
            new_external_id,
            lead_id,
        )

    await record_audit_event(
        client_id=lead.client_id,
        operation="sync",
        actor="founder_retool",
        target_table="leads",
        target_id=str(lead_id),
        snapshot={
            "action": action,
            "provider": config.crm_provider,
            "external_id": new_external_id,
        },
    )

    logger.info(
        "admin re-push",
        extra={
            "lead_id": str(lead_id),
            "client_id": str(lead.client_id),
            "provider": config.crm_provider,
            "action": action,
        },
    )
    return {
        "lead_id": str(lead_id),
        "client_id": str(lead.client_id),
        "provider": config.crm_provider,
        "action": action,
        "external_id": new_external_id,
    }


class LeadOutcomeIn(BaseModel):
    outcome: LeadOutcome
    recovered_value: Decimal | None = None
    source: OutcomeSource = OutcomeSource.owner_report


@router.post("/leads/{lead_id}/outcome")
async def record_lead_outcome(lead_id: UUID, body: LeadOutcomeIn) -> dict[str, Any]:
    """Record a lead's booked outcome + recovered revenue (owner report).

    The universal capture path — works for every client, CRM or not. It writes
    the same columns the revenue_sync CRM readback does, but with
    outcome_source='owner_report' by default: the founder records it from Retool
    (e.g. after the monthly review), or it's pulled in from a client's reply.
    A 'won' outcome must carry a recovered_value.
    """
    if body.outcome == LeadOutcome.won and body.recovered_value is None:
        raise HTTPException(
            status_code=400, detail="recovered_value is required when outcome is 'won'"
        )

    async with get_service_connection() as conn:
        row = await conn.fetchrow("SELECT client_id FROM leads WHERE id = $1", lead_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found")
        client_id = row["client_id"]
        await conn.execute(
            """
            UPDATE leads
            SET outcome = $1, recovered_value = $2, outcome_source = $3,
                outcome_recorded_at = NOW()
            WHERE id = $4
            """,
            body.outcome.value,
            body.recovered_value,
            body.source.value,
            lead_id,
        )

    recovered_str = str(body.recovered_value) if body.recovered_value is not None else None
    await record_audit_event(
        client_id=client_id,
        operation="update",
        actor="founder_retool",
        target_table="leads",
        target_id=str(lead_id),
        snapshot={
            "outcome": body.outcome.value,
            "recovered_value": recovered_str,
            "source": body.source.value,
        },
    )

    logger.info(
        "admin lead outcome recorded",
        extra={
            "lead_id": str(lead_id),
            "client_id": str(client_id),
            "outcome": body.outcome.value,
            "source": body.source.value,
        },
    )
    return {
        "lead_id": str(lead_id),
        "client_id": str(client_id),
        "outcome": body.outcome.value,
        "recovered_value": recovered_str,
        "source": body.source.value,
    }


def _canonical_updates(lead: Lead) -> dict[str, Any]:
    """The lead's current canonical fields as an updates dict.

    The adapter applies whatever it has mappings for and ignores the
    rest. None and empty values are dropped so we never null out a
    field that's already set on the CRM side.
    """
    raw = {
        "contact_name": lead.contact_name,
        "contact_company": lead.contact_company,
        "phone": lead.phone,
        "email": lead.email,
        "address": lead.address,
        "service_type": lead.service_type,
        "sqft": lead.sqft,
        "budget_range": lead.budget_range,
        "timeframe": lead.timeframe,
        "notes": lead.notes,
    }
    return {k: v for k, v in raw.items() if v is not None and v != ""}
