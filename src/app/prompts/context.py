"""Unified prompt context — one object, four blocks, all three prompts.

Prompt context used to be assembled ad hoc in greeting, intent, and qualifier,
each doing its own lookups. This builds it once:

  <business>  — name, category, service types, area, hours, tone, terminology.
                Stable and large, so it is emitted FIRST as its own system
                content block with cache_control ephemeral. The Sonnet qualifier
                resends it every turn; caching it is a real cost line.
  <caller>    — who this is: name, type, call_count, days since last contact,
                known_facts, rolling summary, last_intent. OMITTED for a
                first-time caller (no contact, or a brand-new unknown one).
  <state>     — captured fields, still-needed fields, turn N of max. Only when
                a qualification schema + lead are in hand (the qualifier).
  <time>      — local time in the client's timezone + is-business-hours. Finally
                wires the call_time / business_hours_status the greeting dropped.

`system_blocks()` returns the Anthropic system as content blocks so the business
block can carry cache_control; `as_text()` gives the flattened string for the
cheap single-call prompts (and tests).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.client_config import ClientConfig
from app.models.contact import Contact, ContactType
from app.models.qualification import QualificationSchema
from app.services.qualification import applicable_fields, askable_fields, field_value

_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass(frozen=True)
class PromptContext:
    business: str
    time: str
    caller: str | None = None
    state: str | None = None

    def system_blocks(self, instructions: str = "") -> list[dict[str, Any]]:
        """Anthropic system content blocks. Business first (cached ephemeral),
        then caller/state/time and any prompt-specific instructions."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": self.business,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        rest = "\n".join(
            block for block in (self.caller, self.state, self.time, instructions) if block
        )
        if rest:
            blocks.append({"type": "text", "text": rest})
        return blocks

    def as_text(self) -> str:
        return "\n".join(
            block for block in (self.business, self.caller, self.state, self.time) if block
        )


def build_prompt_context(
    config: ClientConfig,
    contact: Contact | None,
    *,
    timezone: str = "America/Los_Angeles",
    schema: QualificationSchema | None = None,
    state: dict[str, Any] | None = None,
    turn_count: int = 0,
    now: datetime | None = None,
) -> PromptContext:
    """Assemble the prompt context. `contact` None (or a brand-new unknown one)
    omits the caller block; a schema + captured state adds the state block (the
    caller passes the state it already merged, so it isn't computed twice)."""
    now = now or datetime.now(UTC)
    return PromptContext(
        business=_business_block(config),
        caller=_caller_block(contact, now),
        state=_state_block(schema, state, turn_count) if schema is not None and state is not None
        else None,
        time=_time_block(config, timezone, now),
    )


def _business_block(config: ClientConfig) -> str:
    lines = [
        f"Business: {config.business_name or 'our team'}",
        f"Category: {config.category}",
    ]
    if config.service_types:
        lines.append(f"Services: {', '.join(config.service_types)}")
    zips = config.service_area_zips[:5]
    lines.append(f"Service area: {', '.join(zips) if zips else 'the local area'}")
    lines.append(f"Tone: {config.tone_of_voice}")
    hours = _hours_summary(config)
    if hours:
        lines.append(f"Business hours: {hours}")
    overrides = config.brand.get("terminology_overrides") or {}
    if overrides:
        pairs = ", ".join(f"say '{v}' not '{k}'" for k, v in overrides.items())
        lines.append(f"Terminology: {pairs}")
    body = "\n".join(lines)
    return f"<business>\n{body}\n</business>"


def _caller_block(contact: Contact | None, now: datetime) -> str | None:
    # First-time caller: no contact, or a never-seen unknown one with no history.
    if contact is None:
        return None
    if (
        contact.contact_type == ContactType.unknown
        and contact.call_count <= 1
        and contact.lead_count == 0
        and not contact.summary
        and not contact.known_facts
    ):
        return None

    lines: list[str] = []
    if contact.name:
        lines.append(f"Name: {contact.name}")
    lines.append(f"Type: {contact.contact_type.value}")
    if contact.call_count:
        lines.append(f"Calls so far: {contact.call_count}")
    days = (now - contact.last_seen_at).days
    if days > 0:
        lines.append(f"Days since last contact: {days}")
    if contact.known_facts:
        facts = ", ".join(f"{k}={v}" for k, v in contact.known_facts.items())
        lines.append(f"Known: {facts}")
    if contact.summary:
        lines.append(f"History: {contact.summary}")
    if contact.last_intent:
        lines.append(f"Last intent: {contact.last_intent}")
    body = "\n".join(lines)
    return f"<caller>\n{body}\n</caller>"


def _state_block(
    schema: QualificationSchema,
    state: dict[str, Any],
    turn_count: int,
) -> str | None:
    captured = [
        f"{f.label}: {field_value(f, state)}"
        for f in applicable_fields(schema, state)
        if field_value(f, state) not in (None, "")
    ]
    still_needed = [f.label for f in askable_fields(schema, state)]
    lines = [
        "Captured: " + (", ".join(captured) if captured else "(nothing yet)"),
        "Still needed: " + (", ".join(still_needed) if still_needed else "(complete)"),
        f"Turn {turn_count + 1} of at most {schema.max_turns}",
    ]
    body = "\n".join(lines)
    return f"<state>\n{body}\n</state>"


def _time_block(config: ClientConfig, timezone: str, now: datetime) -> str:
    local = _to_local(timezone, now)
    open_now = _is_business_hours(config, local)
    status = "open" if open_now else "closed"
    return (
        f"<time>\nLocal time: {local:%A %I:%M %p}\n"
        f"Business hours status: {status}\n</time>"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_local(timezone: str, now: datetime) -> datetime:
    try:
        return now.astimezone(ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError):
        return now


def _hours_summary(config: ClientConfig) -> str:
    hours = config.business_hours or {}
    parts = []
    for day in _WEEKDAYS:
        window = hours.get(day)
        if window and window.get("open") and window.get("close"):
            parts.append(f"{day.capitalize()} {window['open']}-{window['close']}")
    return "; ".join(parts)


def _is_business_hours(config: ClientConfig, local: datetime) -> bool:
    hours = config.business_hours or {}
    window = hours.get(_WEEKDAYS[local.weekday()])
    if not window or not window.get("open") or not window.get("close"):
        return False
    current = local.strftime("%H:%M")
    return bool(window["open"] <= current <= window["close"])
