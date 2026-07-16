"""Twilio webhook receiver — Phase 0 LLR build.

Path: POST /webhooks/twilio/{event_type}/{client_id}
Auth: X-Twilio-Signature, verified by the tenant_resolver middleware
      (services/twilio_signature.py).

The missed-call → SMS flow is the heart of Lead Leak Recovery:
  1. Accept the missed-call webhook, dedupe on CallSid
  2. Return 200 immediately — Twilio expects a fast ack
  3. In the background: create a 'twilio_missed_call' Lead, send the
     client's greeting SMS to the caller, record the Message + events
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.adapters.registry import get_adapter
from app.db import set_tenant_context
from app.models.client_config import ClientConfig
from app.models.contact import Contact, ContactType
from app.models.lead import Lead, LeadCreate, LeadUpdate, QualificationStatus
from app.models.message import Message
from app.prompts.greeting import generate_greeting, render_customer_ack, render_vendor_ack
from app.prompts.intent import DEFAULT_INTENT_VERSION, Intent, classify_intent
from app.prompts.qualifier import qualifier_turn
from app.prompts.summarize import persist_summary, summarize_conversation
from app.services.classification import Route, classify_caller
from app.services.contacts import merge_person_facts, resolve_contact
from app.services.dedupe import is_duplicate
from app.services.owner_alert import alert_existing_customer, alert_owner, find_vip_reason
from app.services.qualification import (
    TerminationReason,
    check_hard_gates,
    completeness_score,
    get_schema,
    merge_state,
    should_terminate,
    split_extracted,
    value_score,
)
from app.services.sms import send_sms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/twilio", tags=["webhooks"])

# Sent when the first reply is too thin to classify (intent == ambiguous).
# One question only; the lead stays 'unqualified' so the intent gate re-runs
# on the next inbound.
INTENT_CLARIFIER = (
    "Happy to help! Are you after a quote for new work, or is this about an "
    "existing job? Just a quick word so I can point you to the right person."
)

# Qualification states that warrant an automatic CRM push (crm_push stage in
# docs/workflow-schema.md Section 3). high_value is included for forward
# compatibility — the qualifier sets 'qualified' today.
_CRM_PUSH_STATUSES = frozenset(
    {QualificationStatus.qualified, QualificationStatus.high_value}
)

# Deterministic termination → the lead's terminal status. Code owns this now;
# the qualifier no longer sets qualification_status.
_STATUS_BY_TERMINATION: dict[TerminationReason, QualificationStatus] = {
    TerminationReason.qualified: QualificationStatus.qualified,
    TerminationReason.disqualified: QualificationStatus.disqualified,
    TerminationReason.needs_review: QualificationStatus.needs_review,
}

# Twilio reads a webhook's HTTP response body as TwiML. Every real message
# (greeting, qualifier reply, owner alert) is sent asynchronously via the REST
# API in the background task, so the synchronous ack must be an EMPTY TwiML
# document. A non-empty body like "ok" gets echoed to the caller as an
# auto-reply on the messaging webhook, and fails TwiML parsing (caller hears an
# "application error") on the voice webhook.
_TWIML_ACK = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _ack() -> Response:
    """Empty-TwiML 200 — tells Twilio 'received, nothing to say synchronously'."""
    return Response(status_code=200, content=_TWIML_ACK, media_type="application/xml")


_DEFAULT_TIMEZONE = "America/Los_Angeles"


async def _fetch_timezone(conn: Any, client_id: UUID) -> str:
    """The client's timezone (clients.timezone) for the prompt context's time
    block. Falls back to a default if unset or unreadable."""
    tz = await conn.fetchval("SELECT timezone FROM clients WHERE id = $1", client_id)
    return tz if isinstance(tz, str) and tz else _DEFAULT_TIMEZONE


@router.post("/missed-call/{client_id}")
async def missed_call_webhook(
    client_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    # Twilio webhooks are form-encoded. The X-Twilio-Signature was already
    # verified by the tenant_resolver middleware before we got here.
    form = await request.form()
    payload: dict[str, Any] = {k: str(v) for k, v in form.items()}

    call_sid = payload.get("CallSid")
    caller = payload.get("From")

    # Twilio retries on timeout/5xx — dedupe on CallSid so a retry can't
    # create a second lead or fire a second SMS at the caller.
    if call_sid and is_duplicate(client_id, source="twilio", external_id=call_sid):
        return _ack()

    logger.info(
        "twilio missed call accepted",
        extra={"client_id": str(client_id), "call_sid": call_sid, "from": caller},
    )

    background_tasks.add_task(_process_missed_call, client_id, payload)
    return _ack()


@router.post("/sms-reply/{client_id}")
async def sms_reply_webhook(
    client_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Inbound SMS from a lead — one turn of the qualification conversation."""
    form = await request.form()
    payload: dict[str, Any] = {k: str(v) for k, v in form.items()}

    message_sid = payload.get("MessageSid")
    if message_sid and is_duplicate(client_id, source="twilio_sms", external_id=message_sid):
        return _ack()

    logger.info(
        "twilio sms reply accepted",
        extra={"client_id": str(client_id), "from": payload.get("From")},
    )

    background_tasks.add_task(_process_sms_reply, client_id, payload)
    return _ack()


