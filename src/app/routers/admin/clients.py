"""Admin client views: list/switcher, config read + partial write, AI usage.

THE ISOLATION INVARIANT (read before adding any endpoint here): these
handlers use get_service_connection(), which BYPASSES RLS — the explicit
client_id filter in every SQL statement is the only tenant isolation. Never
rely on RLS here, never omit the filter.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.db import get_service_connection
from app.services.admin_auth import AdminInfo, require_admin_user
from app.services.audit import record_audit_event

from .schemas import (
    AIUsageOut,
    ClassificationConfig,
    ClientConfigAdminOut,
    ClientConfigUpdate,
    ClientListItem,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin_user)])

# Columns selected for the config payload — explicit list (clients.updated_at
# would collide with client_configs.updated_at under SELECT *).
_CONFIG_SELECT = """
SELECT c.id AS client_id, c.slug, c.business_name, c.status, c.tier, c.timezone,
       cc.business_hours, cc.service_area_zips, cc.twilio_number, cc.vip_keywords,
       cc.vip_value_threshold, cc.crm_provider, cc.crm_credentials,
       cc.webhook_signing_secrets, cc.qualification_prompt, cc.greeting_template,
       cc.prompt_versions, cc.ai_interaction_cap_monthly, cc.ai_interactions_used,
       cc.ai_period_resets_at, cc.brand, cc.notification_emails, cc.owner_alert_emails,
       cc.owner_alert_phones, cc.feature_flags, cc.classification_config,
       cc.existing_customer_alert_contact, cc.vendor_allowlist, cc.revenue_config,
       cc.updated_at
FROM clients c
JOIN client_configs cc ON cc.client_id = c.id
WHERE c.id = $1
"""


@router.get("/clients", response_model=list[ClientListItem])
async def list_clients() -> list[ClientListItem]:
    """The switcher. The one intentionally cross-tenant query in the admin
    surface — names and operational basics only, no lead data."""
    async with get_service_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.slug, c.business_name, c.status, c.tier, c.timezone,
                   c.launched_at, c.created_at, cc.crm_provider, cc.twilio_number,
                   (SELECT count(*) FROM leads l
                     WHERE l.client_id = c.id
                       AND l.created_at >= now() - interval '30 days'
                       AND l.is_test = FALSE) AS leads_30d
            FROM clients c
            LEFT JOIN client_configs cc ON cc.client_id = c.id
            ORDER BY c.business_name
            """
        )
    return [ClientListItem(**dict(r)) for r in rows]


def _config_out(row: Any) -> ClientConfigAdminOut:
    data = dict(row)
    crm_credentials = data.pop("crm_credentials", None)
    signing_secrets = data.pop("webhook_signing_secrets", None) or {}
    data["classification_config"] = ClassificationConfig(
        **(data.get("classification_config") or {})
    )
    return ClientConfigAdminOut(
        **{**data, "business_hours": data.get("business_hours") or {}},
        has_crm_credentials=bool(crm_credentials),
        webhook_integrations=sorted(signing_secrets.keys()),
    )


