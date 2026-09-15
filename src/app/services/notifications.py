"""Dead-letter alerts → Slack incoming webhook (ALERT_WEBHOOK_URL).

The worker calls `send_alert(event, error)` exactly once when an event
becomes `dead`. The ERROR log line is always written; the Slack POST
happens only when ALERT_WEBHOOK_URL is set. Any failure here is logged
and swallowed by the worker — an alert sink outage must never take the
worker down.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.models.event import Event

logger = logging.getLogger(__name__)

ALERT_TIMEOUT_SECONDS = 10.0
MAX_ERROR_CHARS = 1500


def build_alert(event: Event, error: str, *, base_url: str) -> dict[str, Any]:
    """Slack incoming-webhook payload (Block Kit + plain-text fallback)."""
    base = base_url.rstrip("/")
    error_text = error if len(error) <= MAX_ERROR_CHARS else error[: MAX_ERROR_CHARS - 1] + "…"
    return {
        "text": f":rotating_light: Event dead-lettered — {event.source}/{event.topic} {event.webhook_id}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Event dead-lettered — needs attention"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Event*\n`{event.id}`"},
                    {"type": "mrkdwn", "text": f"*Topic*\n{event.source}/{event.topic}"},
                    {"type": "mrkdwn", "text": f"*Webhook id*\n`{event.webhook_id}`"},
                    {"type": "mrkdwn", "text": f"*Attempts*\n{event.attempts}"},
                    {"type": "mrkdwn", "text": f"*Received*\n{event.received_at.isoformat(timespec='seconds')}"},
                    {"type": "mrkdwn", "text": f"*Shop*\n{event.shop_domain or '—'}"},
                ],
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Last error*\n```{error_text}```"}},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"Triage: `GET {base}/events?status=dead` · "
                            f"Replay after fixing: `POST {base}/events/{event.id}/replay` "
                            "(Authorization: Bearer ADMIN_TOKEN)"
                        ),
                    }
                ],
            },
        ],
    }


async def send_alert(event: Event, error: str, *, client: httpx.AsyncClient | None = None) -> None:
    """Worker alert sink. Logs always; POSTs to ALERT_WEBHOOK_URL when configured."""
    settings = get_settings()
    logger.error(
        "event dead-lettered",
        extra={
            "event_id": str(event.id),
            "webhook_id": event.webhook_id,
            "topic": event.topic,
            "status": "dead",
            "attempts": event.attempts,
            "error": error,
        },
    )
    url = settings.alert_webhook_url
    if not url:
        logger.warning(
            "ALERT_WEBHOOK_URL not set — dead-letter alert was not delivered anywhere",
            extra={"event_id": str(event.id)},
        )
        return

    payload = build_alert(event, error, base_url=settings.base_url)
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=ALERT_TIMEOUT_SECONDS)
    try:
        resp = await http.post(url, json=payload)
        resp.raise_for_status()
        logger.info("dead-letter alert sent", extra={"event_id": str(event.id)})
    finally:
        if own_client:
            await http.aclose()
