"""Owner alert system — VIP keyword and value triggers.

When a lead matches a client's VIP signals — a configured keyword in the
conversation, or a budget at/above vip_value_threshold — the owner gets
an immediate email + SMS so a high-value lead can be touched fast.

Evaluation is deterministic. The AI vip_classifier in the prompt
taxonomy is a separate, later refinement.
"""

from __future__ import annotations

import logging

from app.models.client_config import ClientConfig
from app.services.notifications import notify_owner_vip, send_email
from app.services.sms import send_sms

logger = logging.getLogger(__name__)

# Lower bound (USD) of each budget_range tier. A lead meets the value
# trigger when its tier floor is >= config.vip_value_threshold.
_BUDGET_FLOOR: dict[str, float] = {
    "<5k": 0.0,
    "5k-15k": 5_000.0,
    "15k-50k": 15_000.0,
    "50k+": 50_000.0,
}


def find_vip_reason(
    config: ClientConfig,
    *,
    text: str,
    budget_range: str | None,
) -> str | None:
    """Return a human-readable reason if the lead is a VIP, else None.

    `text` is the customer's conversation text (scanned for VIP
    keywords); `budget_range` is the lead's current budget tier, if known.
    """
    haystack = text.lower()
    matched = [kw for kw in config.vip_keywords if kw and kw.lower() in haystack]
    if matched:
        return f"VIP keyword match: {', '.join(matched)}"

    threshold = config.vip_value_threshold
    if threshold is not None and budget_range:
        floor = _BUDGET_FLOOR.get(budget_range)
        if floor is not None and floor >= threshold:
            return f"Budget {budget_range} meets the ${threshold:,.0f} owner-alert threshold"

    return None


async def alert_owner(config: ClientConfig, *, lead_summary: str, reason: str) -> bool:
    """Send the owner alert over every configured channel (email + SMS).

    Returns True if at least one channel accepted the alert. Never raises
    — the underlying senders log and swallow their own failures.
    """
    delivered = await notify_owner_vip(config, lead_summary=lead_summary, reason=reason)

    if config.owner_alert_phones and config.twilio_number:
        sms_body = f"VIP lead — {reason}. {lead_summary}"
        for phone in config.owner_alert_phones:
            result = await send_sms(to=phone, body=sms_body, from_number=config.twilio_number)
            delivered = delivered or result is not None

    if not delivered:
        logger.warning(
            "owner alert: no channel delivered",
            extra={"client_id": str(config.client_id), "reason": reason},
        )
    return delivered


async def alert_existing_customer(config: ClientConfig, *, summary: str) -> bool:
    """Ping the business when a known customer reaches voicemail.

    An existing customer hitting voicemail is a priority service event,
    ranked above a cold lead — so this fires even when text_existing_customers
    suppresses the caller-facing SMS. Targets existing_customer_alert_contact
    when set (email if it looks like one, else SMS); otherwise falls back to
    the owner_alert_* lists. Never raises.
    """
    reason = "Existing customer reached voicemail"
    body_line = f"{reason}: {summary}"
    target = (config.existing_customer_alert_contact or "").strip()

    if target:
        if "@" in target:
            return await send_email(
                to=[target],
                subject="Existing customer called",
                html=_existing_customer_html(summary),
            )
        if config.twilio_number:
            result = await send_sms(to=target, body=body_line, from_number=config.twilio_number)
            return result is not None
        logger.warning(
            "existing-customer alert: phone target but no twilio_number",
            extra={"client_id": str(config.client_id)},
        )
        return False

    # No explicit contact — fall back to the standard owner alert channels.
    delivered = False
    if config.owner_alert_emails:
        delivered = await send_email(
            to=config.owner_alert_emails,
            subject="Existing customer called",
            html=_existing_customer_html(summary),
        )
    if config.owner_alert_phones and config.twilio_number:
        for phone in config.owner_alert_phones:
            result = await send_sms(to=phone, body=body_line, from_number=config.twilio_number)
            delivered = (result is not None) or delivered

    if not delivered:
        logger.warning(
            "existing-customer alert: no channel delivered",
            extra={"client_id": str(config.client_id)},
        )
    return delivered


def _existing_customer_html(summary: str) -> str:
    return f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 600px;">
        <h2>Existing customer called</h2>
        <p>A known customer reached voicemail — likely a service request.</p>
        <p>{summary}</p>
    </div>
    """
