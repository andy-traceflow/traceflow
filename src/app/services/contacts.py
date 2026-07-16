"""Contact identity service — get-or-create, typing, history, person facts.

This module owns the `contacts` table. In particular it is the ONE place the
contact-type precedence rule lives:

    manual  >  crm  >  inferred

`set_contact_type` is the ONLY writer of `contact_type`. An `inferred` write can
never clobber a `crm` or `manual` value; a `crm` write can never clobber
`manual`; and `blocked` is settable only with `source='manual'` and is never
overwritten by any classifier, scorer, or CRM sync. Every applied type CHANGE
drops a `contact_type_changed` event.

All functions assume `conn` is already inside the tenant RLS context for the
contact's client (the webhook/admin caller establishes it via set_tenant_context).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.adapters.registry import get_adapter
from app.models.client_config import ClientConfig
from app.models.contact import (
    PERSON_FACT_KEYS,
    Contact,
    ContactHistory,
    ContactLeadRef,
    ContactType,
    ContactTypeSource,
    from_crm_contact_type,
)
from app.models.crm_contact import CRMContact
from app.models.qualification import FieldScope, QualificationSchema
from app.services.phone import normalize

logger = logging.getLogger(__name__)

# Caller-side ceiling on the CRM lookup — the adapter caps itself too; this is
# the belt-and-suspenders guarantee a slow CRM can't delay the missed-call SMS.
LOOKUP_TIMEOUT = 2.0

# Precedence ranks. A write is permitted only when its source ranks at least as
# high as the source currently on the row.
_SOURCE_RANK: dict[ContactTypeSource, int] = {
    ContactTypeSource.inferred: 0,
    ContactTypeSource.crm: 1,
    ContactTypeSource.manual: 2,
}


async def resolve_contact(
    conn: Any,
    client_id: UUID,
    phone: str,
    default_region: str = "US",
) -> Contact:
    """Get-or-create the contact for `phone`, bumping `last_seen_at`.

    Idempotent under concurrent webhooks: the INSERT ... ON CONFLICT DO UPDATE
    can't race two rows into existence for the same (client_id, phone). The
    phone is normalized to E.164 first so `+1 (702) 517-8074` and `7025178074`
    resolve to the same contact. If the number can't be normalized we fall back
    to the trimmed raw value rather than drop the caller — a slightly-imperfect
    key beats a lost contact.
    """
    normalized = normalize(phone, default_region)
    if normalized is None:
        normalized = (phone or "").strip()
    if not normalized:
        raise ValueError("resolve_contact requires a non-empty phone")

    row = await conn.fetchrow(
        """
        INSERT INTO contacts (client_id, phone)
        VALUES ($1, $2)
        ON CONFLICT (client_id, phone)
        DO UPDATE SET last_seen_at = now()
        RETURNING *
        """,
        client_id,
        normalized,
    )
    return Contact(**dict(row))


@dataclass(frozen=True)
class _TypeDecision:
    write: bool
    is_change: bool


def _decide_type_write(
    current_type: ContactType,
    current_source: ContactTypeSource,
    new_source: ContactTypeSource,
    new_type: ContactType,
) -> _TypeDecision:
    """Pure precedence decision. Kept separate so the matrix is unit-testable
    without a database.

    A write is refused when its source ranks below the current source, or when
    it would overwrite a `blocked` contact with anything other than a manual
    decision. When permitted, `is_change` reflects whether the type actually
    moved (an equal-source re-confirmation still refreshes contact_type_at for
    the cache TTL but emits no change event)."""
    if _SOURCE_RANK[new_source] < _SOURCE_RANK[current_source]:
        return _TypeDecision(write=False, is_change=False)
    if current_type == ContactType.blocked and new_source != ContactTypeSource.manual:
        return _TypeDecision(write=False, is_change=False)
    return _TypeDecision(write=True, is_change=new_type != current_type)


async def set_contact_type(
    conn: Any,
    contact_id: UUID,
    new_type: ContactType | str,
    source: ContactTypeSource | str,
    reason: str | None = None,
) -> bool:
    """Set a contact's type, honoring the precedence rule. Returns whether the
    write was applied.

    The ONLY writer of contacts.contact_type. Raises ValueError only for the
    hard invariant violation of setting `blocked` from a non-manual source —
    everything else that's disallowed by precedence is a silent, logged no-op
    (returns False), because the caller (a classifier or CRM sync) legitimately
    tries and should simply be denied, not error out.
    """
    new_type = ContactType(new_type)
    source = ContactTypeSource(source)

    if new_type == ContactType.blocked and source != ContactTypeSource.manual:
        raise ValueError("contact_type 'blocked' is settable only with source='manual'")

    row = await conn.fetchrow(
        "SELECT client_id, contact_type, contact_type_source FROM contacts WHERE id = $1",
        contact_id,
    )
    if row is None:
        logger.warning("set_contact_type: contact not found", extra={"contact_id": str(contact_id)})
        return False

    current_type = ContactType(row["contact_type"])
    current_source = ContactTypeSource(row["contact_type_source"])

    decision = _decide_type_write(current_type, current_source, source, new_type)
    if not decision.write:
        logger.info(
            "set_contact_type refused by precedence",
            extra={
                "contact_id": str(contact_id),
                "current": f"{current_type}:{current_source}",
                "attempted": f"{new_type}:{source}",
            },
        )
        return False

    await conn.execute(
        """
        UPDATE contacts
           SET contact_type = $2,
               contact_type_source = $3,
               contact_type_at = now(),
               contact_type_reason = $4
         WHERE id = $1
        """,
        contact_id,
        new_type.value,
        source.value,
        reason,
    )

    if decision.is_change:
        await conn.execute(
            """
            INSERT INTO events (client_id, lead_id, event_type, payload)
            VALUES ($1, NULL, 'contact_type_changed', $2)
            """,
            row["client_id"],
            {
                "contact_id": str(contact_id),
                "old_type": current_type.value,
                "new_type": new_type.value,
                "source": source.value,
                "reason": reason,
            },
        )
    return True


async def contact_history(
    conn: Any,
    contact_id: UUID,
    lead_limit: int = 3,
) -> ContactHistory:
    """The contact plus its most recent leads (newest first)."""
    crow = await conn.fetchrow("SELECT * FROM contacts WHERE id = $1", contact_id)
    if crow is None:
        raise ValueError(f"contact {contact_id} not found")
    contact = Contact(**dict(crow))

    lead_rows = await conn.fetch(
        """
        SELECT id, qualification_status, created_at, service_type, sqft, budget_range, timeframe
          FROM leads
         WHERE contact_id = $1
         ORDER BY created_at DESC
         LIMIT $2
        """,
        contact_id,
        lead_limit,
    )
    leads = [ContactLeadRef(**dict(r)) for r in lead_rows]
    return ContactHistory(contact=contact, leads=leads)


async def _apply_facts(
    conn: Any,
    contact_id: UUID,
    filtered: dict[str, Any],
) -> dict[str, Any]:
    """Right-biased merge of a pre-filtered fact dict into known_facts."""
    if not filtered:
        row = await conn.fetchrow("SELECT known_facts FROM contacts WHERE id = $1", contact_id)
        return dict(row["known_facts"]) if row else {}
    row = await conn.fetchrow(
        """
        UPDATE contacts
           SET known_facts = known_facts || $2::jsonb
         WHERE id = $1
        RETURNING known_facts
        """,
        contact_id,
        filtered,
    )
    return dict(row["known_facts"]) if row else {}


async def merge_known_facts(
    conn: Any,
    contact_id: UUID,
    facts: dict[str, Any],
) -> dict[str, Any]:
    """Merge facts into contacts.known_facts, restricted to the fixed
    PERSON_FACT_KEYS contract. The low-level writer used when there is no schema
    in hand (e.g. seeding). Empty values are ignored."""
    filtered = {
        k: v for k, v in facts.items() if k in PERSON_FACT_KEYS and v not in (None, "")
    }
    return await _apply_facts(conn, contact_id, filtered)


async def merge_person_facts(
    conn: Any,
    contact_id: UUID,
    extracted: dict[str, Any],
    schema: QualificationSchema,
) -> dict[str, Any]:
    """Merge the PERSON-scoped fields from a qualifier extraction into
    known_facts. The schema is the authority on what's person-scoped (broader
    than PERSON_FACT_KEYS — e.g. a client-defined property_type). Empty values
    are ignored."""
    person_keys = {f.key for f in schema.fields if f.scope == FieldScope.person}
    filtered = {k: v for k, v in extracted.items() if k in person_keys and v not in (None, "")}
    return await _apply_facts(conn, contact_id, filtered)


# ===========================================================================
# Contact source-of-truth resolver (Slice 2.5)
#
# There is ONE lifecycle. Where the authoritative answer to "what is this
# caller" lives is config, resolved at runtime — exactly like revenue_config.mode
# (ADR-0003). The contacts table is ALWAYS populated: the cache in 'crm' mode,
# the ledger in 'traceflow' mode. No code outside this section branches on which
# mode is active.
# ===========================================================================


@dataclass(frozen=True)
class TypeResolution:
    """The outcome of resolving a contact's type for this call."""

    contact_type: ContactType
    source: ContactTypeSource
    mode: str  # 'crm' | 'traceflow'
    from_cache: bool
    crm_external_id: str | None = None


