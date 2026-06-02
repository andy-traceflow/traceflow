"""Caller classification — pre-send routing for missed calls.

Decides what a missed-caller IS before any SMS or AI interaction is spent:
an active conversation we're already mid-qualifying, an existing customer,
a known non-lead (vendor/partner), spam, or a genuine potential lead.
See docs/workflow-schema.md Section 3 (Technical Lead Lifecycle).

Prime directive: a lookup failure must NEVER drop a real lead. Every
ambiguous, failing, or unknown path degrades toward `potential_lead`, and
the post-reply intent classifier is the safety net. CRM-known callers are
never scored as spam.

Behavior is config-driven, not branched in code: a client with no CRM and
an empty vendor_allowlist always lands on `potential_lead` — identical to
the pre-classification behavior — so this is safe to ship by default.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.adapters.registry import get_adapter
from app.models.client_config import ClientConfig
from app.models.crm_contact import ContactType, CRMContact
from app.models.lead import Classification
from app.services.spam import SpamRisk, is_spam, score_spam_risk

logger = logging.getLogger(__name__)

# Caller-side ceiling on the CRM lookup. The adapter enforces its own short
# timeout too; this is the belt-and-suspenders guarantee that a slow CRM can
# never delay the missed-call SMS past its target.
LOOKUP_TIMEOUT = 2.0


class Route(StrEnum):
    """The dispatch branch chosen for a missed caller.

    `active_conversation` short-circuits lead creation entirely; the others
    each create a lead row carrying the matching `classification` tag.
    """

    active_conversation = "active_conversation"
    existing_customer = "existing_customer"
    known_non_lead = "known_non_lead"
    spam = "spam"
    potential_lead = "potential_lead"


@dataclass(frozen=True)
class ClassificationResult:
    route: Route
    classification: Classification
    should_text: bool
    reason: str
    contact: CRMContact | None = None
    # Set only for the active_conversation route — the open lead the caller
    # is already being qualified on.
    existing_lead_id: UUID | None = None


async def classify_caller(
    conn: Any,
    client_id: UUID,
    phone: str | None,
    config: ClientConfig,
) -> ClassificationResult:
    """Classify a missed caller and decide how to route them.

    `conn` must already be inside the tenant RLS context for `client_id`.
    Never raises — every failure path degrades toward `potential_lead`.
    """
    # No caller number — can't classify; treat as a lead so we never drop one.
    if not phone:
        return ClassificationResult(
            Route.potential_lead,
            Classification.potential_lead,
            should_text=True,
            reason="no_caller_number",
        )

    # Active conversation: an open lead already exists for this number. A
    # second missed call shouldn't spawn a duplicate lead or re-greet them.
    active_lead_id = await conn.fetchval(
        """
        SELECT id FROM leads
        WHERE client_id = $1 AND phone = $2
          AND qualification_status IN ('unqualified', 'qualifying')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        client_id,
        phone,
    )
    if active_lead_id is not None:
        return ClassificationResult(
            Route.active_conversation,
            Classification.potential_lead,
            should_text=False,
            reason="active_conversation",
            existing_lead_id=active_lead_id,
        )

    # Vendor allowlist — explicit, phone-based, authoritative for vendors.
    if phone in config.vendor_allowlist:
        return ClassificationResult(
            Route.known_non_lead,
            Classification.known_non_lead,
            should_text=config.text_vendors,
            reason="vendor_allowlist",
        )

    # CRM lookup — does the client already know this number?
    contact: CRMContact | None = None
    if config.crm_lookup_enabled and config.crm_provider:
        contact = await _safe_lookup(config, phone)

    if contact is not None:
        if contact.contact_type == ContactType.customer:
            return ClassificationResult(
                Route.existing_customer,
                Classification.existing_customer,
                should_text=config.text_existing_customers,
                reason="crm_customer",
                contact=contact,
            )
        if contact.contact_type == ContactType.vendor:
            return ClassificationResult(
                Route.known_non_lead,
                Classification.known_non_lead,
                should_text=config.text_vendors,
                reason="crm_vendor",
                contact=contact,
            )
        # Known to the CRM as a lead/unknown contact — a re-engagement, still
        # a potential lead. CRM-known callers are never treated as spam.
        return ClassificationResult(
            Route.potential_lead,
            Classification.potential_lead,
            should_text=True,
            reason="crm_known_lead",
            contact=contact,
        )

    # Unknown caller. Spam scoring runs ONLY here — CRM-known callers are
    # never scored as spam. Any failure degrades toward potential_lead.
    if config.spam_filtering_enabled:
        risk = await _safe_spam_score(phone)
        if risk is not None and is_spam(risk, config.spam_risk_threshold):
            # drop_spam_silently=False keeps the recovery text flowing (the
            # lead is merely tagged spam for metrics), protecting against a
            # false positive; the default True sends nothing, zero spend.
            return ClassificationResult(
                Route.spam,
                Classification.spam,
                should_text=not config.drop_spam_silently,
                reason=f"spam_risk:{risk.name}",
            )

    # Default: a genuine recoverable lead.
    return ClassificationResult(
        Route.potential_lead,
        Classification.potential_lead,
        should_text=True,
        reason="default_potential_lead",
    )


async def _safe_lookup(config: ClientConfig, phone: str) -> CRMContact | None:
    """Run the CRM phone lookup under a hard timeout. Never raises.

    The adapter already returns None on its own failures and caps itself;
    this wrapper is the caller-side guarantee the missed-call SMS is never
    delayed by a slow or misbehaving CRM.
    """
    try:
        adapter = get_adapter(config.crm_provider)  # type: ignore[arg-type]
    except ValueError:
        logger.warning(
            "classification: unknown crm_provider",
            extra={"provider": config.crm_provider, "client_id": str(config.client_id)},
        )
        return None
    try:
        return await asyncio.wait_for(
            adapter.lookup_by_phone(phone, config), timeout=LOOKUP_TIMEOUT
        )
    except Exception as e:
        logger.warning("classification: crm lookup failed/timed out", exc_info=e)
        return None


async def _safe_spam_score(phone: str) -> SpamRisk | None:
    """Run the Twilio spam lookup under a hard timeout. Never raises.

    score_spam_risk already self-caps and returns None on its own failures;
    this wrapper is the caller-side guarantee that a slow Lookup can never
    delay the missed-call SMS past its target.
    """
    try:
        return await asyncio.wait_for(score_spam_risk(phone), timeout=LOOKUP_TIMEOUT)
    except Exception as e:
        logger.warning("classification: spam lookup failed/timed out", exc_info=e)
        return None
