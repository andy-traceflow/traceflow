"""Missed-call greeting prompt.

The first SMS sent after a missed call: a Jinja2 template filled from
client_configs and sent to a cheap, fast model (Haiku). See the
prompt-engineering skill for the wider prompt taxonomy.

generate_greeting returns None when the API key is unset or the call
fails — the caller falls back to a static template so the lead always
gets a text.
"""

from __future__ import annotations

import logging

import jinja2

from app.config import get_settings
from app.models.client_config import ClientConfig
from app.services.ai import get_anthropic_client

logger = logging.getLogger(__name__)

GREETING_MODEL = "claude-haiku-4-5"
GREETING_MAX_TOKENS = 200
DEFAULT_GREETING_VERSION = "v1"

GREETING_TEMPLATE_V1 = """\
You are writing an SMS on behalf of {{business_name}}, a {{business_category}} business serving {{service_area}}.

Tone: {{tone_of_voice}}

Task: write a short SMS (under 160 characters) that:
1. Apologizes for missing the caller's phone call
2. Identifies the business by name
3. Asks what the caller needs help with

Do not use emojis unless the tone is "casual". Do not promise specific response times. Do not give pricing or quotes.

Respond with ONLY the SMS body — no quotes, no commentary, no sign-off.
"""

PROMPT_VERSIONS: dict[str, str] = {
    "v1": GREETING_TEMPLATE_V1,
}


async def generate_greeting(config: ClientConfig) -> tuple[str, str] | None:
    """Generate the missed-call greeting SMS via the Anthropic API.

    Returns (sms_text, version) on success, or None when generation is
    not possible (no API key) or fails — the caller is expected to fall
    back to a static template.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        logger.warning("anthropic_api_key not set — skipping AI greeting")
        return None

    version = config.prompt_versions.get("greeting", DEFAULT_GREETING_VERSION)
    prompt = _render_prompt(config, version)

    try:
        response = await get_anthropic_client().messages.create(
            model=GREETING_MODEL,
            max_tokens=GREETING_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.warning("AI greeting generation failed", exc_info=e)
        return None

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        logger.warning("AI greeting returned empty text")
        return None
    return text, version


def _render_prompt(config: ClientConfig, version: str) -> str:
    """Render the greeting prompt template for the given version."""
    template_src = PROMPT_VERSIONS.get(version)
    if template_src is None:
        logger.warning(
            "unknown greeting prompt version — using default",
            extra={"version": version},
        )
        template_src = PROMPT_VERSIONS[DEFAULT_GREETING_VERSION]
    return jinja2.Template(template_src).render(
        business_name=config.business_name or "our team",
        business_category=config.category,
        service_area=_service_area(config),
        tone_of_voice=config.tone_of_voice,
    )


def _service_area(config: ClientConfig) -> str:
    zips = config.service_area_zips[:3]
    return ", ".join(zips) if zips else "the local area"