async def _process_missed_call(client_id: UUID, payload: dict[str, Any]) -> None:
    """Classify the caller, create the lead, and (if warranted) greet them.

    Runs as a background task so the webhook 200 returns to Twilio
    immediately. Caller classification routes existing customers, vendors,
    and active conversations away from the default recovery greeting — but a
    classification failure always degrades to potential_lead, so a real lead
    is never dropped. send_sms handles its own failures; a DB error here
    surfaces in the server log but cannot delay the ack.
    """
    caller = payload.get("From")
    call_sid = payload.get("CallSid")

    lead = LeadCreate(
        client_id=client_id,
        source_system="twilio_missed_call",
        external_id=call_sid,
        phone=caller,
        raw_payload=payload,
    )

    # The DB transaction must not stay open across the Twilio SMS call, so
    # the lead is persisted here and the message recorded in a second block.
    async with set_tenant_context(client_id) as conn:
        config_row = await conn.fetchrow(
            "SELECT * FROM client_configs WHERE client_id = $1", client_id
        )
        if config_row is None:
            logger.error("missed call: no client_config", extra={"client_id": str(client_id)})
            return
        config = ClientConfig(**dict(config_row))
        timezone = await _fetch_timezone(conn, client_id)

        result = await classify_caller(conn, client_id, caller, config)

        # Every missed call bumps the contact's call_count (when we have one).
        if result.contact is not None:
            await conn.execute(
                "UPDATE contacts SET call_count = call_count + 1 WHERE id = $1",
                result.contact.id,
            )

        # Active conversation: a lead is already open and fresh. Don't spawn a
        # duplicate or re-greet — just record the repeat call.
        if result.route == Route.active_conversation:
            await conn.execute(
                """
                INSERT INTO events (client_id, lead_id, event_type, payload)
                VALUES ($1, $2, 'missed_call_during_active_conversation', $3)
                """,
                client_id,
                result.existing_lead_id,
                {"call_sid": call_sid, "from": caller},
            )
            logger.info(
                "missed call during active conversation — no new lead",
                extra={"client_id": str(client_id), "lead_id": str(result.existing_lead_id)},
            )
            return

        if result.route == Route.resumed_conversation and result.existing_lead_id is not None:
            # Stale-open lead resumed — reuse it (no duplicate CRM record) and
            # re-greet. The recognition greeting content lands in Slice 4.
            lead_id = result.existing_lead_id
            await conn.execute(
                """
                INSERT INTO events (client_id, lead_id, event_type, payload)
                VALUES ($1, $2, 'conversation_resumed', $3)
                """,
                client_id,
                lead_id,
                {"call_sid": call_sid, "reason": result.reason},
            )
        else:
            # A new lead for every other route. A returning contact (or a resume
            # without reuse) pre-populates person-scoped facts so we don't
            # re-ask for what we already know.
            seed_name: str | None = None
            seed_address: str | None = None
            if result.is_returning and result.contact is not None:
                facts = result.contact.known_facts
                seed_name = facts.get("contact_name")
                seed_address = facts.get("address")
            contact_id = result.contact.id if result.contact else None

            lead_row = await conn.fetchrow(
                """
                INSERT INTO leads
                    (client_id, external_id, source_system, phone, raw_payload,
                     classification, contact_id, contact_name, address)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
                """,
                client_id,
                lead.external_id,
                lead.source_system,
                lead.phone,
                payload,
                result.classification.value,
                contact_id,
                seed_name,
                seed_address,
            )
            lead_id = lead_row["id"] if lead_row else None

            event_type = "returning_contact" if result.is_returning else "twilio_missed_call_received"
            await conn.execute(
                f"""
                INSERT INTO events (client_id, lead_id, event_type, payload)
                VALUES ($1, $2, '{event_type}', $3)
                """,
                client_id,
                lead_id,
                {
                    "call_sid": call_sid,
                    "route": result.route.value,
                    "classification": result.classification.value,
                    "reason": result.reason,
                    "is_returning": result.is_returning,
                },
            )

    # Existing customer reaching voicemail is a priority service event —
    # alert the business regardless of whether the caller is also texted.
    if result.route == Route.existing_customer:
        contact_name = result.contact.name if result.contact else None
        summary = f"{contact_name or 'A customer'} ({caller or 'unknown number'})"
        await alert_existing_customer(config, summary=summary)

    # Routing says don't send the recovery greeting (vendor, opted-out
    # existing customer, or — once Slice 3 lands — spam). Record why, bill no
    # AI interaction, and stop here.
    if not result.should_text:
        async with set_tenant_context(client_id) as conn:
            await conn.execute(
                """
                INSERT INTO events (client_id, lead_id, event_type, payload)
                VALUES ($1, $2, 'greeting_suppressed', $3)
                """,
                client_id,
                lead_id,
                {
                    "route": result.route.value,
                    "reason": result.reason,
                    "classification": result.classification.value,
                },
            )
        logger.info(
            "missed-call greeting suppressed by classification",
            extra={
                "client_id": str(client_id),
                "lead_id": str(lead_id),
                "route": result.route.value,
            },
        )
        return

    if not caller:
        logger.warning("missed call: no caller number — cannot send greeting", extra={"client_id": str(client_id)})
        return
    if not config.twilio_number:
        logger.warning("missed call: client has no twilio_number configured", extra={"client_id": str(client_id)})
        return

    # Route-specific greeting. Existing customers and vendors get a static
    # service ack (no sales qualification, no AI); everyone else gets the AI
    # greeting — neutral for a first-timer, recognition for a returning caller.
    if result.route == Route.existing_customer:
        greeting = render_customer_ack(config)
        ai_generated = False
        prompt_version = "greeting:customer_ack"
    elif result.route == Route.known_non_lead:
        greeting = render_vendor_ack(config)
        ai_generated = False
        prompt_version = "greeting:vendor_ack"
    else:
        ai_result = await generate_greeting(
            config, result.contact, is_returning=result.is_returning, timezone=timezone
        )
        if ai_result is not None:
            greeting, greeting_version = ai_result
            ai_generated = True
            prompt_version = f"greeting:{greeting_version}"
        else:
            # No API key, or the AI call failed — fall back to a static template.
            greeting = _render_greeting(config)
            ai_generated = False
            prompt_version = None

    sms_result = await send_sms(to=caller, body=greeting, from_number=config.twilio_number)

    async with set_tenant_context(client_id) as conn:
        if ai_generated:
            await conn.execute(
                "UPDATE client_configs SET ai_interactions_used = ai_interactions_used + 1 "
                "WHERE client_id = $1",
                client_id,
            )
        if sms_result:
            await conn.execute(
                """
                INSERT INTO messages
                    (client_id, lead_id, direction, channel, body,
                     ai_generated, prompt_version, raw_payload)
                VALUES ($1, $2, 'outbound', 'sms', $3, $4, $5, $6)
                """,
                client_id,
                lead_id,
                greeting,
                ai_generated,
                prompt_version,
                sms_result,
            )
            await _touch_lead_activity(conn, lead_id, client_id, direction="outbound")
            await conn.execute(
                """
                INSERT INTO events (client_id, lead_id, event_type, payload)
                VALUES ($1, $2, 'greeting_sms_sent', $3)
                """,
                client_id,
                lead_id,
                {"to": caller, "sid": sms_result.get("sid"), "ai_generated": ai_generated},
            )
            logger.info(
                "missed-call greeting sent",
                extra={
                    "client_id": str(client_id),
                    "lead_id": str(lead_id),
                    "ai_generated": ai_generated,
                },
            )
        else:
            await conn.execute(
                """
                INSERT INTO events (client_id, lead_id, event_type, payload)
                VALUES ($1, $2, 'greeting_sms_failed', $3)
                """,
                client_id,
                lead_id,
                {"to": caller},
            )
            logger.error(
                "missed-call greeting failed to send",
                extra={"client_id": str(client_id), "lead_id": str(lead_id)},
            )


