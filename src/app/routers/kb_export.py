"""KB CSV export endpoint.

Tenant-scoped: a client downloads only their own entries. The CSV
format is the universal `question,answer` shape — most chatbot
vendors (and bulk-import tools) accept it. Per-vendor variations can
be handled by query-string options later.
"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.db import get_connection
from app.middleware.auth import require_permission, verify_jwt

router = APIRouter(prefix="/api/kb", tags=["kb"], dependencies=[Depends(verify_jwt)])


@router.get(
    "/export.csv",
    dependencies=[Depends(require_permission("can_export"))],
)
async def export_csv(
    include_metadata: bool = Query(default=False, description="If true, include category/tags/source columns"),
) -> StreamingResponse:
    """Stream the current tenant's KB as CSV."""
    async with get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT question, answer, category, tags, source
            FROM kb_entries
            ORDER BY id
            """
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    if include_metadata:
        writer.writerow(["question", "answer", "category", "tags", "source"])
        for r in rows:
            writer.writerow([
                r["question"],
                r["answer"],
                r["category"],
                ";".join(r["tags"] or []),
                r["source"],
            ])
    else:
        writer.writerow(["question", "answer"])
        for r in rows:
            writer.writerow([r["question"], r["answer"]])

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=kb_entries.csv"},
    )