def type_cache_fresh(contact: Contact, config: ClientConfig) -> bool:
    """Is the contact's crm-sourced type still inside the cache TTL?

    A fresh crm-typed contact lets the caller skip the CRM + spam lookups
    entirely — the repeat-known-caller cost win. Only meaningful for
    crm-sourced types; an inferred/manual type is authoritative regardless.
    """
    if contact.contact_type_at is None:
        return False
    age = datetime.now(UTC) - contact.contact_type_at
    return age < timedelta(days=config.contact_type_cache_days)


def _crm_available(config: ClientConfig) -> bool:
    """True when the client has a CRM provider with a registered adapter and
    credentials. A provider with no adapter (e.g. ServiceTitan) is False — it
    lands on 'traceflow' with no special casing."""
    if not config.crm_provider or not config.crm_credentials:
        return False
    try:
        get_adapter(config.crm_provider)
    except ValueError:
        return False
    return True


def resolve_mode(config: ClientConfig) -> str:
    """Resolve contact_config.source_of_truth to a concrete 'crm'|'traceflow'.

    'auto' and explicit 'crm' both require a working CRM to actually use it;
    otherwise they degrade to 'traceflow' (the local contacts row is authority).
    This is the ONLY place the mode is decided.
    """
    sot = config.contact_source_of_truth
    if sot in ("auto", "crm") and _crm_available(config):
        return "crm"
    return "traceflow"


