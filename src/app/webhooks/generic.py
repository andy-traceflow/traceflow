"""Generic webhook receiver — Layer 3 of the integration model.

Path: POST /webhooks/generic/{client_id}/{slug}

For long-tail systems where building a full adapter isn't justified:
homegrown CRMs, niche field-service tools, anything that fires JSON at
a URL. The per-client config in client_webhook_configs stores parsing
rules (JSONPath, jq, or Python templates) that extract canonical
Lead fields from any payload shape.

Signature verification reads the per-config signing_secret +
signing_algorithm columns and validates the request against the
matching pattern.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from jsonpath_ng import parse as jsonpath_parse

from app.db import set_tenant_context
from app.models.lead import LeadCreate
from app.services.webhook_signature import (
    verify_hmac_sha256_base64,
    verify_hmac_sha256_hex,
    verify_timestamped_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/generic", tags=["webhooks"])


@router.post("/{client_id}/{slug}")
async def generic_webhook(
    client_id: UUID,
    slug: str,
    request: Request,
) -> Response:
    body: bytes = getattr(request.state, "_cached_body", b"") or await request.body()

    async with set_tenant_context(client_id) as conn:
        config_row = await conn.fetchrow(
            """
            SELECT parser_type, field_extractors, signing_secret,
                   signing_algorithm, signature_header
            FROM client_webhook_configs
            WHERE client_id = $1 AND webhook_slug = $2
            """,
            client_id,
            slug,
        )

    if config_row is None:
        raise HTTPException(status_code=404, detail=f"no webhook config for slug '{slug}'")

    _verify_generic_signature(request, body, config_row)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        # Acknowledge so the provider doesn't retry forever.
        logger.warning("generic webhook: invalid JSON", extra={"client_id": str(client_id), "slug": slug})
        return Response(status_code=200, content="ok")

    lead_data = _extract_fields(
        payload=payload,
        parser_type=config_row["parser_type"],
        extractors=config_row["field_extractors"] or {},
    )

    lead = LeadCreate(
        client_id=client_id,
        source_system=f"generic:{slug}",
        raw_payload=payload,
        **lead_data,
    )

    async with set_tenant_context(client_id) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO leads (
                client_id, external_id, source_system,
                contact_name, contact_company, phone, email, address,
                service_type, sqft, budget_range, timeframe,
                raw_payload
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            RETURNING id
            """,
            client_id,
            lead.external_id,
            lead.source_system,
            lead.contact_name,
            lead.contact_company,
            lead.phone,
            lead.email,
            lead.address,
            lead.service_type,
            lead.sqft,
            lead.budget_range,
            lead.timeframe,
            payload,
        )
        lead_id = row["id"] if row else None

        await conn.execute(
            """
            INSERT INTO events (client_id, lead_id, event_type, payload)
            VALUES ($1, $2, 'generic_webhook_received', $3)
            """,
            client_id,
            lead_id,
            {"slug": slug},
        )

    logger.info(
        "generic lead persisted",
        extra={"client_id": str(client_id), "slug": slug, "lead_id": str(lead_id)},
    )
    return Response(status_code=200, content="ok")


def _verify_generic_signature(request: Request, body: bytes, config_row: Any) -> None:
    algorithm = config_row["signing_algorithm"]
    secret = config_row["signing_secret"]
    header_name = config_row["signature_header"] or "X-Signature"

    if algorithm == "none":
        return

    signature = request.headers.get(header_name, "")
    if not secret:
        raise HTTPException(status_code=500, detail="signing_secret not configured for this webhook slug")

    if algorithm == "hmac_sha256":
        # Accept either hex or base64 — try both before failing.
        if verify_hmac_sha256_hex(secret, body, signature) or verify_hmac_sha256_base64(secret, body, signature):
            return
        raise HTTPException(status_code=401, detail="signature mismatch")

    if algorithm == "hmac_sha256_timestamped":
        if verify_timestamped_signature(secret, body, signature):
            return
        raise HTTPException(status_code=401, detail="signature mismatch or replay")

    raise HTTPException(status_code=500, detail=f"unknown signing_algorithm: {algorithm}")


def _extract_fields(
    *,
    payload: dict[str, Any],
    parser_type: str,
    extractors: dict[str, str],
) -> dict[str, Any]:
    """Run the configured parser against payload to extract canonical fields."""
    result: dict[str, Any] = {}

    if parser_type == "jsonpath":
        for canonical_field, expression in extractors.items():
            try:
                expr = jsonpath_parse(expression)
                matches = [m.value for m in expr.find(payload)]
                if matches:
                    result[canonical_field] = matches[0]
            except Exception as e:
                logger.warning(
                    "jsonpath extraction failed",
                    extra={"field": canonical_field, "expr": expression, "error": str(e)},
                )

    elif parser_type == "jq":
        # jq support requires the `jq` Python package; left out of base
        # deps until a client actually needs it.
        logger.warning("jq parser_type configured but not yet wired")

    elif parser_type == "python_template":
        # Sandboxed eval is non-trivial; defer until needed. Until then
        # we silently produce no extractions for this parser type.
        logger.warning("python_template parser_type configured but not yet wired")

    else:
        logger.error("unknown parser_type", extra={"parser_type": parser_type})

    return result
