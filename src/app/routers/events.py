"""Triage + replay, protected by one bearer token (ADMIN_TOKEN).

    GET  /events?status=dead&limit=50   newest first, payload included
    GET  /events/{id}
    POST /events/{id}/replay            dead → received, attempts reset to 0

The difference between a 10-minute fix and an hour with a database
client: when an alert fires, read the dead event here, fix the cause
(credential, renamed column, mapping), replay it.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.models.event import EventStatus
from app.services.events import EventStore, get_event_store

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


def require_admin_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Fail closed: no configured token, no header, or a mismatch → 401."""
    expected = get_settings().admin_token
    presented = credentials.credentials if credentials else ""
    if not expected or not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=401,
            detail="invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


router = APIRouter(prefix="/events", tags=["events"], dependencies=[Depends(require_admin_token)])


@router.get("")
async def list_events(
    status: EventStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    store: EventStore = Depends(get_event_store),
) -> dict[str, Any]:
    events = await store.list_events(status=status, limit=limit)
    return {
        "status": status.value if status else None,
        "count": len(events),
        "events": [e.model_dump(mode="json") for e in events],
    }


@router.get("/{event_id}")
async def get_event(
    event_id: UUID, store: EventStore = Depends(get_event_store)
) -> dict[str, Any]:
    event = await store.get(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event.model_dump(mode="json")


@router.post("/{event_id}/replay")
async def replay_event(
    event_id: UUID, store: EventStore = Depends(get_event_store)
) -> dict[str, Any]:
    event = await store.get(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    if event.status != EventStatus.DEAD:
        raise HTTPException(
            status_code=409, detail=f"event is {event.status.value}; only dead events can be replayed"
        )
    replayed = await store.replay(event_id)
    if replayed is None:  # lost a race with another replay
        raise HTTPException(status_code=409, detail="event is no longer dead")
    logger.info(
        "event replayed",
        extra={
            "event_id": str(event_id),
            "webhook_id": replayed.webhook_id,
            "status": replayed.status.value,
            "attempts": replayed.attempts,
        },
    )
    return replayed.model_dump(mode="json")
