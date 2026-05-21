"""Shopify webhook receiver.

Path: POST /webhooks/shopify/{client_id}
Auth: HMAC-SHA256 base64 (verified by tenant_resolver middleware via
      the per-client secret in client_configs.webhook_signing_secrets).

Pattern matches the original source-repo Shopify webhook handler with
two changes:
  - tenant identification via URL path (not store-key lookup)
  - dedupe + downstream processing run in a BackgroundTask so the 200
    is returned to Shopify within the 5-second SLA
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.db import set_tenant_context
from app.models.lead import LeadCreate
from app.services.dedupe import is_duplicate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/shopify", tags=["webhooks"])


@router.post("/{client_id}")
async def shopify_webhook(
    client_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    # The body was already read + verified by the tenant_resolver middleware
    # and cached on request.state._cached_body — re-reading here is free.
    body: bytes = getattr(request.state, "_cached_body", b"") or await request.body()

    try:
        order = json.loads(body)
    except json.JSONDecodeError:
        # Acknowledge to Shopify so it doesn't retry an unparseable body.
        logger.warning("shopify webhook: invalid JSON body", extra={"client_id": str(client_id)})
        return Response(status_code=200, content="ok")

    order_id = order.get("id")
    if is_duplicate(client_id, source="shopify", external_id=order_id):
        return Response(status_code=200, content="ok")

    topic = request.headers.get("X-Shopify-Topic", "unknown")
    logger.info(
        "shopify webhook accepted",
        extra={
            "client_id": str(client_id),
            "order_id": str(order_id),
            "topic": topic,
        },
    )

    background_tasks.add_task(_process_order, client_id, order)
    return Response(status_code=200, content="ok")


async def _process_order(client_id: UUID, order: dict[str, Any]) -> None:
    """Convert a Shopify order payload into a canonical Lead and persist it.

    Per architecture, raw_payload is always preserved on the lead. CRM
    push and AI qualification happen downstream — this function's job is
    intake, not orchestration.
    """
    lead_create = _shopify_order_to_lead(client_id, order)

    async with set_tenant_context(client_id) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO leads (
                client_id, external_id, source_system,
                contact_name, contact_company, phone, email, address,
                raw_payload
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
            """,
            lead_create.client_id,
            lead_create.external_id,
            lead_create.source_system,
            lead_create.contact_name,
            lead_create.contact_company,
            lead_create.phone,
            lead_create.email,
            lead_create.address,
            order,
        )
        lead_id = row["id"] if row else None

        await conn.execute(
            """
            INSERT INTO events (client_id, lead_id, event_type, payload)
            VALUES ($1, $2, 'shopify_order_received', $3)
            """,
            client_id,
            lead_id,
            {"order_id": order.get("id"), "name": order.get("name")},
        )

    logger.info(
        "shopify lead persisted",
        extra={"client_id": str(client_id), "lead_id": str(lead_id)},
    )


def _shopify_order_to_lead(client_id: UUID, order: dict[str, Any]) -> LeadCreate:
    """Translate a Shopify order webhook payload into a canonical LeadCreate.

    Resolution priority for contact and company mirrors the source repo's
    behavior: shipping_address > billing_address > customer.
    """
    contact_name = _resolve_contact_name(order)
    contact_company = _resolve_company(order)
    phone = _resolve_phone(order)
    email = order.get("email") or (order.get("customer") or {}).get("email")
    address = _resolve_address(order)

    return LeadCreate(
        client_id=client_id,
        source_system="shopify",
        external_id=str(order.get("id")) if order.get("id") is not None else None,
        contact_name=contact_name,
        contact_company=contact_company,
        phone=phone,
        email=email,
        address=address,
        raw_payload=order,
    )


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
