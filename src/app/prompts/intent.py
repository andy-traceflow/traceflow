"""Post-reply intent classification.

Runs on a lead's FIRST inbound SMS, before the qualifier. It is the safety
net for existing customers and non-leads who called from a number the
pre-send CRM lookup didn't recognise, and the first real signal of genuine
sales intent. A cheap Haiku call triages the reply into one of five intents
(see docs/workflow-schema.md Section 3, intent_classification).

classify_intent returns None when the API key is unset or the call fails.
The caller treats None as "proceed as a sales lead" — degrading toward the
qualifier so a real lead is NEVER dropped on an AI outage.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

import jinja2

from app.config import get_settings
from app.models.client_config import ClientConfig
from app.models.message import Message, MessageDirection
from app.services.ai import get_anthropic_client

logger = logging.getLogger(__name__)

INTENT_MODEL = "claude-haiku-4-5"
INTENT_MAX_TOKENS = 200
DEFAULT_INTENT_VERSION = "v1"


class Intent(StrEnum):
    """What the first inbound reply reveals the texter to be."""

    sales = "sales"
    existing_customer = "existing_customer"
    non_lead = "non_lead"
    spam = "spam"
    ambiguous = "ambiguous"


INTENT_SYSTEM_V1 = """\
You are triaging the FIRST text reply to a missed-call follow-up for {{business_name}}, a {{business_category}} business serving {{service_area}}. The person called, missed a connection, and just replied by SMS. Decide what they are so the conversation routes correctly.

Classify the reply into exactly one intent:
- sales: a prospective customer interested in the service, a quote, pricing, scheduling, or describing work they want done. This is the default whenever the reply plausibly expresses interest.
- existing_customer: someone who is ALREADY a customer — referencing a job you already did, an existing invoice, a warranty, a prior appointment, or following up on completed or ongoing work.
- non_lead: not a buyer and not an existing customer — a vendor, supplier, subcontractor, recruiter, job-seeker, salesperson, partner, personal contact, or wrong number.
- spam: promotional blasts, scams, phishing, automated junk, or content unrelated to the business.
- ambiguous: genuinely too little to tell (e.g. "ok", "thanks", "?", a lone emoji). Use this ONLY when you cannot reasonably choose another label.

Bias toward sales: a missed caller is most often a prospect. When a reply could plausibly be a buyer, choose sales rather than ambiguous. Reserve ambiguous for near-empty messages.

Call the classify_intent tool with your decision. Do not write a reply to the customer.
"""

PROMPT_VERSIONS: dict[str, str] = {
    "v1": INTENT_SYSTEM_V1,
}

CLASSIFY_TOOL: dict[str, Any] = {
    "name": "classify_intent",
    "description": "Record the intent classification for the customer's first reply.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["sales", "existing_customer", "non_lead", "spam", "ambiguous"],
                "description": "The single best label for the reply.",
            },
            "reason": {
                "type": "string",
                "description": "One short phrase explaining the choice.",
            },
        },
        "required": ["intent"],
    },
}


async def classify_intent(
    config: ClientConfig,
    history: list[Message],
) -> Intent | None:
    """Classify the intent of the latest inbound reply.

    `history` is the SMS exchange so far, oldest first, ending with the
    customer's latest inbound message. Returns the parsed Intent, or None
    when the API key is unset or the call fails — the caller treats None as
    "proceed as a sales lead" so a real lead is never dropped.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        logger.warning("anthropic_api_key not set — skipping intent classification")
        return None

    api_messages = _to_api_messages(history)
    if not api_messages:
        logger.warning("intent classification: no inbound message to classify")
        return None

    version = config.prompt_versions.get("intent", DEFAULT_INTENT_VERSION)
    system = _render_system(config, version)

    try:
        response = await get_anthropic_client().messages.create(
            model=INTENT_MODEL,
            max_tokens=INTENT_MAX_TOKENS,
            system=system,
            messages=api_messages,
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_intent"},
        )
    except Exception as e:
        logger.warning("intent classification failed", exc_info=e)
        return None

    for block in response.content:
        if block.type == "tool_use" and block.name == "classify_intent":
            raw = block.input.get("intent")
            try:
                return Intent(raw)
            except ValueError:
                logger.warning("intent classification: unknown intent %r", raw)
                return None

    logger.warning("intent classification: no classify_intent tool call in response")
    return None


def _to_api_messages(history: list[Message]) -> list[dict[str, str]]:
    """Map stored messages to Anthropic message dicts.

    Drops leading assistant turns (the missed-call greeting, a prior
    clarifying question) so the list starts with a user turn, as the API
    requires.
    """
    msgs: list[dict[str, str]] = [
        {
            "role": "user" if m.direction == MessageDirection.inbound else "assistant",
            "content": m.body,
        }
        for m in history
    ]
    while msgs and msgs[0]["role"] == "assistant":
        msgs.pop(0)
    return msgs


def _render_system(config: ClientConfig, version: str) -> str:
    """Render the intent system prompt for the given version."""
    template_src = PROMPT_VERSIONS.get(version)
    if template_src is None:
        logger.warning(
            "unknown intent prompt version — using default",
            extra={"version": version},
        )
        template_src = PROMPT_VERSIONS[DEFAULT_INTENT_VERSION]
    return jinja2.Template(template_src).render(
        business_name=config.business_name or "our team",
        business_category=config.category,
        service_area=_service_area(config),
    )


def _service_area(config: ClientConfig) -> str:
    zips = config.service_area_zips[:3]
    return ", ".join(zips) if zips else "the local area"
