"""Tenant resolver middleware.

Runs before every request. Extracts the client_id from the URL path
(preferred) or the payload (fallback for webhooks that don't support
path params), verifies the tenant exists and is active, optionally
verifies the webhook signature, then sets the ContextVar that
db.get_connection() reads to scope queries via RLS.

Routes that don't carry a client_id (e.g. /health, /, /docs) pass
through without modifying tenant context.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.db import set_current_tenant
from app.services.webhook_signature import verify_signature_for_request

logger = logging.getLogger(__name__)

# Webhook URLs that embed the client_id in the path
_PATH_PATTERNS = [
    re.compile(r"^/webhooks/twilio/[^/]+/(?P<client_id>[0-9a-f-]{36})/?"),
    re.compile(r"^/webhooks/shopify/(?P<client_id>[0-9a-f-]{36})/?"),
    re.compile(r"^/webhooks/crm/[^/]+/(?P<client_id>[0-9a-f-]{36})/?"),
    re.compile(r"^/webhooks/generic/(?P<client_id>[0-9a-f-]{36})/[^/]+/?"),
]


async def tenant_resolver_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    client_id = _extract_client_id_from_path(request.url.path)

    if client_id is None:
        # No tenant context needed for /health, /docs, root, admin routes
        # that handle their own auth, etc.
        return await call_next(request)

    # Verify webhook signature using the per-client secret loaded from
    # client_configs.webhook_signing_secrets. Raises 401 on mismatch.
    if request.url.path.startswith("/webhooks/"):
        try:
            await verify_signature_for_request(request, client_id)
        except PermissionError as e:
            logger.warning(
                "webhook signature verification failed",
                extra={"client_id": str(client_id), "path": request.url.path, "error": str(e)},
            )
            return JSONResponse(
                status_code=401,
                content={"error": "signature_verification_failed"},
            )

    set_current_tenant(client_id)
    try:
        response = await call_next(request)
    finally:
        set_current_tenant(None)

    return response


def _extract_client_id_from_path(path: str) -> UUID | None:
    for pattern in _PATH_PATTERNS:
        m = pattern.match(path)
        if m:
            try:
                return UUID(m.group("client_id"))
            except ValueError:
                return None
    return None
