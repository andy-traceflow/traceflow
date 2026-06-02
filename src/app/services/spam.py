"""Spam scoring for unknown missed-callers via Twilio Lookup v2.

Runs ONLY for callers the CRM didn't recognise (see classify_caller). A
disposable, non-fixed VOIP line is the classic robocaller signature; a
toll-free or premium origin is unusual for a real residential or commercial
customer. We map Twilio's line_type_intelligence to a coarse risk tier and
let the caller compare it against the client's configured threshold.

Prime directive holds here too: Lookup is best-effort. Missing credentials,
a timeout, an HTTP error, or an unparseable body all return None — "no
signal, treat as not spam" — so a real lead is NEVER dropped on an outage.
A later revision can layer Twilio's sms_pumping_risk add-on on top.

The Twilio account is platform-level (one TraceFlow account), so scoring
needs only the caller's number, not per-client credentials.
"""

from __future__ import annotations

import logging
from enum import IntEnum

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

LOOKUP_BASE = "https://lookups.twilio.com/v2/PhoneNumbers"
SPAM_LOOKUP_TIMEOUT = 2.0


class SpamRisk(IntEnum):
    """Coarse risk tier from a caller's line type. Ordered, so a client
    threshold can be expressed as a floor (see is_spam)."""

    low = 0
    moderate = 1
    high = 2


# Twilio line_type_intelligence `type` -> risk. Anything not listed (mobile,
# landline, fixedVoip, personal, unknown, missing) is low — a normal customer.
_HIGH_RISK_LINE_TYPES = frozenset({"nonFixedVoip"})
_MODERATE_RISK_LINE_TYPES = frozenset(
    {"tollFree", "premium", "sharedCost", "uan", "voicemail", "pager"}
)

# spam_risk_threshold -> lowest risk tier that counts as spam. `low` is never
# a floor: a low-risk number is never dropped, whatever the threshold. There
# is no tier below `high` to relax to, so "permissive" matches the default;
# "strict" opts into also dropping suspicious (moderate) line types.
_THRESHOLD_FLOOR: dict[str, SpamRisk] = {
    "permissive": SpamRisk.high,
    "moderate": SpamRisk.high,
    "strict": SpamRisk.moderate,
}
_DEFAULT_FLOOR = SpamRisk.high


def is_spam(risk: SpamRisk, threshold: str) -> bool:
    """True if `risk` meets or exceeds the floor for the client's threshold."""
    return risk >= _THRESHOLD_FLOOR.get(threshold, _DEFAULT_FLOOR)


def _risk_from_line_type(line_type: str | None) -> SpamRisk:
    if line_type in _HIGH_RISK_LINE_TYPES:
        return SpamRisk.high
    if line_type in _MODERATE_RISK_LINE_TYPES:
        return SpamRisk.moderate
    return SpamRisk.low


async def score_spam_risk(phone: str) -> SpamRisk | None:
    """Best-effort spam risk for an unknown caller. None on ANY failure —
    no credentials, transport error, HTTP error, or unparseable body — so
    the caller degrades toward potential_lead and never drops a real lead."""
    settings = get_settings()
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        logger.warning("twilio credentials not set — skipping spam lookup")
        return None

    url = f"{LOOKUP_BASE}/{phone}"
    try:
        async with httpx.AsyncClient(timeout=SPAM_LOOKUP_TIMEOUT) as client:
            resp = await client.get(
                url,
                params={"Fields": "line_type_intelligence"},
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            )
    except httpx.HTTPError as e:
        logger.warning("spam lookup failed (transport)", exc_info=e)
        return None

    if resp.status_code >= 400:
        logger.warning("twilio lookup error", extra={"status": resp.status_code})
        return None
    try:
        data = resp.json()
    except ValueError:
        logger.warning("twilio lookup: non-JSON response")
        return None

    line_type = (data.get("line_type_intelligence") or {}).get("type")
    risk = _risk_from_line_type(line_type)
    logger.info("spam lookup", extra={"line_type": line_type, "risk": risk.name})
    return risk
