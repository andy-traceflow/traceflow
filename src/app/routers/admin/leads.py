"""Admin lead views + actions: list, detail, conversation (read-only) and
the three sanctioned actions — repush to CRM, record outcome (ADR-0003
owner report), mark-as-test. No free editing of lead rows, ever.

THE ISOLATION INVARIANT: service connection bypasses RLS — every statement
filters by the path's client_id; a lead under a different client is a 404,
indistinguishable from absent.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.adapters.registry import get_adapter
from app.db import get_service_connection
from app.models.client_config import ClientConfig
from app.models.lead import Lead, LeadOutcome
from app.services.admin_auth import AdminInfo, forbid_demo_writes, require_admin_user
from app.services.audit import record_audit_event

from .schemas import (
    ConversationMessage,
    IntentInfo,
    LeadDetailOut,
    LeadListItem,
    LeadListOut,
    LeadOutcomeIn,
    MarkTestIn,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin_user), Depends(forbid_demo_writes)])

_CLASSIFICATION_FILTER = "^(potential_lead|existing_customer|known_non_lead|spam|all)$"


# ===========================================================================
# Read views
# ===========================================================================


@router.get("/clients/{client_id}/leads", response_model=LeadListOut)
async def list_leads(
    client_id: UUID,
    classification: str = Query(default="potential_lead", pattern=_CLASSIFICATION_FILTER),
    include_test: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> LeadListOut:
    """Genuine leads first by default — pass classification=all for the
    full stream (matches the digest/report denominator semantics)."""
    async with get_service_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT l.id, l.created_at, l.contact_name, l.phone, l.email,
                   l.classification, l.qualification_status, l.qualification_score,
                   l.service_type, l.budget_range, l.timeframe, l.outcome,
                   l.recovered_value, l.external_id, l.pushed_to_crm_at, l.is_test,
                   (SELECT count(*) FROM messages m WHERE m.lead_id = l.id) AS message_count,
                   (SELECT max(m.created_at) FROM messages m WHERE m.lead_id = l.id)
                       AS last_message_at
            FROM leads l
            WHERE l.client_id = $1
              AND ($2 = 'all' OR l.classification = $2)
              AND (l.is_test = FALSE OR $3)
            ORDER BY l.created_at DESC
            LIMIT $4 OFFSET $5
            """,
            client_id,
            classification,
            include_test,
            limit,
            offset,
        )
        total = await conn.fetchval(
            """
            SELECT count(*)
            FROM leads l
            WHERE l.client_id = $1
              AND ($2 = 'all' OR l.classification = $2)
              AND (l.is_test = FALSE OR $3)
            """,
            client_id,
            classification,
            include_test,
        )
    return LeadListOut(
        data=[LeadListItem(**dict(r)) for r in rows],
        count=int(total or 0),
    )


