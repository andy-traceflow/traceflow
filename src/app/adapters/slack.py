"""Slack destination — one Block Kit message per record.

The cheapest destination and the natural smoke test for a fresh fork:
if a test order shows up in the channel, the whole pipeline works.
Append-only: `_key` is ignored. `_line_items` render as a bullet list.

Env: SLACK_BOT_TOKEN (xoxb-…, scope `chat:write`), SLACK_CHANNEL (id or
#name; the bot must be a member).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.adapters.base import display_name, require_env, split_record
from app.pipeline import PermanentDeliveryError, TransientDeliveryError

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api"
DEFAULT_TIMEOUT = 30.0
MAX_FIELDS_PER_SECTION = 10  # Block Kit limit
MAX_LINE_ITEMS = 20
MAX_VALUE_LENGTH = 200

_TRANSIENT_ERRORS = frozenset({"ratelimited", "rate_limited", "internal_error", "service_unavailable"})


class SlackAdapter:
    name = "slack"

    def __init__(
        self,
        *,
        bot_token: str | None = None,
        channel: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.bot_token = bot_token or require_env("SLACK_BOT_TOKEN")
        self.channel = channel or require_env("SLACK_CHANNEL")
        self._client = httpx.AsyncClient(
            base_url=SLACK_API_BASE,
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
            headers={
                "Authorization": f"Bearer {self.bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )

    # ------------------------------------------------------------------
    # Destination interface
    # ------------------------------------------------------------------

    async def upsert_record(self, record: dict[str, Any]) -> str:
        data = await self._call(
            "chat.postMessage",
            {
                "channel": self.channel,
                "text": display_name(record, fallback="New event"),  # notification fallback
                "blocks": self.build_blocks(record),
            },
        )
        return str(data["ts"])

    async def health_check(self) -> bool:
        try:
            await self._call("auth.test", {})
            return True
        except Exception as e:
            logger.warning("slack health_check failed", exc_info=e)
            return False

    # ------------------------------------------------------------------
    # Block Kit
    # ------------------------------------------------------------------

    @classmethod
    def build_blocks(cls, record: dict[str, Any]) -> list[dict[str, Any]]:
        fields, meta = split_record(record)
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": display_name(record, "New event")[:150]},
            }
        ]

        items = [
            {"type": "mrkdwn", "text": f"*{name}*\n{cls._fmt(value)}"}
            for name, value in fields.items()
            if value is not None and value != ""
        ]
        for i in range(0, len(items), MAX_FIELDS_PER_SECTION):
            blocks.append({"type": "section", "fields": items[i : i + MAX_FIELDS_PER_SECTION]})

        line_items = meta.get("_line_items") or []
        if line_items:
            lines = [f"• {cls._line_item(li)}" for li in line_items[:MAX_LINE_ITEMS]]
            if len(line_items) > MAX_LINE_ITEMS:
                lines.append(f"… and {len(line_items) - MAX_LINE_ITEMS} more")
            blocks.append({"type": "divider"})
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})
        return blocks

    @staticmethod
    def _fmt(value: Any) -> str:
        if isinstance(value, list | tuple):
            text = ", ".join(str(v) for v in value)
        else:
            text = str(value)
        return text if len(text) <= MAX_VALUE_LENGTH else text[: MAX_VALUE_LENGTH - 1] + "…"

    @classmethod
    def _line_item(cls, li: dict[str, Any]) -> str:
        li_fields, _ = split_record(li)
        name = display_name(li, "item")
        rest = ", ".join(
            f"{k}: {cls._fmt(v)}"
            for k, v in li_fields.items()
            if v is not None and v != "" and str(v).strip() != name
        )
        return f"*{name}*" + (f" — {rest}" if rest else "")

    # ------------------------------------------------------------------
    # Slack Web API
    # ------------------------------------------------------------------

    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Slack reports failures inside a 200 body as {"ok": false, "error": ...}."""
        resp = await self._client.post(f"/{method}", json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            error = str(data.get("error") or "unknown_error")
            if error in _TRANSIENT_ERRORS:
                raise TransientDeliveryError(f"slack {method}: {error}")
            raise PermanentDeliveryError(f"slack {method}: {error}")
        return data