@router.get("/clients/{client_id}/config", response_model=ClientConfigAdminOut)
async def get_client_config(client_id: UUID) -> ClientConfigAdminOut:
    async with get_service_connection() as conn:
        row = await conn.fetchrow(_CONFIG_SELECT, client_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found")
    return _config_out(row)


@router.put("/clients/{client_id}/config", response_model=ClientConfigAdminOut)
async def update_client_config(
    client_id: UUID,
    body: ClientConfigUpdate,
    admin: AdminInfo = Depends(require_admin_user),
) -> ClientConfigAdminOut:
    """Partial update: only fields present in the request are written.
    A provided null clears the column (e.g. {"greeting_template": null}).
    timezone lands on clients; everything else on client_configs."""
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided")
    if "classification_config" in updates and body.classification_config is not None:
        # exclude_unset propagates into nested models — re-dump the validated
        # block in full so the stored JSONB always carries every toggle
        # (matching migration 013's materialized default), never a partial
        # whose behavior would drift if code defaults ever change.
        updates["classification_config"] = body.classification_config.model_dump()
    timezone = updates.pop("timezone", None)

    async with get_service_connection() as conn:
        exists = await conn.fetchval("SELECT 1 FROM clients WHERE id = $1", client_id)
        if exists is None:
            raise HTTPException(status_code=404, detail=f"Client {client_id} not found")

        try:
            if timezone is not None:
                await conn.execute(
                    "UPDATE clients SET timezone = $2 WHERE id = $1", client_id, timezone
                )
            if updates:
                # Column names come from the validated model's field set
                # (extra="forbid"), never from raw request keys — safe to
                # interpolate. updated_at bumps via the table trigger.
                sets = ", ".join(f"{col} = ${i}" for i, col in enumerate(updates, start=2))
                await conn.execute(
                    f"UPDATE client_configs SET {sets} WHERE client_id = $1",
                    client_id,
                    *updates.values(),
                )
        except asyncpg.exceptions.UniqueViolationError as e:
            # twilio_number is globally unique — surface as a conflict, not a 500.
            raise HTTPException(
                status_code=409, detail="twilio_number already assigned to another client"
            ) from e

        row = await conn.fetchrow(_CONFIG_SELECT, client_id)

    changed = sorted(set(updates) | ({"timezone"} if timezone is not None else set()))
    await record_audit_event(
        client_id=client_id,
        operation="update",
        actor=admin.email,
        actor_user_id=admin.id,
        target_table="client_configs",
        target_id=str(client_id),
        snapshot={"fields": changed, "values": body.model_dump(mode="json", exclude_unset=True)},
    )
    logger.info(
        "admin config update",
        extra={"client_id": str(client_id), "fields": changed, "admin": admin.email},
    )
    if row is None:  # client_configs row missing despite clients row — data bug
        raise HTTPException(status_code=404, detail=f"No client_config for {client_id}")
    return _config_out(row)


@router.get("/clients/{client_id}/ai-usage", response_model=AIUsageOut)
async def get_ai_usage(client_id: UUID) -> AIUsageOut:
    async with get_service_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT ai_interaction_cap_monthly, ai_interactions_used, ai_period_resets_at
            FROM client_configs
            WHERE client_id = $1
            """,
            client_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found")
    return _usage_out(row)


@router.post("/clients/{client_id}/ai-usage/reset", response_model=AIUsageOut)
async def reset_ai_usage(
    client_id: UUID, admin: AdminInfo = Depends(require_admin_user)
) -> AIUsageOut:
    """Zero the month's AI interaction counter (e.g. after a billing
    adjustment or a runaway test). Cap and reset date are untouched."""
    async with get_service_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT ai_interaction_cap_monthly, ai_interactions_used, ai_period_resets_at
            FROM client_configs
            WHERE client_id = $1
            """,
            client_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"Client {client_id} not found")
        previous_used = row["ai_interactions_used"]
        await conn.execute(
            "UPDATE client_configs SET ai_interactions_used = 0 WHERE client_id = $1",
            client_id,
        )

    await record_audit_event(
        client_id=client_id,
        operation="update",
        actor=admin.email,
        actor_user_id=admin.id,
        target_table="client_configs",
        target_id=str(client_id),
        snapshot={"ai_interactions_used": 0, "previous_used": previous_used},
    )
    return _usage_out({**dict(row), "ai_interactions_used": 0})


def _usage_out(row: Any) -> AIUsageOut:
    cap = row["ai_interaction_cap_monthly"]
    used = row["ai_interactions_used"]
    return AIUsageOut(
        cap=cap,
        used=used,
        remaining=max(cap - used, 0),
        percent_used=round(used / cap * 100, 1) if cap > 0 else 0.0,
        resets_at=row["ai_period_resets_at"],
    )
