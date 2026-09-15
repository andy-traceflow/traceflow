"""Shopify webhook receiver.

Path: POST /webhooks/shopify/{topic:path}

The handler does exactly three things: verify → persist → 200.
  1. `verify_shopify_signature` (a dependency) checks the HMAC and fails closed.
  2. One row is inserted into `events`. `ON CONFLICT (source, webhook_id)
     DO NOTHING` absorbs Shopify redeliveries.
  3. 200. Shopify's 5-second budget is never at risk because nothing
     downstream runs here — the worker reads the row later.

If the insert fails, the handler returns 503 rather than 200: durable
first. Shopify retries non-2xx for ~48 hours, so the event is not lost.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.services.events import EventStore, get_event_store
from app.services.webhook_signature import verify_shopify_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/shopify", tags=["webhooks"])

SOURCE = "shopify"


@router.post("/{topic:path}")
async def shopify_webhook(
    topic: str,
    request: Request,
    body: bytes = Depends(verify_shopify_signature),
    store: EventStore = Depends(get_event_store),
) -> Response:
    # `body` is the exact bytes the dependency verified. Never re-read the stream.
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        # Acknowledge so Shopify stops retrying a body that will never parse.
        logger.warning("shopify webhook: invalid JSON body", extra={"topic": topic})
        return Response(status_code=200, content="ok")

    webhook_id = request.headers.get("X-Shopify-Webhook-Id", "").strip()
    if not webhook_id:
        # Shopify always sends one. A request without it still passed HMAC,
        # so it came from someone holding the secret (e.g. a manual replay).
        # Derive a deterministic id from the body so it is deduplicated
        # rather than dropped or double-stored.
        webhook_id = "sha256:" + hashlib.sha256(body).hexdigest()
        logger.warning(
            "shopify webhook: missing X-Shopify-Webhook-Id, using body hash",
            extra={"topic": topic, "webhook_id": webhook_id},
        )

    # Shopify's header is authoritative; the path segment is the fallback.
    event_topic = request.headers.get("X-Shopify-Topic", "").strip() or topic
    shop_domain = request.headers.get("X-Shopify-Shop-Domain", "").strip() or None

    try:
        event_id = await store.insert(
            source=SOURCE,
            topic=event_topic,
            webhook_id=webhook_id,
            shop_domain=shop_domain,
            payload=payload,
        )
    except Exception:
        logger.exception(
            "shopify webhook: event store insert failed — NOT acknowledging",
            extra={"topic": event_topic, "webhook_id": webhook_id},
        )
        raise HTTPException(status_code=503, detail="event store unavailable") from None

    if event_id is None:
        logger.info(
            "shopify webhook: duplicate absorbed",
            extra={"topic": event_topic, "webhook_id": webhook_id},
        )
        return Response(status_code=200, content="ok")

    logger.info(
        "event received",
        extra={
            "event_id": str(event_id),
            "webhook_id": webhook_id,
            "topic": event_topic,
            "shop_domain": shop_domain,
            "status": "received",
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
