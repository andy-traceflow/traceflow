"""Routing & classification activity — the metrics-integrity view.

The LLR recovery rate is computed over potential_lead only; these endpoints
expose the denominator breakdown (what every missed call was classified as,
including calls absorbed by an active conversation) so the 25%+ recovery
guarantee stays auditable. Source of truth is the events stream written by
the missed-call webhook (see docs/workflow-schema.md, lead lifecycle §3).

ISOLATION INVARIANT: service connection bypasses RLS — every query filters
by the path's client_id.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.db import get_service_connection
from app.services.admin_auth import require_admin_user

from .schemas import RoutingActivityOut, RoutingLogItem

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin_user)])

# Every missed call lands exactly one of these on the event stream:
# - twilio_missed_call_received: a routing decision was made
#   (payload: call_sid, route, classification, reason)
# - missed_call_during_active_conversation: caller already mid-conversation
#   (payload: call_sid, from)
_ENTRY_EVENTS = ("twilio_missed_call_received", "missed_call_during_active_conversation")
# greeting_suppressed (payload: route, reason, classification) is not an
# entry event — it explains why a routed call got no SMS, so it appears in
# the log but never in the breakdown denominator.
_LOG_EVENTS = (*_ENTRY_EVENTS, "greeting_suppressed")

_ACTIVE_CONVERSATION = "active_conversation"


@router.get("/clients/{client_id}/routing-activity", response_model=RoutingActivityOut)
async def routing_activity(
    client_id: UUID,
    window_days: int = Query(default=30, ge=1, le=365),
) -> RoutingActivityOut:
    """Per-classification counts of every missed call in the window."""
    async with get_service_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT CASE WHEN e.event_type = 'missed_call_during_active_conversation'
                        THEN 'active_conversation'
                        ELSE COALESCE(e.payload->>'classification', 'unknown') END AS bucket,
                   count(*) AS n
            FROM events e
            WHERE e.client_id = $1
              AND e.event_type = ANY($2::text[])
              AND e.created_at >= now() - make_interval(days => $3)
            GROUP BY 1
            """,
            client_id,
            list(_ENTRY_EVENTS),
            window_days,
        )

    breakdown = {r["bucket"]: int(r["n"]) for r in rows}
    total = sum(breakdown.values())
    return RoutingActivityOut(
        window_days=window_days,
        total_calls=total,
        breakdown=breakdown,
        genuine_lead_rate=round(breakdown.get("potential_lead", 0) / total, 3) if total else 0.0,
        spam_rate=round(breakdown.get("spam", 0) / total, 3) if total else 0.0,
    )


@router.get("/clients/{client_id}/routing-log", response_model=list[RoutingLogItem])
async def routing_log(
    client_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[RoutingLogItem]:
    """Most-recent-first stream of routing decisions (and greeting
    suppressions) with the caller and the classifier's reason."""
    async with get_service_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT e.created_at, e.event_type, e.payload, e.lead_id, l.phone
            FROM events e
            LEFT JOIN leads l ON l.id = e.lead_id
            WHERE e.client_id = $1
              AND e.event_type = ANY($2::text[])
            ORDER BY e.created_at DESC
            LIMIT $3
            """,
            client_id,
            list(_LOG_EVENTS),
            limit,
        )
    return [_log_item(r) for r in rows]


def _log_item(row: Any) -> RoutingLogItem:
    payload: dict[str, Any] = row["payload"] or {}
    if row["event_type"] == "missed_call_during_active_conversation":
        decision: str | None = _ACTIVE_CONVERSATION
    else:
        decision = payload.get("route") or payload.get("classification")
    return RoutingLogItem(
        created_at=row["created_at"],
        event_type=row["event_type"],
        routing_decision=decision,
        caller=row["phone"] or payload.get("from"),
        reason=payload.get("reason"),
        lead_id=row["lead_id"],
    )
