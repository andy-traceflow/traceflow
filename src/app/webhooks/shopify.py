"""Shopify webhook receiver.

Path: POST /webhooks/shopify/{topic:path}

Target shape (Phase 3c): verify HMAC → insert one row into `events` →
return 200. No transformation, no destination call — the worker does
that from the events table.

Current state (Phase 2): verify → acknowledge. The HMAC dependency runs
before the handler body and fails closed. Unparseable JSON is acknowledged
with a 200 so Shopify stops retrying a body that will never parse. Durable
persistence lands in Phase 3c — until then nothing is stored.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from app.services.webhook_signature import verify_shopify_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/shopify", tags=["webhooks"])


@router.post("/{topic:path}")
async def shopify_webhook(
    topic: str,
    request: Request,
    body: bytes = Depends(verify_shopify_signature),
) -> Response:
    # `body` is the exact bytes the dependency verified (also cached on
    # request.state._cached_body). Never re-read the stream here.
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        # Acknowledge to Shopify so it doesn't retry an unparseable body.
        logger.warning("shopify webhook: invalid JSON body", extra={"topic": topic})
        return Response(status_code=200, content="ok")

    webhook_id = request.headers.get("X-Shopify-Webhook-Id", "")
    logger.info(
        "shopify webhook accepted (verified; persistence lands in Phase 3c)",
        extra={
            "topic": topic,
            "webhook_id": webhook_id,
            "order_id": str(payload.get("id")) if isinstance(payload, dict) else None,
        },
    )
    return Response(status_code=200, content="ok")


# ---------------------------------------------------------------------------
# Payload resolution helpers. Priority: shipping_address > billing_address >
# customer. This ordering is hard-won from the SEMCO integration — keep it.
# ---------------------------------------------------------------------------


def _resolve_contact_name(order: dict[str, Any]) -> str | None:
    for key in ("shipping_address", "billing_address"):
        addr = order.get(key) or {}
        first = (addr.get("first_name") or "").strip()
        last = (addr.get("last_name") or "").strip()
        if first or last:
            return f"{first} {last}".strip()
    customer = order.get("customer") or {}
    first = (customer.get("first_name") or "").strip()
    last = (customer.get("last_name") or "").strip()
    if first or last:
        return f"{first} {last}".strip()
    return None


def _resolve_company(order: dict[str, Any]) -> str | None:
    for key in ("shipping_address", "billing_address"):
        addr = order.get(key) or {}
        company = (addr.get("company") or "").strip()
        if company:
            return company
    return None


def _resolve_phone(order: dict[str, Any]) -> str | None:
    for key in ("shipping_address", "billing_address"):
        addr = order.get(key) or {}
        phone = (addr.get("phone") or "").strip()
        if phone:
            return phone
    customer = order.get("customer") or {}
    phone = (customer.get("phone") or "").strip()
    return phone or None


def _resolve_address(order: dict[str, Any]) -> str | None:
    addr = order.get("shipping_address") or order.get("billing_address") or {}
    parts = [
        addr.get("address1"),
        addr.get("address2"),
        addr.get("city"),
        addr.get("province"),
        addr.get("zip"),
    ]
    parts = [p for p in parts if p]
    return ", ".join(parts) if parts else None
