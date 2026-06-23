"""Field-mapping CRUD (Layer 2 of the integration model).

Mappings translate canonical Lead fields to each integration's field names;
they're resolved fresh per push (services/field_mappings.resolve_mappings
does not cache), so edits here take effect on the next adapter call with no
invalidation step.

ISOLATION INVARIANT: service connection bypasses RLS — every statement
filters by the path's client_id.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import get_service_connection
from app.services.admin_auth import AdminInfo, forbid_demo_writes, require_admin_user
from app.services.audit import record_audit_event

from .schemas import FieldMappingIn, FieldMappingOut

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin_user), Depends(forbid_demo_writes)])


@router.get("/clients/{client_id}/field-mappings", response_model=list[FieldMappingOut])
async def list_field_mappings(
    client_id: UUID,
    integration: str | None = Query(default=None),
) -> list[FieldMappingOut]:
    async with get_service_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT integration, canonical_field, external_field, external_field_type,
                   transform, notes, updated_at
            FROM client_field_mappings
            WHERE client_id = $1
              AND ($2::text IS NULL OR integration = $2)
            ORDER BY integration, canonical_field
            """,
            client_id,
            integration,
        )
    return [FieldMappingOut(**dict(r)) for r in rows]


@router.put("/clients/{client_id}/field-mappings", response_model=FieldMappingOut)
async def upsert_field_mapping(
    client_id: UUID,
    body: FieldMappingIn,
    admin: AdminInfo = Depends(require_admin_user),
) -> FieldMappingOut:
    """Insert or replace the mapping for (integration, canonical_field)."""
    async with get_service_connection() as conn:
        exists = await conn.fetchval("SELECT 1 FROM clients WHERE id = $1", client_id)
        if exists is None:
            raise HTTPException(status_code=404, detail=f"Client {client_id} not found")
        row = await conn.fetchrow(
            """
            INSERT INTO client_field_mappings
                (client_id, integration, canonical_field, external_field,
                 external_field_type, transform, notes)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (client_id, integration, canonical_field) DO UPDATE
                SET external_field = EXCLUDED.external_field,
                    external_field_type = EXCLUDED.external_field_type,
                    transform = EXCLUDED.transform,
                    notes = EXCLUDED.notes
            RETURNING integration, canonical_field, external_field, external_field_type,
                      transform, notes, updated_at
            """,
            client_id,
            body.integration,
            body.canonical_field,
            body.external_field,
            body.external_field_type,
            body.transform,
            body.notes,
        )

    await record_audit_event(
        client_id=client_id,
        operation="update",
        actor=admin.email,
        actor_user_id=admin.id,
        target_table="client_field_mappings",
        target_id=f"{body.integration}:{body.canonical_field}",
        snapshot=body.model_dump(mode="json"),
    )
    logger.info(
        "admin field-mapping upsert",
        extra={
            "client_id": str(client_id),
            "integration": body.integration,
            "canonical_field": body.canonical_field,
            "admin": admin.email,
        },
    )
    return FieldMappingOut(**dict(row))


@router.delete("/clients/{client_id}/field-mappings/{integration}/{canonical_field}")
async def delete_field_mapping(
    client_id: UUID,
    integration: str,
    canonical_field: str,
    admin: AdminInfo = Depends(require_admin_user),
) -> dict[str, Any]:
    async with get_service_connection() as conn:
        # Fetch first so the audit snapshot preserves the deleted state.
        row = await conn.fetchrow(
            """
            SELECT integration, canonical_field, external_field, external_field_type,
                   transform, notes
            FROM client_field_mappings
            WHERE client_id = $1 AND integration = $2 AND canonical_field = $3
            """,
            client_id,
            integration,
            canonical_field,
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"No mapping for {integration}:{canonical_field}",
            )
        await conn.execute(
            """
            DELETE FROM client_field_mappings
            WHERE client_id = $1 AND integration = $2 AND canonical_field = $3
            """,
            client_id,
            integration,
            canonical_field,
        )

    await record_audit_event(
        client_id=client_id,
        operation="delete",
        actor=admin.email,
        actor_user_id=admin.id,
        target_table="client_field_mappings",
        target_id=f"{integration}:{canonical_field}",
        snapshot=dict(row),
    )
    return {"deleted": True, "integration": integration, "canonical_field": canonical_field}
