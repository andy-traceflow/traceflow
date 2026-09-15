"""Admin onboarding review + promote — /api/admin/onboarding-submissions.

Submissions land in the onboarding_submissions staging table (from Jotform or
the future native form). The founder reviews them here and promotes a good one
into live clients + client_configs rows via the shared provisioning service.

THE ISOLATION INVARIANT (same as the rest of /api/admin): these handlers use
get_service_connection(), which BYPASSES RLS. onboarding_submissions is
service-role-locked by design (migration 022) — it is pre-tenant data with no
client_id, reachable only through this authenticated admin surface.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, ValidationError

from app.db import get_service_connection
from app.services.admin_auth import AdminInfo, forbid_demo_writes, require_admin_user
from app.services.audit import record_audit_event
from app.services.provisioning import ProvisionSpec, SlugConflictError, provision_client

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin_user), Depends(forbid_demo_writes)])

_LIST_STATUSES = ("new", "reviewed", "promoted", "rejected")


# ===========================================================================
# Schemas
# ===========================================================================


class OnboardingListItem(BaseModel):
    id: UUID
    status: str
    source: str
    business_name: str | None
    contact_email: str | None
    promoted_client_id: UUID | None
    created_at: datetime
    updated_at: datetime


class OnboardingDetailOut(OnboardingListItem):
    raw_payload: dict[str, Any]
    mapped_config: dict[str, Any]
    notes: str | None


class OnboardingUpdate(BaseModel):
    """Reviewer edits before promotion. extra='forbid' → a typo is a 422."""

    model_config = ConfigDict(extra="forbid")

    status: str | None = None  # 'reviewed' | 'rejected' (promote has its own route)
    mapped_config: dict[str, Any] | None = None
    notes: str | None = None


class PromoteOut(BaseModel):
    submission_id: UUID
    client_id: UUID


# ===========================================================================
# Routes
# ===========================================================================


@router.get("/onboarding-submissions", response_model=list[OnboardingListItem])
async def list_submissions(
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[OnboardingListItem]:
    if status_filter is not None and status_filter not in _LIST_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status_filter}")
    query = """
        SELECT id, status, source, business_name, contact_email,
               promoted_client_id, created_at, updated_at
        FROM onboarding_submissions
    """
    args: list[Any] = []
    if status_filter is not None:
        query += " WHERE status = $1"
        args.append(status_filter)
    query += " ORDER BY created_at DESC"
    async with get_service_connection() as conn:
        rows = await conn.fetch(query, *args)
    return [OnboardingListItem(**dict(r)) for r in rows]


@router.get("/onboarding-submissions/{submission_id}", response_model=OnboardingDetailOut)
async def get_submission(submission_id: UUID) -> OnboardingDetailOut:
    async with get_service_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, status, source, business_name, contact_email,
                   promoted_client_id, raw_payload, mapped_config, notes,
                   created_at, updated_at
            FROM onboarding_submissions
            WHERE id = $1
            """,
            submission_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Submission {submission_id} not found")
    return OnboardingDetailOut(**dict(row))


@router.put("/onboarding-submissions/{submission_id}", response_model=OnboardingDetailOut)
async def update_submission(
    submission_id: UUID,
    body: OnboardingUpdate,
    admin: AdminInfo = Depends(require_admin_user),
) -> OnboardingDetailOut:
    """Reviewer edits: adjust the mapped draft, add notes, or mark
    reviewed/rejected. Promotion is a separate, explicit action."""
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided")
    if "status" in updates and updates["status"] not in ("reviewed", "rejected"):
        # 'promoted' only via the promote route; 'new' isn't a manual target.
        raise HTTPException(
            status_code=400, detail="status may only be set to 'reviewed' or 'rejected' here"
        )

    async with get_service_connection() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM onboarding_submissions WHERE id = $1", submission_id
        )
        if exists is None:
            raise HTTPException(status_code=404, detail=f"Submission {submission_id} not found")
        sets = ", ".join(f"{col} = ${i}" for i, col in enumerate(updates, start=2))
        await conn.execute(
            f"UPDATE onboarding_submissions SET {sets} WHERE id = $1",
            submission_id,
            *updates.values(),
        )
        row = await conn.fetchrow(
            """
            SELECT id, status, source, business_name, contact_email,
                   promoted_client_id, raw_payload, mapped_config, notes,
                   created_at, updated_at
            FROM onboarding_submissions
            WHERE id = $1
            """,
            submission_id,
        )
    logger.info(
        "onboarding submission updated",
        extra={"submission_id": str(submission_id), "fields": sorted(updates), "admin": admin.email},
    )
    return OnboardingDetailOut(**dict(row))


@router.post("/onboarding-submissions/{submission_id}/promote", response_model=PromoteOut)
async def promote_submission(
    submission_id: UUID,
    admin: AdminInfo = Depends(require_admin_user),
) -> PromoteOut:
    """Create a live tenant from the reviewed mapped_config, atomically flip the
    submission to 'promoted', and record the actor. Idempotency: a submission
    already promoted returns 409 rather than creating a duplicate client."""
    async with get_service_connection() as conn:
        row = await conn.fetchrow(
            "SELECT status, mapped_config FROM onboarding_submissions WHERE id = $1",
            submission_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"Submission {submission_id} not found")
        if row["status"] == "promoted":
            raise HTTPException(status_code=409, detail="Submission already promoted")

        try:
            spec = ProvisionSpec(**(row["mapped_config"] or {}))
        except ValidationError as e:
            # Draft is incomplete/invalid — surface what to fix, don't 500.
            raise HTTPException(
                status_code=422, detail=f"mapped_config is not promotable: {e.errors()}"
            ) from e

        # get_service_connection() already runs this block in one transaction,
        # so the client rows + submission flip commit together (or roll back
        # together on any raise below).
        try:
            client_id = await provision_client(spec, conn=conn)
        except SlugConflictError as e:
            raise HTTPException(
                status_code=409,
                detail=f"A client with slug '{spec.slug}' (or that twilio number) already exists",
            ) from e
        await conn.execute(
            """
            UPDATE onboarding_submissions
            SET status = 'promoted', promoted_client_id = $2
            WHERE id = $1
            """,
            submission_id,
            client_id,
        )

    await record_audit_event(
        client_id=client_id,
        operation="create",
        actor=admin.email,
        actor_user_id=admin.id,
        target_table="clients",
        target_id=str(client_id),
        snapshot={"promoted_from_submission": str(submission_id), "slug": spec.slug},
    )
    logger.info(
        "onboarding submission promoted",
        extra={
            "submission_id": str(submission_id),
            "client_id": str(client_id),
            "admin": admin.email,
        },
    )
    return PromoteOut(submission_id=submission_id, client_id=client_id)
