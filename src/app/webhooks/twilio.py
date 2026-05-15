"""Twilio webhook receiver (stub for Phase 0 LLR build).

Path: POST /webhooks/twilio/{event_type}/{client_id}
Auth: X-Twilio-Signature (HMAC-SHA1 over URL + sorted params).
      Verification lives in services/twilio_signature.py (to be added).

The missed-call → SMS flow lands here. Phase 0 will:
  1. Accept the missed-call webhook
  2. Create a Lead with source_system='twilio_missed_call'
  3. Schedule a background task to generate + send the AI greeting
  4. Return 200 immediately

For now the stub accepts the event and returns 200 so the rest of the
pipeline can be developed in parallel.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/twilio", tags=["webhooks"])


@router.post("/missed-call/{client_id}")
async def missed_call_webhook(client_id: UUID, request: Request) -> Response:
    # Form-encoded; cache raw body for downstream processing later.
    form = await request.form()
    payload = dict(form)

    logger.info(
        "twilio missed call (stub)",
        extra={
            "client_id": str(client_id),
            "from": payload.get("From"),
            "to": payload.get("To"),
        },
    )

    # TODO Phase 0 actual build: create Lead + schedule AI greeting send.
    # For now: persist the raw event so we have data to test against.
    _ = json.dumps(payload, default=str)
    return Response(status_code=200, content="ok")


@router.post("/sms-reply/{client_id}")
async def sms_reply_webhook(client_id: UUID, request: Request) -> Response:
    """Inbound SMS from a lead in an active qualification conversation."""
    form = await request.form()
    payload = dict(form)
    logger.info(
        "twilio sms reply (stub)",
        extra={"client_id": str(client_id), "from": payload.get("From")},
    )
    # TODO Phase 0 actual build: route to qualifier turn handler.
    return Response(status_code=200, content="ok")
