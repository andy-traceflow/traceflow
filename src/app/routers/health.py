"""GET /health — what the maintenance retainer monitors.

Reports database connectivity, the destination's `health_check()`, event
counts by status (including `dead`), and the timestamp of the most
recent successful delivery.

Status codes: 503 only when the database is unreachable (the service
genuinely cannot do its job — Render should restart it). A destination
outage returns 200 with `"status": "degraded"`: restarting us would not
fix Notion, and events are safe in the table meanwhile.

The destination check makes a real network call, so its result is
cached for DESTINATION_CHECK_TTL_SECONDS — Render polls this path often
enough that hitting Notion/Monday on every poll would burn rate limit.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.adapters.registry import get_adapter
from app.config import get_settings
from app.services.events import EventStore, get_event_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

DESTINATION_CHECK_TTL_SECONDS = 60.0
DESTINATION_CHECK_TIMEOUT_SECONDS = 10.0

_destination_cache: dict[str, object] = {"checked_at": 0.0, "ok": None}


def reset_destination_cache() -> None:
    """Tests only."""
    _destination_cache.update(checked_at=0.0, ok=None)


async def _destination_ok(destination_name: str) -> bool:
    now = time.monotonic()
    cached = _destination_cache["ok"]
    if cached is not None and now - float(_destination_cache["checked_at"]) < DESTINATION_CHECK_TTL_SECONDS:  # type: ignore[arg-type]
        return bool(cached)
    try:
        adapter = get_adapter(destination_name)
        ok = await asyncio.wait_for(adapter.health_check(), timeout=DESTINATION_CHECK_TIMEOUT_SECONDS)
    except Exception as e:
        logger.warning("destination health check failed", exc_info=e)
        ok = False
    _destination_cache.update(checked_at=now, ok=ok)
    return ok


@router.get("/health")
async def health(store: EventStore = Depends(get_event_store)) -> JSONResponse:
    settings = get_settings()

    db_ok = True
    counts: dict[str, int] = {}
    last_delivered: datetime | None = None
    try:
        counts = await store.count_by_status()
        last_delivered = await store.last_delivered_at()
    except Exception as e:
        logger.error("database health check failed", exc_info=e)
        db_ok = False

    destination_ok = await _destination_ok(settings.destination)

    body = {
        "status": "ok" if db_ok and destination_ok else "degraded",
        "environment": settings.environment,
        "git_commit": os.getenv("RENDER_GIT_COMMIT", "unknown")[:7],
        "destination": settings.destination,
        "checks": {"database": db_ok, "destination": destination_ok},
        "events": counts,
        "dead_events": counts.get("dead", 0) if db_ok else None,
        "last_delivered_at": last_delivered.isoformat() if last_delivered else None,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    return JSONResponse(status_code=200 if db_ok else 503, content=body)
