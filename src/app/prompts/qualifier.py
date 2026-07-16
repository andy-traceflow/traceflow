"""Lead qualification prompt (config-driven + unified context, Slices 3–4).

A multi-turn SMS conversation that collects the client's configured
qualification fields. The tool schema is GENERATED from the client's schema; the
model no longer decides when the conversation is done (services/qualification.py
owns termination).

v2 (default) assembles its system prompt from the unified prompt context: the
<business> block is emitted first with cache_control ephemeral (the Sonnet
qualifier resends the system every turn — caching it is a real cost line), then
<caller>/<state>/<time>, then the qualifier rules. v1 is kept as a plain-string
legacy prompt for pinned clients.

qualifier_turn returns None when the API key is unset or the call fails.
"""

from __future__ import annotations

import logging
from typing import Any

import jinja2

from app.config import get_settings
from app.models.client_config import ClientConfig
from app.models.contact import Contact
from app.models.message import Message, MessageDirection
from app.models.qualification import QualificationSchema
from app.prompts.context import build_prompt_context
from app.services.ai import get_anthropic_client

logger = logging.getLogger(__name__)

QUALIFIER_MODEL = "claude-sonnet-4-6"
QUALIFIER_MAX_TOKENS = 300
DEFAULT_QUALIFIER_VERSION = "v2"

# v1 — legacy plain-string prompt for pinned clients. It still tells the model to
# set qualification_status; the caller ignores that (code owns termination).
QUALIFIER_SYSTEM_V1 = """\
You are a friendly SMS intake assistant for {{business_name}}, a {{business_category}} business serving {{service_area}}. The customer recently called, missed a connection, and is now replying by text.

Collect enough information to qualify this lead. Rules: ONE question per message; keep every message under 160 characters; sound {{tone_of_voice}}; if they ask for pricing, say someone will follow up with specifics; after 4-5 exchanges wrap up gracefully. Use the update_lead tool to record fields as you learn them. Always reply with a short SMS.
"""

# v2 — rules only. The business context, caller history, captured/needed state,
# and local time all arrive as context blocks above this text.
QUALIFIER_INSTRUCTIONS_V2 = """\
You are the SMS intake assistant for the business described above. The caller recently missed a call and is replying by text. The blocks above say what you already know and what is still needed.

Rules:
- Ask about at most {{max_questions}} of the "Still needed" fields above per message — {{max_questions}} question, never more. If nothing is still needed, do NOT ask another question: thank them warmly and say someone will follow up shortly.
- Only ask for a field listed under "Still needed". Never re-ask something already known, and never invent your own questions.
- Keep every message under 160 characters (SMS). Match the tone above.
- Do not ask for budget directly — it is inferred. If they ask about pricing, tell them someone will follow up with specifics.
- If they mention a competitor, do not badmouth them.
- Record what you learn with the update_lead tool. Do NOT judge whether the lead qualifies — just collect and ask.

Always reply with a short SMS to the customer, in addition to any tool call.
"""

PROMPT_VERSIONS: dict[str, str] = {
    "v1": QUALIFIER_SYSTEM_V1,
    "v2": QUALIFIER_INSTRUCTIONS_V2,
}


def build_update_lead_tool(schema: QualificationSchema) -> dict[str, Any]:
    """Generate the Anthropic update_lead tool schema from the client's
    qualification schema. Properties keyed by field key; enums carry options.
    qualification_status is deliberately absent — code owns termination."""
    properties: dict[str, Any] = {}
    for field in schema.fields:
        prop: dict[str, Any] = {"description": field.ask or field.label}
        if field.type == "number":
            prop["type"] = "number"
        elif field.type == "boolean":
            prop["type"] = "boolean"
        elif field.type == "enum":
            prop["type"] = "string"
            prop["enum"] = field.options or []
        else:
            prop["type"] = "string"
        properties[field.key] = prop
    return {
        "name": "update_lead",
        "description": (
            "Record qualification details learned from the conversation. Call this "
            "whenever the customer reveals new information. Only include fields you "
            "have actually learned — omit the rest."
        ),
        "input_schema": {"type": "object", "properties": properties},
    }


async def qualifier_turn(
    config: ClientConfig,
    history: list[Message],
    schema: QualificationSchema,
    state: dict[str, Any],
    turn_count: int,
    *,
    contact: Contact | None = None,
    timezone: str = "America/Los_Angeles",
) -> tuple[str, dict[str, Any], str] | None:
    """Run one turn of qualification.

    Returns (reply_text, extracted_fields, prompt_version), or None when the API
    key is unset or the call fails. Extractions are keyed by field key — the
    caller splits them via services.qualification.split_extracted.
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
    system = _build_system(config, version, schema, state, turn_count, contact, timezone)
    tool = build_update_lead_tool(schema)

    try:
        response = await get_anthropic_client().messages.create(
            model=QUALIFIER_MODEL,
            max_tokens=QUALIFIER_MAX_TOKENS,
            system=system,
            messages=api_messages,
            tools=[tool],
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


def _build_system(
    config: ClientConfig,
    version: str,
    schema: QualificationSchema,
    state: dict[str, Any],
    turn_count: int,
    contact: Contact | None,
    timezone: str,
) -> str | list[dict[str, Any]]:
    """v2 → context content blocks (business cached). v1 → a plain string."""
    if version == "v1":
        return jinja2.Template(QUALIFIER_SYSTEM_V1).render(
            business_name=config.business_name or "our team",
            business_category=config.category,
            service_area=", ".join(config.service_area_zips[:3]) or "the local area",
            tone_of_voice=config.tone_of_voice,
        )
    instructions = jinja2.Template(QUALIFIER_INSTRUCTIONS_V2).render(
        max_questions=schema.max_questions_per_message
    )
    ctx = build_prompt_context(
        config, contact, timezone=timezone, schema=schema, state=state, turn_count=turn_count
    )
    return ctx.system_blocks(instructions)


def _to_api_messages(history: list[Message]) -> list[dict[str, str]]:
    """Map stored messages to Anthropic message dicts, dropping leading
    assistant turns so the list starts with a user turn (API requirement)."""
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
