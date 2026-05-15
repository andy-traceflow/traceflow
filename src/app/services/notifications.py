"""Transactional email notifications via Resend.

Per-client recipient lists live in client_configs (notification_emails,
owner_alert_emails). Templates are minimal HTML; the heavy lifting is
plain content so the same renderer can later target SMS or push
without rewrite.

Failure to send is logged but does not propagate — the leads pipeline
must not block on email vendor outages.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.models.client_config import ClientConfig

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


async def send_email(
    *,
    to: list[str],
    subject: str,
    html: str,
    from_email: str | None = None,
) -> bool:
    """Send a transactional email. Returns True on success.

    Silently no-ops if RESEND_API_KEY is unset (dev mode) — emits a warning.
    """
    settings = get_settings()
    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY not set — skipping email", extra={"subject": subject})
        return False
    if not to:
        logger.warning("send_email called with empty recipient list", extra={"subject": subject})
        return False

    payload: dict[str, Any] = {
        "from": from_email or settings.notify_from_email,
        "to": to,
        "subject": subject,
        "html": html,
    }
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(RESEND_API_URL, headers=headers, json=payload)
            resp.raise_for_status()
        logger.info("email sent", extra={"subject": subject, "to_count": len(to)})
        return True
    except httpx.HTTPError as e:
        logger.exception("email send failed", exc_info=e)
        return False


async def notify_lead_success(
    config: ClientConfig,
    *,
    lead_summary: str,
    source_system: str,
    details: dict[str, Any] | None = None,
) -> bool:
    """Notify the operations team that a lead was processed end-to-end."""
    if not config.notification_emails:
        return False

    body = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 600px;">
        <h2 style="color: #2e7d32;">Lead processed</h2>
        <p><strong>Source:</strong> {source_system}</p>
        <p><strong>Summary:</strong> {lead_summary}</p>
        {_render_details(details)}
    </div>
    """
    return await send_email(
        to=config.notification_emails,
        subject=f"Lead processed — {source_system}",
        html=body,
    )


async def notify_lead_failure(
    config: ClientConfig,
    *,
    source_system: str,
    error: str,
    context: str = "",
) -> bool:
    """Notify the operations team that a lead pipeline failed."""
    if not config.notification_emails:
        return False

    context_block = (
        f"<p><strong>Context:</strong> {context}</p>" if context else ""
    )
    body = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 600px;">
        <h2 style="color: #c62828;">Lead pipeline failure</h2>
        <p><strong>Source:</strong> {source_system}</p>
        {context_block}
        <pre style="background: #f5f5f5; padding: 12px; border-radius: 4px;">{error}</pre>
    </div>
    """
    return await send_email(
        to=config.notification_emails,
        subject=f"Lead pipeline FAILED — {source_system}",
        html=body,
    )


async def notify_owner_vip(
    config: ClientConfig,
    *,
    lead_summary: str,
    reason: str,
) -> bool:
    """High-priority owner alert: VIP keyword match, big-budget tip-off, etc."""
    if not config.owner_alert_emails:
        return False
    body = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 600px;">
        <h2>VIP lead — needs attention</h2>
        <p><strong>Reason:</strong> {reason}</p>
        <p>{lead_summary}</p>
    </div>
    """
    return await send_email(
        to=config.owner_alert_emails,
        subject="VIP lead",
        html=body,
    )


def _render_details(details: dict[str, Any] | None) -> str:
    if not details:
        return ""
    rows = "".join(
        f"<tr><td style='padding:4px 12px 4px 0; font-weight:600;'>{k}</td>"
        f"<td style='padding:4px 0;'>{v}</td></tr>"
        for k, v in details.items()
    )
    return f"<table style='margin-top:8px;'>{rows}</table>"
