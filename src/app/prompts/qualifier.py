"""Lead qualification prompt.

A multi-turn SMS conversation that collects qualification fields from an
inbound lead. Each inbound SMS is one turn: the full conversation so far
is replayed to a capable model (Sonnet), which replies with one short
SMS and records extracted fields via the update_lead tool.

qualifier_turn returns None when the API key is unset or the call fails
— the caller flags the lead for human review rather than dropping it.
See the prompt-engineering skill for the wider prompt taxonomy.
"""

from __future__ import annotations

import logging
from typing import Any

import jinja2

from app.config import get_settings
from app.models.client_config import ClientConfig
from app.models.message import Message, MessageDirection
from app.services.ai import get_anthropic_client

logger = logging.getLogger(__name__)

QUALIFIER_MODEL = "claude-sonnet-4-6"
QUALIFIER_MAX_TOKENS = 300
DEFAULT_QUALIFIER_VERSION = "v1"

QUALIFIER_SYSTEM_V1 = """\
You are a friendly SMS intake assistant for {{business_name}}, a {{business_category}} business serving {{service_area}}. The customer recently called, missed a connection, and is now replying by text.

Your goal: collect enough information to qualify this lead. Extract these fields as they come up:
- service_type (the work they want{% if service_types %}; usually one of: {{service_types | join(", ")}}{% endif %})
- sqft (project size in square feet, if applicable)
- budget_range (one of: <5k, 5k-15k, 15k-50k, 50k+ — infer it from project size and service type; do not ask for budget directly)
- timeframe (one of: asap, this_month, this_quarter, researching)
- address or service area

Rules:
- ONE question per message. Never more.
- Keep every message under 160 characters (SMS).
- Sound {{tone_of_voice}}.
- If they mention a competitor, do not badmouth them.
- If they ask for pricing, redirect: "I'll have someone reach out with specifics."
- After 4-5 exchanges, once you have the key fields, wrap up gracefully and tell them someone will follow up.
- If they are clearly not a fit (wrong area, wrong service, residential vs commercial mismatch), thank them politely and wrap up.
- Do not sign off with the business name every message.
{% if vip_keywords %}- High-value signals to watch for: {{vip_keywords | join(", ")}}.
{% endif %}
Use the update_lead tool to record fields as you learn them. When the conversation is complete, also use it to set qualification_status: "qualified" for a real, in-area lead with enough detail; "needs_review" if it is unclear; "spam" if it is not a genuine lead.

Always reply with a short SMS to the customer, in addition to any tool call.
"""

PROMPT_VERSIONS: dict[str, str] = {
    "v1": QUALIFIER_SYSTEM_V1,
}

UPDATE_LEAD_TOOL: dict[str, Any] = {
    "name": "update_lead",
    "description": (
        "Record qualification details learned from the conversation. Call this "
        "whenever the customer reveals new information. Only include fields you "
        "have actually learned — omit the rest."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "contact_name": {"type": "string", "description": "The customer's name"},
            "service_type": {"type": "string", "description": "The service the customer wants"},
            "sqft": {"type": "number", "description": "Project size in square feet"},
            "budget_range": {
                "type": "string",
                "enum": ["<5k", "5k-15k", "15k-50k", "50k+"],
            },
            "timeframe": {
                "type": "string",
                "enum": ["asap", "this_month", "this_quarter", "researching"],
            },
            "address": {"type": "string", "description": "Project address or service area"},
            "qualification_status": {
                "type": "string",
                "enum": ["qualified", "needs_review", "spam"],
                "description": (
                    "Set ONLY when the conversation is complete: 'qualified' for a "
                    "real in-area lead with enough detail, 'needs_review' if unclear, "
                    "'spam' if not a genuine lead."
                ),
            },
        },
    },
}


async def qualifier_turn(
    config: ClientConfig,
    history: list[Message],
) -> tuple[str, dict[str, Any], str] | None:
    """Run one turn of the qualification conversation.

    `history` is the full SMS exchange so far, oldest first, ending with
    the customer's latest inbound message. Returns
    (reply_text, extracted_fields, prompt_version), or None when the API
    key is unset or the call fails.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        logger.warning("anthropic_api_key not set — skipping qualifier turn")
        return None

    api_messages = _to_api_messages(history)
    if not api_messages:
        logger.warning("qualifier turn: no inbound message to respond to")
        return None

    version = config.prompt_versions.get("qualifier", DEFAULT_QUALIFIER_VERSION)
    system = _render_system(config, version)

    try:
        response = await get_anthropic_client().messages.create(
            model=QUALIFIER_MODEL,
            max_tokens=QUALIFIER_MAX_TOKENS,
            system=system,
            messages=api_messages,
            tools=[UPDATE_LEAD_TOOL],
        )
    except Exception as e:
        logger.warning("qualifier turn failed", exc_info=e)
        return None

    reply_text = ""
    extracted: dict[str, Any] = {}
    for block in response.content:
        if block.type == "text":
            reply_text += block.text
        elif block.type == "tool_use" and block.name == "update_lead":
            extracted.update(block.input)

    return reply_text.strip(), extracted, version


def _to_api_messages(history: list[Message]) -> list[dict[str, str]]:
    """Map stored messages to Anthropic message dicts.

    Drops leading assistant turns (e.g. the missed-call greeting) so the
    list starts with a user turn, which the API requires.
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
    """Render the qualifier system prompt for the given version."""
    template_src = PROMPT_VERSIONS.get(version)
    if template_src is None:
        logger.warning(
            "unknown qualifier prompt version — using default",
            extra={"version": version},
        )
        template_src = PROMPT_VERSIONS[DEFAULT_QUALIFIER_VERSION]
    return jinja2.Template(template_src).render(
        business_name=config.business_name or "our team",
        business_category=config.category,
        service_area=_service_area(config),
        service_types=config.service_types,
        tone_of_voice=config.tone_of_voice,
        vip_keywords=config.vip_keywords,
    )


def _service_area(config: ClientConfig) -> str:
    zips = config.service_area_zips[:3]
    return ", ".join(zips) if zips else "the local area"