def _render_greeting(config: ClientConfig) -> str:
    """Build the first-touch SMS.

    Uses the client's greeting_template if configured (with the
    {business_name} token substituted), otherwise a sensible default.
    """
    business = config.business_name or "us"
    template = config.greeting_template
    if template:
        return template.replace("{business_name}", business)
    return (
        f"Hi! Thanks for calling {business} — sorry we missed you. "
        "Reply to this text and we'll help you out right away."
    )


async def _process_sms_reply(client_id: UUID, payload: dict[str, Any]) -> None:
    """Run one qualification turn for an inbound SMS.

    Runs as a background task: find the active lead, replay the SMS
    history to the qualifier, apply extracted fields, and reply. If the
    qualifier is unavailable the inbound message is still saved and the
    lead is flagged for human review — a lead is never dropped.
    """
    caller = payload.get("From")
    body = (payload.get("Body") or "").strip()
    if not caller or not body:
        logger.warning("sms reply: missing From or Body", extra={"client_id": str(client_id)})
        return

    # Block 1: resolve the contact, find or OPEN a lead, persist the inbound
    # message + activity, load history.
    async with set_tenant_context(client_id) as conn:
        config_row = await conn.fetchrow(
            "SELECT * FROM client_configs WHERE client_id = $1", client_id
        )
        if config_row is None:
            logger.error("sms reply: no client_config", extra={"client_id": str(client_id)})
            return
        config = ClientConfig(**dict(config_row))
        timezone = await _fetch_timezone(conn, client_id)

        contact = await resolve_contact(conn, client_id, caller, config.default_phone_region)

        # A blocked or spam contact never gets a reply.
        if contact.contact_type in (ContactType.blocked, ContactType.spam):
            await conn.execute(
                """
                INSERT INTO events (client_id, lead_id, event_type, payload)
                VALUES ($1, NULL, 'inbound_sms_dropped', $2)
                """,
                client_id,
                {"contact_type": contact.contact_type.value, "reason": "blocked_or_spam_contact"},
            )
            logger.info(
                "sms reply dropped: blocked/spam contact",
                extra={"client_id": str(client_id), "contact_id": str(contact.id)},
            )
            return

        lead_row = await conn.fetchrow(
            """
            SELECT *
            FROM leads
            WHERE contact_id = $1 AND qualification_status IN ('unqualified', 'qualifying')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            contact.id,
        )
        if lead_row is None:
            # No open lead: a cold inbound (texted without calling first) or a
            # follow-up after a terminal status. Either way it's a real lead —
            # open a new one linked to the contact, seeded with known facts.
            # This closes the silent-drop bug: an inbound SMS is never ignored.
            facts = contact.known_facts
            lead_row = await conn.fetchrow(
                """
                INSERT INTO leads
                    (client_id, source_system, phone, raw_payload, classification,
                     contact_id, contact_name, address)
                VALUES ($1, 'twilio_sms_inbound', $2, $3, 'potential_lead', $4, $5, $6)
                RETURNING *
                """,
                client_id,
                caller,
                payload,
                contact.id,
                facts.get("contact_name"),
                facts.get("address"),
            )
            await conn.execute(
                """
                INSERT INTO events (client_id, lead_id, event_type, payload)
                VALUES ($1, $2, 'inbound_sms_lead_opened', $3)
                """,
                client_id,
                lead_row["id"],
                {"from": caller, "contact_id": str(contact.id)},
            )

        lead = Lead(**dict(lead_row))
        lead_id = lead.id
        prior_status = lead.qualification_status

        await conn.execute(
            """
            INSERT INTO messages (client_id, lead_id, direction, channel, body, raw_payload)
            VALUES ($1, $2, 'inbound', 'sms', $3, $4)
            """,
            client_id,
            lead_id,
            body,
            payload,
        )
        await _touch_lead_activity(conn, lead_id, client_id, direction="inbound")
        history_rows = await conn.fetch(
            """
            SELECT * FROM messages
            WHERE lead_id = $1 AND channel = 'sms'
            ORDER BY created_at
            """,
            lead_id,
        )
        # The owner is alerted at most once per lead.
        already_alerted = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM events "
            "WHERE lead_id = $1 AND event_type = 'owner_alert_sent')",
            lead_id,
        )

    history = [Message(**dict(r)) for r in history_rows]

    # Intent gate: on the FIRST reply (still 'unqualified'), triage before the
    # qualifier. Genuine sales — or a classifier outage — proceeds; existing
    # customers, non-leads, and spam route off the sales track and return here.
    if prior_status == QualificationStatus.unqualified:
        proceed = await _route_first_reply_intent(
            client_id, lead_id, config, history, caller=caller,
            contact=contact, timezone=timezone,
        )
        if not proceed:
            return

    # Deterministic loop control (Slice 3): build the captured state, ask the
    # model for the next question + extractions, then let CODE decide scoring
    # and termination. The model never sets qualification_status.
    schema = get_schema(config)
    state = merge_state(lead, contact)
    current_turn = lead.turn_count + 1  # includes the inbound just recorded

    turn = await qualifier_turn(
        config, history, schema, state, current_turn, contact=contact, timezone=timezone
    )

    if turn is None:
        # Qualifier unavailable — the inbound message is saved; flag for a human.
        async with set_tenant_context(client_id) as conn:
            await conn.execute(
                "UPDATE leads SET qualification_status = 'needs_review' WHERE id = $1",
                lead_id,
            )
            await conn.execute(
                """
                INSERT INTO events (client_id, lead_id, event_type, payload)
                VALUES ($1, $2, 'qualifier_unavailable', $3)
                """,
                client_id,
                lead_id,
                {},
            )
        logger.warning(
            "sms reply: qualifier unavailable — lead flagged needs_review",
            extra={"client_id": str(client_id), "lead_id": str(lead_id)},
        )
        return

    reply_text, extracted, version = turn
    # Split extractions: canonical → leads columns; the rest → qualification_data;
    # person-scoped → contacts.known_facts.
    canonical, qual_data, person = split_extracted(schema, extracted)
    updates = LeadUpdate(**canonical).model_dump(exclude_unset=True) if canonical else {}

    # Deterministic scoring + termination over the post-turn state.
    new_state = {**state, **canonical, **qual_data}
    completeness = completeness_score(schema, new_state)
    value = value_score(schema, new_state, config)
    gate = check_hard_gates(schema, new_state, config)
    termination = (
        TerminationReason.disqualified
        if gate is not None
        else should_terminate(schema, new_state, current_turn)
    )
    new_status = _STATUS_BY_TERMINATION.get(termination) if termination else None

    sms_result = None
    if reply_text and config.twilio_number:
        sms_result = await send_sms(to=caller, body=reply_text, from_number=config.twilio_number)

    # Block 2: apply extractions + scores + status + person facts; record output.
    async with set_tenant_context(client_id) as conn:
        if updates:
            await _apply_lead_updates(conn, lead_id, client_id, updates)
        if qual_data:
            await conn.execute(
                "UPDATE leads SET qualification_data = qualification_data || $2::jsonb "
                "WHERE id = $1 AND client_id = $3",
                lead_id,
                qual_data,
                client_id,
            )
        await conn.execute(
            "UPDATE leads SET qualification_score = $2, value_score = $3 "
            "WHERE id = $1 AND client_id = $4",
            lead_id,
            completeness,
            value,
            client_id,
        )
        if new_status is not None:
            await conn.execute(
                "UPDATE leads SET qualification_status = $2 WHERE id = $1 AND client_id = $3",
                lead_id,
                new_status.value,
                client_id,
            )
        if person:
            await merge_person_facts(conn, contact.id, person, schema)
        await _bill_ai_interaction(conn, client_id)
        if sms_result:
            await conn.execute(
                """
                INSERT INTO messages
                    (client_id, lead_id, direction, channel, body,
                     ai_generated, prompt_version, raw_payload)
                VALUES ($1, $2, 'outbound', 'sms', $3, TRUE, $4, $5)
                """,
                client_id,
                lead_id,
                reply_text,
                f"qualifier:{version}",
                sms_result,
            )
            await _touch_lead_activity(conn, lead_id, client_id, direction="outbound")
        await conn.execute(
            """
            INSERT INTO events (client_id, lead_id, event_type, payload)
            VALUES ($1, $2, 'qualifier_turn', $3)
            """,
            client_id,
            lead_id,
            {
                "fields_extracted": sorted([*canonical.keys(), *qual_data.keys()]),
                "completeness": completeness,
                "value_score": value,
                "termination": termination.value if termination else None,
                "reply_sent": sms_result is not None,
            },
        )

    logger.info(
        "qualifier turn complete",
        extra={
            "client_id": str(client_id),
            "lead_id": str(lead_id),
            "completeness": completeness,
            "termination": termination.value if termination else None,
        },
    )

    # crm_push (workflow-schema Section 3): a lead CODE just moved to a qualified
    # state lands in the client's CRM automatically. No-op when the turn didn't
    # qualify it, the client has no CRM, or it was already pushed.
    if new_status in _CRM_PUSH_STATUSES:
        await _maybe_push_to_crm(client_id, lead_id, config)

    # VIP check — alert the owner at most once per lead.
    if not already_alerted:
        await _check_vip_and_alert(
            client_id,
            lead_id,
            config,
            history,
            budget_range=canonical.get("budget_range") or lead.budget_range,
            contact_name=canonical.get("contact_name") or lead.contact_name,
            service_type=canonical.get("service_type") or lead.service_type,
            caller=caller,
        )

    # On a terminal transition, roll the conversation into a durable contact
    # summary so the next call arrives with context. One AI interaction; non-fatal.
    if new_status is not None:
        await _summarize_on_terminal(client_id, config, contact, lead, history, schema)


async def _route_first_reply_intent(
    client_id: UUID,
    lead_id: UUID,
    config: ClientConfig,
    history: list[Message],
    *,
    caller: str,
    contact: Contact | None = None,
    timezone: str = _DEFAULT_TIMEZONE,
) -> bool:
    """Triage the first inbound reply before qualification.

    Returns True to proceed to the qualifier — genuine sales intent, OR the
    classifier was unavailable (no key / failure), in which case we degrade
    toward qualifying so a real lead is never dropped. Returns False for a
    terminal route (existing customer, non-lead, spam) or an ambiguous reply
    that got a clarifying question; in those cases this function has already
    recorded the outcome.

    A real classification (intent is not None) bills one AI interaction.
    """
    intent = await classify_intent(config, history, contact=contact, timezone=timezone)
    billable = intent is not None

    if intent is None or intent == Intent.sales:
        async with set_tenant_context(client_id) as conn:
            await conn.execute(
                "UPDATE leads SET qualification_status = 'qualifying' "
                "WHERE id = $1 AND qualification_status = 'unqualified'",
                lead_id,
            )
            if billable:
                await _bill_ai_interaction(conn, client_id)
            await _record_intent_event(conn, client_id, lead_id, intent, proceeded=True)
        return True

    if intent == Intent.existing_customer:
        await alert_existing_customer(
            config, summary=_existing_customer_summary(history, caller)
        )
        async with set_tenant_context(client_id) as conn:
            await conn.execute(
                "UPDATE leads SET qualification_status = 'support_touch', "
                "classification = 'existing_customer' WHERE id = $1",
                lead_id,
            )
            await _bill_ai_interaction(conn, client_id)
            await _record_intent_event(conn, client_id, lead_id, intent, proceeded=False)
        logger.info(
            "intent: existing customer — qualifier skipped, owner alerted",
            extra={"client_id": str(client_id), "lead_id": str(lead_id)},
        )
        return False

    if intent == Intent.non_lead:
        async with set_tenant_context(client_id) as conn:
            await conn.execute(
                "UPDATE leads SET qualification_status = 'non_lead_contact', "
                "classification = 'known_non_lead' WHERE id = $1",
                lead_id,
            )
            await _bill_ai_interaction(conn, client_id)
            await _record_intent_event(conn, client_id, lead_id, intent, proceeded=False)
        logger.info(
            "intent: non-lead — qualifier skipped",
            extra={"client_id": str(client_id), "lead_id": str(lead_id)},
        )
        return False

    if intent == Intent.spam:
        async with set_tenant_context(client_id) as conn:
            await conn.execute(
                "UPDATE leads SET qualification_status = 'spam', "
                "classification = 'spam' WHERE id = $1",
                lead_id,
            )
            await _bill_ai_interaction(conn, client_id)
            await _record_intent_event(conn, client_id, lead_id, intent, proceeded=False)
        logger.info(
            "intent: spam — no reply sent",
            extra={"client_id": str(client_id), "lead_id": str(lead_id)},
        )
        return False

    # ambiguous: ask ONE clarifying question and wait. The lead stays
    # 'unqualified', so the intent gate re-runs on the next inbound reply.
    sms_result = None
    if config.twilio_number:
        sms_result = await send_sms(
            to=caller, body=INTENT_CLARIFIER, from_number=config.twilio_number
        )
    async with set_tenant_context(client_id) as conn:
        await _bill_ai_interaction(conn, client_id)
        if sms_result:
            await conn.execute(
                """
                INSERT INTO messages
                    (client_id, lead_id, direction, channel, body,
                     ai_generated, prompt_version, raw_payload)
                VALUES ($1, $2, 'outbound', 'sms', $3, TRUE, $4, $5)
                """,
                client_id,
                lead_id,
                INTENT_CLARIFIER,
                f"intent:{DEFAULT_INTENT_VERSION}",
                sms_result,
            )
            await _touch_lead_activity(conn, lead_id, client_id, direction="outbound")
        await _record_intent_event(conn, client_id, lead_id, intent, proceeded=False)
    logger.info(
        "intent: ambiguous — clarifying question sent",
        extra={"client_id": str(client_id), "lead_id": str(lead_id)},
    )
    return False


async def _summarize_on_terminal(
    client_id: UUID,
    config: ClientConfig,
    contact: Contact | None,
    lead: Lead,
    history: list[Message],
    schema: Any,
) -> None:
    """Distill a just-finished conversation into a durable contact summary +
    person facts (summarize.py). One AI interaction; failure is non-fatal."""
    if contact is None:
        return
    result = await summarize_conversation(config, contact, lead, history)
    if result is None:
        return
    summary, person_facts = result
    async with set_tenant_context(client_id) as conn:
        await persist_summary(conn, contact, summary, person_facts, schema)
        await _bill_ai_interaction(conn, client_id)
        await conn.execute(
            """
            INSERT INTO events (client_id, lead_id, event_type, payload)
            VALUES ($1, $2, 'conversation_summarized', $3)
            """,
            client_id,
            lead.id,
            {"summary": summary},
        )
    logger.info(
        "conversation summarized onto contact",
        extra={"client_id": str(client_id), "contact_id": str(contact.id)},
    )


async def _bill_ai_interaction(conn: Any, client_id: UUID) -> None:
    """Charge one AI interaction against the client's monthly cap."""
    await conn.execute(
        "UPDATE client_configs SET ai_interactions_used = ai_interactions_used + 1 "
        "WHERE client_id = $1",
        client_id,
    )


async def _touch_lead_activity(
    conn: Any,
    lead_id: UUID,
    client_id: UUID,
    *,
    direction: str,
) -> None:
    """Advance a lead's conversation state on every message (migration 019).

    Bumps last_inbound_at / last_outbound_at and turn_count so the resume window
    (classify_caller) tracks real activity, not just creation time. `direction`
    is a fixed 'inbound'/'outbound' literal from the caller, never user input."""
    column = "last_inbound_at" if direction == "inbound" else "last_outbound_at"
    await conn.execute(
        f"UPDATE leads SET {column} = now(), turn_count = turn_count + 1 "
        "WHERE id = $1 AND client_id = $2",
        lead_id,
        client_id,
    )


async def _record_intent_event(
    conn: Any,
    client_id: UUID,
    lead_id: UUID,
    intent: Intent | None,
    *,
    proceeded: bool,
) -> None:
    """Record the intent decision. intent is None when the classifier was
    unavailable and we degraded toward the qualifier."""
    await conn.execute(
        """
        INSERT INTO events (client_id, lead_id, event_type, payload)
        VALUES ($1, $2, 'intent_classified', $3)
        """,
        client_id,
        lead_id,
        {"intent": intent.value if intent else None, "proceeded": proceeded},
    )


def _existing_customer_summary(history: list[Message], caller: str) -> str:
    """Build a one-line owner alert from the caller and their latest text."""
    latest = next(
        (m.body for m in reversed(history) if m.direction == "inbound"),
        "",
    )
    if latest:
        return f"Existing customer {caller} replied: {latest}"
    return f"Existing customer {caller} replied to the missed-call text."


async def _check_vip_and_alert(
    client_id: UUID,
    lead_id: UUID,
    config: ClientConfig,
    history: list[Message],
    *,
    budget_range: str | None,
    contact_name: str | None,
    service_type: str | None,
    caller: str,
) -> None:
    """Alert the owner if the lead matches a VIP keyword or value trigger."""
    inbound_text = " ".join(m.body for m in history if m.direction == "inbound")
    reason = find_vip_reason(config, text=inbound_text, budget_range=budget_range)
    if reason is None:
        return

    lead_summary = (
        f"{contact_name or 'Unknown caller'} ({caller}) — "
        f"{service_type or 'service TBD'}, budget {budget_range or 'unknown'}"
    )
    delivered = await alert_owner(config, lead_summary=lead_summary, reason=reason)

    async with set_tenant_context(client_id) as conn:
        await conn.execute(
            """
            INSERT INTO events (client_id, lead_id, event_type, payload)
            VALUES ($1, $2, 'owner_alert_sent', $3)
            """,
            client_id,
            lead_id,
            {"reason": reason, "delivered": delivered},
        )
    logger.info(
        "owner alert sent",
        extra={"client_id": str(client_id), "lead_id": str(lead_id), "reason": reason},
    )


async def _maybe_push_to_crm(
    client_id: UUID,
    lead_id: UUID,
    config: ClientConfig,
) -> None:
    """Push a freshly-qualified lead into the client's CRM (crm_push stage).

    Fire-and-forget from the qualifier turn, with the same graceful-degradation
    contract as the rest of the pipeline — it never raises into the caller:
      * No-op when the client has no crm_provider or the provider has no adapter.
      * Idempotent: a lead that already has an external_id is left alone, so a
        re-run can never create a duplicate CRM record (mirrors admin re-push).
      * On failure, the error is logged to the events stream so the founder can
        re-push from the admin tool; the lead row itself is untouched.

    The adapter call is external network IO and is made outside any DB
    transaction; the resulting external_id + pushed_to_crm_at are committed in
    a separate tenant-scoped block.
    """
    if not config.crm_provider:
        return
    try:
        adapter = get_adapter(config.crm_provider)
    except ValueError:
        logger.warning(
            "crm push: unknown crm_provider — skipping",
            extra={"client_id": str(client_id), "provider": config.crm_provider},
        )
        return

    async with set_tenant_context(client_id) as conn:
        lead_row = await conn.fetchrow("SELECT * FROM leads WHERE id = $1", lead_id)
    if lead_row is None:
        return
    lead = Lead(**dict(lead_row))

    # Already in the CRM — updates are the admin re-push's job, not the hot path.
    if lead.external_id is not None:
        return

    try:
        external_id = await adapter.push_lead(lead, config)
    except Exception as e:
        logger.warning(
            "crm push failed",
            extra={
                "client_id": str(client_id),
                "lead_id": str(lead_id),
                "provider": config.crm_provider,
            },
            exc_info=e,
        )
        async with set_tenant_context(client_id) as conn:
            await conn.execute(
                """
                INSERT INTO events (client_id, lead_id, event_type, payload)
                VALUES ($1, $2, 'crm_push_failed', $3)
                """,
                client_id,
                lead_id,
                {"provider": config.crm_provider, "error": str(e)[:500]},
            )
        return

    async with set_tenant_context(client_id) as conn:
        await conn.execute(
            "UPDATE leads SET external_id = $1, pushed_to_crm_at = NOW() "
            "WHERE id = $2 AND client_id = $3",
            external_id,
            lead_id,
            client_id,
        )
        await conn.execute(
            """
            INSERT INTO events (client_id, lead_id, event_type, payload)
            VALUES ($1, $2, 'crm_pushed', $3)
            """,
            client_id,
            lead_id,
            {"provider": config.crm_provider, "external_id": external_id},
        )
    logger.info(
        "crm push complete",
        extra={
            "client_id": str(client_id),
            "lead_id": str(lead_id),
            "provider": config.crm_provider,
            "external_id": external_id,
        },
    )


async def _apply_lead_updates(
    conn: Any,
    lead_id: UUID,
    client_id: UUID,
    updates: dict[str, Any],
) -> None:
    """Apply validated LeadUpdate fields to the lead.

    Column names come from LeadUpdate's fixed field set (not user input),
    so the dynamic SET clause is safe; values are parameterized.
    """
    cols = list(updates.keys())
    set_clause = ", ".join(f"{col} = ${i}" for i, col in enumerate(cols, start=1))
    await conn.execute(
        f"UPDATE leads SET {set_clause} WHERE id = ${len(cols) + 1} AND client_id = ${len(cols) + 2}",
        *[updates[col] for col in cols],
        lead_id,
        client_id,
    )
