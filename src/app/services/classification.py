"""Caller classification — pre-send routing for missed calls.

Decides what a missed-caller IS before any SMS or AI interaction is spent, and
now does it on top of the durable `contacts` identity (migrations 018/019) so
caller memory survives across leads. One tree, in order:

  1. No phone            → potential_lead (never drop a caller).
  2. Resolve the contact (get-or-create, normalized phone).
  3. blocked / spam      → silent drop, zero lookups/AI.
  3b. vendor allowlist   → known_non_lead (explicit, authoritative, live check).
  4. cached customer/vendor → route from the contact, skip CRM + spam lookups
                              (the repeat-known-caller cost win).
  5. open lead           → active_conversation (fresh) or resumed_conversation
                              (older than the resume window — reuse the lead).
  6. type resolution     → the Slice 2.5 resolver (CRM in crm-mode, local in
                              traceflow-mode); re-enter customer/vendor.
  7. spam scoring        → unknown callers ONLY, never a known prospect.
  8. returning prospect  → returning_contact (new lead, seeded with facts).
  9. default             → potential_lead; promote unknown → prospect.

Prime directive unchanged: a lookup failure must NEVER drop a real lead. Every
ambiguous, failing, or unknown path degrades toward `potential_lead`, and the
post-reply intent classifier is the safety net. CRM-known callers and known
prospects are never scored as spam.

Configuration over customization: a client with no CRM and an empty
vendor_allowlist still runs this exact tree — the resolver resolves to
'traceflow' mode and the local contacts row is the authority. There is no
separate no-CRM code path.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.models.client_config import ClientConfig
from app.models.contact import Contact, ContactType, ContactTypeSource
from app.models.lead import Classification
from app.services.contacts import (
    resolve_contact,
    resolve_contact_type,
    set_contact_type,
    type_cache_fresh,
)
from app.services.phone import normalize
from app.services.spam import SpamRisk, is_spam, score_spam_risk

logger = logging.getLogger(__name__)

# Caller-side ceiling on the spam lookup. The service self-caps too; this is the
# belt-and-suspenders guarantee a slow Lookup can't delay the missed-call SMS.
LOOKUP_TIMEOUT = 2.0


class Route(StrEnum):
    """The dispatch branch chosen for a missed caller.

    `active_conversation` short-circuits lead creation; `resumed_conversation`
    reuses an existing (stale) lead; the rest create a lead carrying the matching
    `classification` tag.
    """

    active_conversation = "active_conversation"
    resumed_conversation = "resumed_conversation"
    returning_contact = "returning_contact"
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
    # The durable contact (None only on the no-phone path). Carries the id used
    # to link the lead, plus known_facts to seed a returning caller.
    contact: Contact | None = None
    # Set for active_conversation / resumed_conversation — the open lead the
    # caller already has (None on resume when reuse_lead_on_resume is off).
    existing_lead_id: UUID | None = None
    # True when this caller is recognized from a prior conversation.
    is_returning: bool = False


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
    # 1. No caller number — can't classify or build a contact; treat as a lead.
    if not phone:
        return ClassificationResult(
            Route.potential_lead,
            Classification.potential_lead,
            should_text=True,
            reason="no_caller_number",
        )

    # 2. Resolve the durable contact (get-or-create, normalized phone).
    contact = await resolve_contact(conn, client_id, phone, config.default_phone_region)

    # 3. Hard drops. blocked is a human decision (permanent); spam is an
    # inference that only re-scores once rescore_spam_after_days has elapsed
    # (default None = never). Zero lookups, zero AI, no SMS.
    if contact.contact_type == ContactType.blocked:
        return _drop(contact, reason="blocked_contact")
    rescore_spam = False
    if contact.contact_type == ContactType.spam:
        if not _spam_rescore_due(contact, config):
            return _drop(contact, reason="cached_spam")
        rescore_spam = True

    # 3b. Vendor allowlist — explicit, phone-based, authoritative for vendors,
    # re-checked live every call. Normalize both sides to E.164 first.
    region = config.default_phone_region
    normalized_phone = normalize(phone, region) or phone
    allowlist = {normalize(v, region) or v for v in config.vendor_allowlist}
    if normalized_phone in allowlist:
        await _mark_type(conn, contact, ContactType.vendor, "vendor_allowlist")
        return ClassificationResult(
            Route.known_non_lead,
            Classification.known_non_lead,
            should_text=config.text_vendors,
            reason="vendor_allowlist",
            contact=contact,
        )

    # 4. Cached known type — a fresh crm-typed customer/vendor skips the CRM AND
    # spam lookups entirely. This is the cost win: a repeat known caller is free.
    if contact.contact_type in (ContactType.customer, ContactType.vendor) and type_cache_fresh(
        contact, config
    ):
        return _known_type_result(contact.contact_type, contact, config, "cached_contact_type")

    # 5. Open lead for this contact (mid-conversation).
    open_lead = await conn.fetchrow(
        """
        SELECT id,
               GREATEST(created_at,
                        COALESCE(last_inbound_at, created_at),
                        COALESCE(last_outbound_at, created_at)) AS last_activity
        FROM leads
        WHERE contact_id = $1 AND qualification_status IN ('unqualified', 'qualifying')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        contact.id,
    )
    if open_lead is not None:
        age = datetime.now(UTC) - open_lead["last_activity"]
        if age <= timedelta(hours=config.resume_window_hours):
            # Fresh — a genuine active conversation. Don't re-greet (today's
            # behavior, now time-bounded so a months-stale lead can't hold here).
            return ClassificationResult(
                Route.active_conversation,
                Classification.potential_lead,
                should_text=False,
                reason="active_conversation",
                contact=contact,
                existing_lead_id=open_lead["id"],
            )
        # Stale-open — resume. Reuse the same lead (no duplicate CRM record)
        # when configured; text them a recognition greeting.
        return ClassificationResult(
            Route.resumed_conversation,
            Classification.potential_lead,
            should_text=True,
            reason="resumed_conversation",
            contact=contact,
            existing_lead_id=open_lead["id"] if config.reuse_lead_on_resume else None,
            is_returning=True,
        )

    # 6. Type resolution — the config-driven resolver. In crm mode it looks the
    # number up (and persists the answer); in traceflow mode it reads the local
    # row. Either way, a failure falls through to the local type, never a drop.
    resolution = await resolve_contact_type(conn, config, contact, phone)
    current_type = resolution.contact_type
    if current_type == ContactType.customer:
        return _known_type_result(ContactType.customer, contact, config, "resolved_customer")
    if current_type == ContactType.vendor:
        return _known_type_result(ContactType.vendor, contact, config, "resolved_vendor")

    # 7. Spam scoring — unknown callers only (never a known prospect). Also runs
    # for a spam contact whose rescore window has elapsed.
    if (current_type == ContactType.unknown or rescore_spam) and config.spam_filtering_enabled:
        risk = await _safe_spam_score(phone)
        if risk is not None and is_spam(risk, config.spam_risk_threshold):
            await _mark_type(conn, contact, ContactType.spam, f"spam_risk:{risk.name}")
            return ClassificationResult(
                Route.spam,
                Classification.spam,
                should_text=not config.drop_spam_silently,
                reason=f"spam_risk:{risk.name}",
                contact=contact,
            )
        if rescore_spam and current_type == ContactType.spam:
            # Survived re-scoring — give the number a second life as a prospect.
            await _mark_type(conn, contact, ContactType.prospect, "spam_rescore_cleared")
            current_type = ContactType.prospect

    # 8. Returning prospect — a known contact whose leads are all terminal (step 5
    # already handled any open lead), last seen within the reopen window.
    if current_type == ContactType.prospect:
        last_lead_at = await conn.fetchval(
            "SELECT max(created_at) FROM leads WHERE contact_id = $1", contact.id
        )
        if last_lead_at is not None and (
            datetime.now(UTC) - last_lead_at
        ) <= timedelta(days=config.reopen_window_days):
            return ClassificationResult(
                Route.returning_contact,
                Classification.potential_lead,
                should_text=True,
                reason="returning_contact",
                contact=contact,
                is_returning=True,
            )

    # 9. Default — a genuine recoverable lead. First engagement promotes the
    # contact unknown → prospect so a second call is never spam-scored.
    if current_type == ContactType.unknown:
        await _mark_type(conn, contact, ContactType.prospect, "first_engagement")
    return ClassificationResult(
        Route.potential_lead,
        Classification.potential_lead,
        should_text=True,
        reason="default_potential_lead",
        contact=contact,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drop(contact: Contact, reason: str) -> ClassificationResult:
    """A hard silent drop (blocked / cached spam) — no text, no spend."""
    return ClassificationResult(
        Route.spam,
        Classification.spam,
        should_text=False,
        reason=reason,
        contact=contact,
    )


def _known_type_result(
    contact_type: ContactType,
    contact: Contact,
    config: ClientConfig,
    reason: str,
) -> ClassificationResult:
    """Route a resolved customer/vendor. Existing-customer and vendor messages
    are deliberately distinct (separate should_text toggles)."""
    if contact_type == ContactType.customer:
        return ClassificationResult(
            Route.existing_customer,
            Classification.existing_customer,
            should_text=config.text_existing_customers,
            reason=reason,
            contact=contact,
        )
    return ClassificationResult(
        Route.known_non_lead,
        Classification.known_non_lead,
        should_text=config.text_vendors,
        reason=reason,
        contact=contact,
    )


async def _mark_type(
    conn: Any,
    contact: Contact,
    new_type: ContactType,
    reason: str,
) -> None:
    """Opportunistically record an inferred type on the contact. Respects the
    precedence rule (won't override a crm/manual value) and never raises into
    the classifier — typing memory is best-effort, routing is not."""
    try:
        await set_contact_type(conn, contact.id, new_type, ContactTypeSource.inferred, reason)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("classify_caller: set_contact_type failed", exc_info=e)


def _spam_rescore_due(contact: Contact, config: ClientConfig) -> bool:
    """Whether a spam-typed contact is due for re-scoring on this call."""
    days = config.rescore_spam_after_days
    if days is None or contact.contact_type_at is None:
        return False
    return (datetime.now(UTC) - contact.contact_type_at) >= timedelta(days=days)


async def _safe_spam_score(phone: str) -> SpamRisk | None:
    """Run the Twilio spam lookup under a hard timeout. Never raises.

    score_spam_risk already self-caps and returns None on its own failures;
    this wrapper guarantees a slow Lookup can never delay the missed-call SMS.
    """
    try:
        return await asyncio.wait_for(score_spam_risk(phone), timeout=LOOKUP_TIMEOUT)
    except Exception as e:
        logger.warning("classification: spam lookup failed/timed out", exc_info=e)
        return None
