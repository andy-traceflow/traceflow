"""Rolling conversation summary (Slice 4).

Called ONCE, on a lead's terminal transition — not per turn. A cheap Haiku call
distills the conversation into a durable one-liner on the contact and extracts
any person-scoped facts worth keeping, so the NEXT time this caller reaches out
they arrive with context instead of a blank slate.

summarize_conversation returns (summary, person_facts) or None (no key / failure
/ nothing to summarize). persist_summary writes it: contacts.summary, merges the
person facts into known_facts, and promotes an untyped contact to prospect.
Failure is non-fatal — the caller logs and moves on.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.models.client_config import ClientConfig
from app.models.contact import Contact, ContactType, ContactTypeSource
from app.models.lead import Lead
from app.models.message import Message, MessageDirection
from app.models.qualification import QualificationSchema
from app.prompts.context import build_prompt_context
from app.services.ai import get_anthropic_client
from app.services.contacts import merge_person_facts, set_contact_type

logger = logging.getLogger(__name__)

SUMMARIZE_MODEL = "claude-haiku-4-5"
SUMMARIZE_MAX_TOKENS = 300

SUMMARIZE_SYSTEM = """\
The conversation below just ended. Produce a durable record for this caller so a future conversation can pick up with context.

Call the record_summary tool with:
- summary: ONE concise sentence (under 200 chars) capturing who they are and what they wanted — e.g. "Homeowner in 89101 pricing a ~40 sqft quartz kitchen countertop, hoping to start this month." Write it so it is still useful months later.
- person_facts: durable facts about the PERSON (not this specific project) worth remembering next time — their name, address/zip, preferred contact time, property type. Omit anything project-specific or that you did not actually learn.

Do not write a reply to the customer.
"""

RECORD_TOOL: dict[str, Any] = {
    "name": "record_summary",
    "description": "Record the durable summary and person-scoped facts for this caller.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One durable sentence about the caller."},
            "person_facts": {
                "type": "object",
                "description": "Durable person-scoped facts (name, zip, property_type, etc.).",
            },
        },
        "required": ["summary"],
    },
}


async def summarize_conversation(
    config: ClientConfig,
    contact: Contact,
    lead: Lead,
    history: list[Message],
) -> tuple[str, dict[str, Any]] | None:
    """Summarize a finished conversation. Returns (summary, person_facts), or
    None when the API key is unset, there's nothing to summarize, or the call
    fails."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        logger.warning("anthropic_api_key not set — skipping summary")
        return None

    api_messages = _to_api_messages(history)
    if not api_messages:
        return None

    ctx = build_prompt_context(config, contact)
    try:
        response = await get_anthropic_client().messages.create(
            model=SUMMARIZE_MODEL,
            max_tokens=SUMMARIZE_MAX_TOKENS,
            system=ctx.system_blocks(SUMMARIZE_SYSTEM),
            messages=api_messages,
            tools=[RECORD_TOOL],
            tool_choice={"type": "tool", "name": "record_summary"},
        )
    except Exception as e:
        logger.warning("conversation summary failed", exc_info=e)
        return None

    for block in response.content:
        if block.type == "tool_use" and block.name == "record_summary":
            summary = str(block.input.get("summary") or "").strip()
            person_facts = block.input.get("person_facts") or {}
            if not summary:
                return None
            return summary, dict(person_facts)
    return None


async def persist_summary(
    conn: Any,
    contact: Contact,
    summary: str,
    person_facts: dict[str, Any],
    schema: QualificationSchema,
) -> None:
    """Write the summary onto the contact, merge person facts into known_facts,
    and promote an untyped contact to prospect."""
    await conn.execute("UPDATE contacts SET summary = $2 WHERE id = $1", contact.id, summary)
    if person_facts:
        await merge_person_facts(conn, contact.id, person_facts, schema)
    if contact.contact_type == ContactType.unknown:
        await set_contact_type(
            conn, contact.id, ContactType.prospect, ContactTypeSource.inferred, "post_conversation"
        )


def _to_api_messages(history: list[Message]) -> list[dict[str, str]]:
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
