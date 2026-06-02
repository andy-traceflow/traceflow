"""Outbound SMS via the Twilio REST API.

Mirrors services/notifications.py: a thin async sender that no-ops with a
warning when configuration is absent, logs failures, and never raises
into the leads pipeline.

The Twilio account is platform-level (one TraceFlow Twilio account); the
sending number is per-client (client_configs.twilio_number).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
DEFAULT_TIMEOUT = 10.0


async def send_sms(*, to: str, body: str, from_number: str) -> dict[str, Any] | None:
    """Send an SMS. Returns the Twilio message resource on success, None on failure.

    No-ops (returns None) with a warning when Twilio credentials or the
    sending number are unset — keeps local dev and not-yet-provisioned
    clients from blocking the leads pipeline.
    """
    settings = get_settings()
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        logger.warning("twilio credentials not set — skipping SMS", extra={"to": to})
        return None
    if not from_number:
        logger.warning("send_sms: no from_number — skipping", extra={"to": to})
        return None
    if not to:
        logger.warning("send_sms: no recipient — skipping")
        return None

    url = f"{TWILIO_API_BASE}/Accounts/{settings.twilio_account_sid}/Messages.json"
    form = {"To": to, "From": from_number, "Body": body}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                data=form,
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            )
    except httpx.HTTPError as e:
        logger.exception("sms send failed (transport)", exc_info=e)
        return None

    if resp.status_code >= 400:
        logger.error(
            "twilio sms error",
            extra={"to": to, "status": resp.status_code, "body": resp.text[:500]},
        )
        return None
    try:
        result: dict[str, Any] = resp.json()
    except ValueError:
        logger.error("twilio sms: non-JSON response", extra={"status": resp.status_code})
        return None
    logger.info("sms sent", extra={"to": to, "sid": result.get("sid")})
    return result