@router.get("/clients/{client_id}/leads/{lead_id}", response_model=LeadDetailOut)
async def get_lead(client_id: UUID, lead_id: UUID) -> LeadDetailOut:
    async with get_service_connection() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM leads WHERE id = $1 AND client_id = $2", lead_id, client_id
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found")

        # Post-reply intent lives on the event stream, not the lead row.
        intent_row = await conn.fetchrow(
            """
            SELECT payload, created_at FROM events
            WHERE client_id = $1 AND lead_id = $2 AND event_type = 'intent_classified'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            client_id,
            lead_id,
        )
        message_count = await conn.fetchval(
            "SELECT count(*) FROM messages WHERE client_id = $1 AND lead_id = $2",
            client_id,
            lead_id,
        )

    intent = None
    if intent_row is not None:
        payload = intent_row["payload"] or {}
        intent = IntentInfo(
            intent=payload.get("intent"),
            proceeded=payload.get("proceeded"),
            at=intent_row["created_at"],
        )
    return LeadDetailOut(**dict(row), intent=intent, message_count=int(message_count or 0))


@router.get(
    "/clients/{client_id}/leads/{lead_id}/conversation",
    response_model=list[ConversationMessage],
)
async def get_conversation(client_id: UUID, lead_id: UUID) -> list[ConversationMessage]:
    async with get_service_connection() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM leads WHERE id = $1 AND client_id = $2", lead_id, client_id
        )
        if exists is None:
            raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found")
        rows = await conn.fetch(
            """
            SELECT id, direction, channel, body, ai_generated, prompt_version, created_at
            FROM messages
            WHERE client_id = $1 AND lead_id = $2
            ORDER BY created_at ASC
            """,
            client_id,
            lead_id,
        )
    return [ConversationMessage(**dict(r)) for r in rows]


# ===========================================================================
# Actions
# ===========================================================================


@router.post("/clients/{client_id}/leads/{lead_id}/repush")
async def repush_lead(
    client_id: UUID, lead_id: UUID, admin: AdminInfo = Depends(require_admin_user)
) -> dict[str, Any]:
    """Re-sync a lead to its client's CRM.

    If the lead has no external_id, runs the adapter's push_lead — same
    code path as the original push, just invoked manually (handles the
    "original push failed" case). If the lead already has an external_id,
    runs update_lead with the current canonical fields so the CRM record
    reflects the latest qualifier extractions. Never creates a duplicate
    CRM record.
    """
    async with get_service_connection() as conn:
        lead_row = await conn.fetchrow(
            "SELECT * FROM leads WHERE id = $1 AND client_id = $2", lead_id, client_id
        )
        if lead_row is None:
            raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found")
        lead = Lead(**dict(lead_row))

        config_row = await conn.fetchrow(
            "SELECT * FROM client_configs WHERE client_id = $1", client_id
        )
        if config_row is None:
            raise HTTPException(
                status_code=400, detail=f"No client_config for client {client_id}"
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
            """
            UPDATE leads SET external_id = $1, pushed_to_crm_at = NOW()
            WHERE id = $2 AND client_id = $3
            """,
            new_external_id,
            lead_id,
            client_id,
        )

    await record_audit_event(
        client_id=client_id,
        operation="sync",
        actor=admin.email,
        actor_user_id=admin.id,
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
            "client_id": str(client_id),
            "provider": config.crm_provider,
            "action": action,
            "admin": admin.email,
        },
    )
    return {
        "lead_id": str(lead_id),
        "client_id": str(client_id),
        "provider": config.crm_provider,
        "action": action,
        "external_id": new_external_id,
    }


@router.post("/clients/{client_id}/leads/{lead_id}/outcome")
async def record_lead_outcome(
    client_id: UUID,
    lead_id: UUID,
    body: LeadOutcomeIn,
    admin: AdminInfo = Depends(require_admin_user),
) -> dict[str, Any]:
    """Record a lead's booked outcome + recovered revenue (owner report).

    The universal capture path — works for every client, CRM or not. It
    writes the same columns the revenue_sync CRM readback does, but with
    outcome_source='owner_report' by default (ADR-0003): the founder records
    it from the admin UI, e.g. after the monthly review. A 'won' outcome
    must carry a recovered_value.
    """
    if body.outcome == LeadOutcome.won and body.recovered_value is None:
        raise HTTPException(
            status_code=400, detail="recovered_value is required when outcome is 'won'"
        )

    async with get_service_connection() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM leads WHERE id = $1 AND client_id = $2", lead_id, client_id
        )
        if exists is None:
            raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found")
        await conn.execute(
            """
            UPDATE leads
            SET outcome = $1, recovered_value = $2, outcome_source = $3,
                outcome_recorded_at = NOW()
            WHERE id = $4 AND client_id = $5
            """,
            body.outcome.value,
            body.recovered_value,
            body.source.value,
            lead_id,
            client_id,
        )

    recovered_str = str(body.recovered_value) if body.recovered_value is not None else None
    await record_audit_event(
        client_id=client_id,
        operation="update",
        actor=admin.email,
        actor_user_id=admin.id,
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
            "admin": admin.email,
        },
    )
    return {
        "lead_id": str(lead_id),
        "client_id": str(client_id),
        "outcome": body.outcome.value,
        "recovered_value": recovered_str,
        "source": body.source.value,
    }


@router.post("/clients/{client_id}/leads/{lead_id}/mark-test")
async def mark_lead_test(
    client_id: UUID,
    lead_id: UUID,
    body: MarkTestIn,
    admin: AdminInfo = Depends(require_admin_user),
) -> dict[str, Any]:
    """Flag (or unflag) a lead as a test so it stays out of every metric —
    digest, monthly report, revenue sync, and the admin views' defaults."""
    async with get_service_connection() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM leads WHERE id = $1 AND client_id = $2", lead_id, client_id
        )
        if exists is None:
            raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found")
        await conn.execute(
            "UPDATE leads SET is_test = $1 WHERE id = $2 AND client_id = $3",
            body.is_test,
            lead_id,
            client_id,
        )

    await record_audit_event(
        client_id=client_id,
        operation="update",
        actor=admin.email,
        actor_user_id=admin.id,
        target_table="leads",
        target_id=str(lead_id),
        snapshot={"is_test": body.is_test},
    )
    return {"lead_id": str(lead_id), "is_test": body.is_test}


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
