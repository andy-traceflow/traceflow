"""CRM webhook receiver (stub).

Path: POST /webhooks/crm/{provider}/{client_id}

Used when a CRM pushes status updates back to TraceFlow — e.g. a lead
was contacted, a deal was won, an opportunity changed stage. The
provider segment routes to the matching adapter's parse_webhook().

Phase 0 stub: log and 200. Real implementation comes online once the
first client has bidirectional CRM sync (Phase 2+).
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/crm", tags=["webhooks"])


@router.post("/{provider}/{client_id}")
async def crm_webhook(provider: str, client_id: UUID, request: Request) -> Response:
    body: bytes = getattr(request.state, "_cached_body", b"") or await request.body()
    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        return Response(status_code=200, content="ok")

    logger.info(
        "crm webhook accepted (stub)",
        extra={"provider": provider, "client_id": str(client_id), "payload_keys": list(payload.keys())[:10]},
    )
    # TODO: dispatch via app.adapters.registry.get_adapter(provider).parse_webhook(...)
    return Response(status_code=200, content="ok")
