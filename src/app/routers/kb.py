"""Knowledge base CRUD endpoints.

All endpoints require a valid Supabase JWT (verified via JWKS). The
tenant context is established by the resolver middleware for any route
that carries a client_id in the path; admin/portal routes operate on
the authenticated user's accessible clients.

Endpoints are tenant-scoped via Row Level Security on kb_entries —
even if a buggy query forgot a client_id filter, RLS would deny
cross-tenant reads.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import get_connection
from app.middleware.auth import require_permission, verify_jwt
from app.models.kb import KBEntryCreate, KBEntryList, KBEntryRead, KBEntryUpdate

router = APIRouter(
    prefix="/api/kb",
    tags=["kb"],
    dependencies=[Depends(verify_jwt)],
)


@router.get("/", response_model=KBEntryList)
async def list_entries(
    category: str | None = Query(default=None),
    source: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> KBEntryList:
    """List KB entries for the current tenant. RLS ensures tenant isolation."""
    async with get_connection() as conn:
        params: list[Any] = []
        where_clauses: list[str] = []

        if category:
            params.append(category)
            where_clauses.append(f"category = ${len(params)}")
        if source:
            params.append(source)
            where_clauses.append(f"source = ${len(params)}")

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        count_row = await conn.fetchrow(f"SELECT count(*) AS c FROM kb_entries{where_sql}", *params)
        total = int(count_row["c"]) if count_row else 0

        params.extend([limit, offset])
        rows = await conn.fetch(
            f"""
            SELECT id, client_id, question, answer, category, tags, source, created_at, updated_at
            FROM kb_entries{where_sql}
            ORDER BY created_at DESC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )

    return KBEntryList(
        data=[KBEntryRead(**dict(r)) for r in rows],
        count=total,
    )


@router.get("/{entry_id}", response_model=KBEntryRead)
async def get_entry(entry_id: int) -> KBEntryRead:
    async with get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, client_id, question, answer, category, tags, source, created_at, updated_at
            FROM kb_entries WHERE id = $1
            """,
            entry_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"KB entry {entry_id} not found")
    return KBEntryRead(**dict(row))


@router.post(
    "/",
    response_model=KBEntryRead,
    status_code=201,
    dependencies=[Depends(require_permission("can_edit_kb"))],
)
async def create_entry(entry: KBEntryCreate) -> KBEntryRead:
    """Insert a new entry. client_id comes from the tenant context (RLS)."""
    async with get_connection() as conn:
        # client_id is implicit — the RLS-bound session variable. We
        # still need to write it explicitly so the row is valid; pull it
        # from the session config we set in middleware.
        client_row = await conn.fetchrow(
            "SELECT current_setting('app.current_client_id', true)::uuid AS client_id"
        )
        if client_row is None or client_row["client_id"] is None:
            raise HTTPException(status_code=400, detail="missing tenant context")
        client_id = client_row["client_id"]

        row = await conn.fetchrow(
            """
            INSERT INTO kb_entries (client_id, question, answer, category, tags, source)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, client_id, question, answer, category, tags, source, created_at, updated_at
            """,
            client_id,
            entry.question,
            entry.answer,
            entry.category,
            entry.tags,
            entry.source,
        )
    if row is None:
        raise HTTPException(status_code=500, detail="failed to create kb entry")
    return KBEntryRead(**dict(row))


@router.patch(
    "/{entry_id}",
    response_model=KBEntryRead,
    dependencies=[Depends(require_permission("can_edit_kb"))],
)
async def update_entry(entry_id: int, updates: KBEntryUpdate) -> KBEntryRead:
    update_data = updates.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="no fields to update")

    set_clauses = []
    params: list[Any] = []
    for i, (key, value) in enumerate(update_data.items(), start=1):
        set_clauses.append(f"{key} = ${i}")
        params.append(value)
    params.append(entry_id)

    sql = f"""
        UPDATE kb_entries
        SET {", ".join(set_clauses)}
        WHERE id = ${len(params)}
        RETURNING id, client_id, question, answer, category, tags, source, created_at, updated_at
    """

    async with get_connection() as conn:
        row = await conn.fetchrow(sql, *params)

    if row is None:
        raise HTTPException(status_code=404, detail=f"KB entry {entry_id} not found")
    return KBEntryRead(**dict(row))


@router.delete(
    "/{entry_id}",
    status_code=204,
    dependencies=[Depends(require_permission("can_delete_kb"))],
)
async def delete_entry(entry_id: int) -> None:
    async with get_connection() as conn:
        result = await conn.execute("DELETE FROM kb_entries WHERE id = $1", entry_id)

    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail=f"KB entry {entry_id} not found")