async def _safe_crm_lookup(config: ClientConfig, phone: str) -> CRMContact | None:
    """Run the CRM phone lookup under a hard timeout. Never raises — a lookup
    failure must never drop a lead."""
    try:
        adapter = get_adapter(config.crm_provider)  # type: ignore[arg-type]
    except ValueError:
        return None
    try:
        return await asyncio.wait_for(
            adapter.lookup_by_phone(phone, config), timeout=LOOKUP_TIMEOUT
        )
    except Exception as e:
        logger.warning("contact resolver: crm lookup failed/timed out", exc_info=e)
        return None


async def resolve_contact_type(
    conn: Any,
    config: ClientConfig,
    contact: Contact,
    phone: str,
) -> TypeResolution:
    """Resolve what this caller IS, per the client's source-of-truth mode.

    crm mode: cache-first (a fresh crm-sourced type needs no network call);
    otherwise call the adapter, map its answer into our vocabulary, and persist
    it via set_contact_type(source='crm'). A lookup miss/failure falls through to
    the local contact row — never a drop.

    traceflow mode: the local contacts row IS the authority; no network call.
    `unknown` is a legitimate answer.
    """
    mode = resolve_mode(config)
    if mode == "traceflow":
        return TypeResolution(
            contact.contact_type, contact.contact_type_source, "traceflow",
            from_cache=True, crm_external_id=contact.crm_external_id,
        )

    # crm mode — cache-first.
    if contact.contact_type_source == ContactTypeSource.crm and type_cache_fresh(contact, config):
        return TypeResolution(
            contact.contact_type, ContactTypeSource.crm, "crm",
            from_cache=True, crm_external_id=contact.crm_external_id,
        )

    # The CRM lookup can be disabled per-client (classification_config). When it
    # is, we read the local row rather than hitting the network.
    if not config.crm_lookup_enabled:
        return TypeResolution(
            contact.contact_type, contact.contact_type_source, "crm",
            from_cache=False, crm_external_id=contact.crm_external_id,
        )

    crm_contact = await _safe_crm_lookup(config, phone)
    if crm_contact is None:
        # Fall through to the local row (which may itself be 'unknown').
        return TypeResolution(
            contact.contact_type, contact.contact_type_source, "crm",
            from_cache=False, crm_external_id=contact.crm_external_id,
        )

    mapped = from_crm_contact_type(crm_contact.contact_type)
    await set_contact_type(
        conn, contact.id, mapped, ContactTypeSource.crm,
        reason=f"crm_lookup:{crm_contact.contact_type.value}",
    )
    if crm_contact.external_id:
        await conn.execute(
            "UPDATE contacts SET crm_external_id = $2 WHERE id = $1",
            contact.id, crm_contact.external_id,
        )
    return TypeResolution(
        mapped, ContactTypeSource.crm, "crm",
        from_cache=False, crm_external_id=crm_contact.external_id,
    )


async def _crm_write_back(config: ClientConfig, contact: Contact) -> bool:
    """Write a manual contact type back to the client's CRM.

    Reserved: no adapter exposes a contact-type write yet, so this is a logged
    no-op today. It exists as the single gated seam so that when write-back is
    built, it is the ONLY path that can push a type outward — and only for the
    manual, flag-enabled case its caller guards.
    """
    logger.info(
        "contact write-back requested but not implemented for provider",
        extra={"provider": config.crm_provider, "contact_id": str(contact.id)},
    )
    return False


async def maybe_write_back_contact_type(
    conn: Any,
    config: ClientConfig,
    contact: Contact,
) -> bool:
    """Write a contact type back to the CRM only when explicitly allowed.

    Two guards, both off by default: the flag must be on, AND the type must be a
    human (`manual`) decision. TraceFlow never pushes an inferred or crm-sourced
    type back — silently retagging a contractor's CRM is a trust-destroying event.
    Returns whether a write-back was attempted.
    """
    if not config.crm_write_back_contact_type:
        return False
    if contact.contact_type_source != ContactTypeSource.manual:
        logger.info(
            "contact write-back skipped: not a manual classification",
            extra={"contact_id": str(contact.id), "source": contact.contact_type_source.value},
        )
        return False
    if not _crm_available(config):
        return False
    return await _crm_write_back(config, contact)
