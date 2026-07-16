"""Admin contact views + the one sanctioned write: manual retype.

The contact is the durable caller identity (migrations 018/019). These views
let the operator see who's calling and, when needed, correct the type by hand.

THE ISOLATION INVARIANT: the service connection bypasses RLS — every statement
filters by the path's client_id; a contact under another client is a 404,
indistinguishable from absent.

Manual retype is the ONLY path that writes contact_type_source='manual' and the
only way to set 'blocked'. It goes through services.contacts.set_contact_type,
which enforces precedence (manual wins) and drops a contact_type_changed event;
the change is also audit-logged with the admin's email.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import get_service_connection
from app.models.contact import ContactType, ContactTypeSource
from app.services.admin_auth import AdminInfo, forbid_demo_writes, require_admin_user
from app.services.audit import record_audit_event
from app.services.contacts import set_contact_type

from .schemas import (
    ContactDetailOut,
    ContactLeadItem,
    ContactListItem,
    ContactListOut,
    ContactRetypeIn,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin_user), Depends(forbid_demo_writes)])

_TYPE_FILTER = "^(unknown|prospect|customer|vendor|spam|blocked|all)$"


@router.get("/clients/{client_id}/contacts", response_model=ContactListOut)
async def list_contacts(
    client_id: UUID,
    contact_type: str = Query(default="all", pattern=_TYPE_FILTER),
    search: str = Query(default="", max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ContactListOut:
    """List a client's contacts, filterable by type and searchable by
    phone/name. The UI groups these (New/Existing/Vendors/Filtered); the model
    keeps all six types."""
    async with get_service_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, phone, name, contact_type, contact_type_source,
                   call_count, lead_count, last_seen_at, summary
            FROM contacts
            WHERE client_id = $1
              AND ($2 = 'all' OR contact_type = $2)
              AND ($3 = '' OR phone ILIKE '%' || $3 || '%' OR name ILIKE '%' || $3 || '%')
            ORDER BY last_seen_at DESC
            LIMIT $4 OFFSET $5
            """,
            client_id,
            contact_type,
            search,
            limit,
            offset,
        )
        total = await conn.fetchval(
            """
            SELECT count(*)
            FROM contacts
            WHERE client_id = $1
              AND ($2 = 'all' OR contact_type = $2)
              AND ($3 = '' OR phone ILIKE '%' || $3 || '%' OR name ILIKE '%' || $3 || '%')
            """,
            client_id,
            contact_type,
            search,
        )
    return ContactListOut(
        data=[ContactListItem(**dict(r)) for r in rows],
        count=int(total or 0),
    )


@router.get("/clients/{client_id}/contacts/{contact_id}", response_model=ContactDetailOut)
async def get_contact(client_id: UUID, contact_id: UUID) -> ContactDetailOut:
    """A contact with its full lead history (completeness + value scores)."""
    async with get_service_connection() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM contacts WHERE id = $1 AND client_id = $2", contact_id, client_id
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"Contact {contact_id} not found")
        lead_rows = await conn.fetch(
            """
            SELECT id, created_at, qualification_status, classification, service_type,
                   qualification_score, value_score, outcome, recovered_value
            FROM leads
            WHERE contact_id = $1 AND client_id = $2
            ORDER BY created_at DESC
            """,
            contact_id,
            client_id,
        )
    return ContactDetailOut(
        **dict(row),
        leads=[ContactLeadItem(**dict(r)) for r in lead_rows],
    )


@router.patch("/clients/{client_id}/contacts/{contact_id}")
async def retype_contact(
    client_id: UUID,
    contact_id: UUID,
    body: ContactRetypeIn,
    admin: AdminInfo = Depends(require_admin_user),
) -> dict[str, Any]:
    """Manually set a contact's type. The only writer of source='manual' and the
    only way to set 'blocked'. Precedence-safe (manual always wins) and
    audit-logged."""
    async with get_service_connection() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM contacts WHERE id = $1 AND client_id = $2", contact_id, client_id
        )
        if exists is None:
            raise HTTPException(status_code=404, detail=f"Contact {contact_id} not found")
        applied = await set_contact_type(
            conn,
            contact_id,
            ContactType(body.contact_type),
            ContactTypeSource.manual,
            reason=body.reason,
        )

    await record_audit_event(
        client_id=client_id,
        operation="update",
        actor=admin.email,
        actor_user_id=admin.id,
        target_table="contacts",
        target_id=str(contact_id),
        snapshot={"contact_type": body.contact_type, "reason": body.reason, "applied": applied},
    )
    logger.info(
        "admin contact retype",
        extra={
            "client_id": str(client_id),
            "contact_id": str(contact_id),
            "contact_type": body.contact_type,
            "admin": admin.email,
        },
    )
    return {
        "contact_id": str(contact_id),
        "contact_type": body.contact_type,
        "source": "manual",
        "applied": applied,
    }
