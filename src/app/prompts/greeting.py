"""Missed-call greeting prompts.

Three AI-selectable situations, plus two static acks:
  * neutral   — an unknown, first-touch caller (GREETING_NEUTRAL_V1).
  * returning — a recognized caller (GREETING_RETURNING_V1): greet by name and
    ask same-project-or-new in one line. NEVER guess a name — a null name falls
    back to neutral.
  * customer / vendor — a service acknowledgment rendered from the client's
    existing_customer_template / vendor_ack_template (no AI, no sales
    qualification). Handled by render_customer_ack / render_vendor_ack.

generate_greeting builds the unified prompt context (business block cached; the
caller block gives the AI the returning caller's history) and returns
(sms_text, version), or None when the API key is unset or the call fails — the
caller falls back to a static template so the lead always gets a text.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.models.client_config import ClientConfig
from app.models.contact import Contact
from app.prompts.context import build_prompt_context
from app.services.ai import get_anthropic_client

logger = logging.getLogger(__name__)

GREETING_MODEL = "claude-haiku-4-5"
GREETING_MAX_TOKENS = 200
DEFAULT_GREETING_VERSION = "neutral_v1"

GREETING_NEUTRAL_V1 = """\
Write a short SMS (under 160 characters) on behalf of the business above to a caller whose call was just missed. It must:
1. Apologize for missing their call
2. Identify the business by name
3. Ask what they need help with

Do not use emojis unless the tone is "casual". Do not promise specific response times. Do not give pricing or quotes. Respond with ONLY the SMS body — no quotes, no commentary, no sign-off.
"""

GREETING_RETURNING_V1 = """\
This caller is known to us — see the caller block above for their name and history. Write a short SMS (under 160 characters) that:
1. Greets them BY NAME (use the exact name in the caller block; never invent one)
2. Apologizes for missing their call
3. In ONE line, asks whether this is about their previous project or something new

Sound warm and familiar, not scripted. Do not use emojis unless the tone is "casual". Do not give pricing. Respond with ONLY the SMS body — no quotes, no commentary.
"""

PROMPT_VERSIONS: dict[str, str] = {
    "neutral_v1": GREETING_NEUTRAL_V1,
    "returning_v1": GREETING_RETURNING_V1,
}


def _select_version(config: ClientConfig, contact: Contact | None, is_returning: bool) -> str:
    """Pick the greeting variant. Returning requires a known name AND the client
    opting in AND a returning signal (the classifier's is_returning, or a
    CRM-linked contact). Never guess: a null name always falls back to neutral."""
    if (
        contact is not None
        and contact.name
        and config.recognize_returning_callers
        and (is_returning or contact.crm_external_id)
    ):
        return "returning_v1"
    return DEFAULT_GREETING_VERSION


async def generate_greeting(
    config: ClientConfig,
    contact: Contact | None = None,
    *,
    is_returning: bool = False,
    timezone: str = "America/Los_Angeles",
) -> tuple[str, str] | None:
    """Generate the missed-call greeting SMS. Returns (sms_text, version), or
    None when generation is not possible (no API key) or fails."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        logger.warning("anthropic_api_key not set — skipping AI greeting")
        return None

    version = config.prompt_versions.get("greeting") or _select_version(
        config, contact, is_returning
    )
    instructions = PROMPT_VERSIONS.get(version, GREETING_NEUTRAL_V1)
    # The returning variant needs the caller block; the neutral one doesn't, but
    # building the same context object either way keeps the business block cached.
    ctx = build_prompt_context(
        config,
        contact if version == "returning_v1" else None,
        timezone=timezone,
    )

    try:
        response = await get_anthropic_client().messages.create(
            model=GREETING_MODEL,
            max_tokens=GREETING_MAX_TOKENS,
            system=ctx.system_blocks(instructions),
            messages=[{"role": "user", "content": "Write the greeting SMS now."}],
        )
    except Exception as e:
        logger.warning("AI greeting generation failed", exc_info=e)
        return None

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        logger.warning("AI greeting returned empty text")
        return None
    return text, version


def render_customer_ack(config: ClientConfig) -> str:
    """Service acknowledgment for an existing customer at voicemail — no sales
    qualification. Uses the client's template if set, else a sensible default."""
    business = config.business_name or "us"
    template = config.existing_customer_template
    if template:
        return template.replace("{business_name}", business)
    return (
        f"Hi! Thanks for calling {business} — sorry we missed you. "
        "We've let the team know and someone will reach out shortly."
    )


def render_vendor_ack(config: ClientConfig) -> str:
    """Minimal acknowledgment for a known vendor/partner."""
    business = config.business_name or "us"
    template = config.vendor_ack_template
    if template:
        return template.replace("{business_name}", business)
    return f"Thanks for reaching out to {business}. We'll be in touch if we need anything."


def render_handoff(config: ClientConfig) -> str:
    """Closing SMS when qualification ends and a human takes over.

    Code owns termination (services/qualification.should_terminate), so code
    owns the closing too. On the terminal turn the model is still
    mid-conversation: its text is usually a preamble ("Got it!") or a follow-up
    question the system will never process. Neither tells the caller what
    happens next, so we replace it with this.

    The promise here is the product: a caller who reached the end of
    qualification is told a real person will contact them.
    """
    business = config.business_name or "us"
    template = config.handoff_template
    if template:
        return template.replace("{business_name}", business)
    return (
        "Perfect — that's everything I need for now. "
        f"Someone from {business} will reach out shortly to follow up. "
        "Thanks for your patience!"
    )


def render_decline(config: ClientConfig) -> str:
    """Closing SMS when a hard gate disqualified the lead (out of service area,
    or below a disqualify_if floor).

    Deliberately does NOT promise a callback — no one is going to call, and
    telling the caller otherwise is worse than saying nothing.
    """
    business = config.business_name or "us"
    template = config.decline_template
    if template:
        return template.replace("{business_name}", business)
    return (
        f"Thanks for reaching out to {business}! Unfortunately it looks like "
        "we're not able to help with this one, but we appreciate you getting in touch."
    )
