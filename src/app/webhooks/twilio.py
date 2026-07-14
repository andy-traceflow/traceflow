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
from app.models.lead import Lead, LeadCreate, LeadUpdate, QualificationStatus
from app.models.message import Message
from app.prompts.greeting import generate_greeting
from app.prompts.intent import DEFAULT_INTENT_VERSION, Intent, classify_intent
from app.prompts.qualifier import qualifier_turn
from app.services.classification import Route, classify_caller
from app.services.dedupe import is_duplicate
from app.services.owner_alert import alert_existing_customer, alert_owner, find_vip_reason
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

        result = await classify_caller(conn, client_id, caller, config)

        # Active conversation: a lead is already open for this caller. Don't
        # spawn a duplicate or re-greet — just record the repeat call.
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

        lead_row = await conn.fetchrow(
            """
            INSERT INTO leads
                (client_id, external_id, source_system, phone, raw_payload, classification)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            client_id,
            lead.external_id,
            lead.source_system,
            lead.phone,
            payload,
            result.classification.value,
        )
        lead_id = lead_row["id"] if lead_row else None

        await conn.execute(
            """
            INSERT INTO events (client_id, lead_id, event_type, payload)
            VALUES ($1, $2, 'twilio_missed_call_received', $3)
            """,
            client_id,
            lead_id,
            {
                "call_sid": call_sid,
                "route": result.route.value,
                "classification": result.classification.value,
                "reason": result.reason,
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

    ai_result = await generate_greeting(config)
    if ai_result is not None:
        greeting, greeting_version = ai_result
        ai_generated = True
        prompt_version = f"greeting:{greeting_version}"
    else:
        # No API key, or the AI call failed — fall back to the static
        # template so the lead still gets a text.
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

    # Block 1: find the active lead, persist the inbound message, load history.
    async with set_tenant_context(client_id) as conn:
        lead_row = await conn.fetchrow(
            """
            SELECT id, qualification_status, budget_range, contact_name, service_type
            FROM leads
            WHERE client_id = $1 AND phone = $2
              AND qualification_status IN ('unqualified', 'qualifying')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            client_id,
            caller,
        )
        if lead_row is None:
            logger.warning(
                "sms reply: no active lead for caller — ignoring",
                extra={"client_id": str(client_id)},
            )
            return
        lead_id = lead_row["id"]
        prior_status = lead_row["qualification_status"]
        prior_budget = lead_row["budget_range"]
        prior_contact_name = lead_row["contact_name"]
        prior_service_type = lead_row["service_type"]

        config_row = await conn.fetchrow(
            "SELECT * FROM client_configs WHERE client_id = $1", client_id
        )
        if config_row is None:
            logger.error("sms reply: no client_config", extra={"client_id": str(client_id)})
            return
        config = ClientConfig(**dict(config_row))

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
            client_id, lead_id, config, history, caller=caller
        )
        if not proceed:
            return

    turn = await qualifier_turn(config, history)

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
    updates = LeadUpdate(**extracted).model_dump(exclude_unset=True) if extracted else {}

    sms_result = None
    if reply_text and config.twilio_number:
        sms_result = await send_sms(to=caller, body=reply_text, from_number=config.twilio_number)

    # Block 2: apply extracted fields, record the outbound message + events.
    async with set_tenant_context(client_id) as conn:
        if updates:
            await _apply_lead_updates(conn, lead_id, client_id, updates)
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
                VALUES ($1, $2, 'outbound', 'sms', $3, TRUE, $4, $5)
                """,
                client_id,
                lead_id,
                reply_text,
                f"qualifier:{version}",
                sms_result,
            )
        await conn.execute(
            """
            INSERT INTO events (client_id, lead_id, event_type, payload)
            VALUES ($1, $2, 'qualifier_turn', $3)
            """,
            client_id,
            lead_id,
            {"fields_extracted": sorted(updates.keys()), "reply_sent": sms_result is not None},
        )

    logger.info(
        "qualifier turn complete",
        extra={
            "client_id": str(client_id),
            "lead_id": str(lead_id),
            "fields_extracted": len(updates),
        },
    )

    # crm_push (workflow-schema Section 3): a lead the qualifier just moved to
    # a qualified state lands in the client's CRM automatically. No-op when the
    # turn didn't qualify it, when the client has no CRM, or when it was already
    # pushed — all handled inside the helper.
    new_status = updates.get("qualification_status") or prior_status
    if new_status in _CRM_PUSH_STATUSES:
        await _maybe_push_to_crm(client_id, lead_id, config)

    # VIP check — alert the owner at most once per lead.
    if not already_alerted:
        await _check_vip_and_alert(
            client_id,
            lead_id,
            config,
            history,
            budget_range=updates.get("budget_range") or prior_budget,
            contact_name=updates.get("contact_name") or prior_contact_name,
            service_type=updates.get("service_type") or prior_service_type,
            caller=caller,
        )


async def _route_first_reply_intent(
    client_id: UUID,
    lead_id: UUID,
    config: ClientConfig,
    history: list[Message],
    *,
    caller: str,
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
    intent = await classify_intent(config, history)
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
        await _record_intent_event(conn, client_id, lead_id, intent, proceeded=False)
    logger.info(
        "intent: ambiguous — clarifying question sent",
        extra={"client_id": str(client_id), "lead_id": str(lead_id)},
    )
    return False


async def _bill_ai_interaction(conn: Any, client_id: UUID) -> None:
    """Charge one AI interaction against the client's monthly cap."""
    await conn.execute(
        "UPDATE client_configs SET ai_interactions_used = ai_interactions_used + 1 "
        "WHERE client_id = $1",
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
