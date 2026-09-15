"""Vendor-neutral webhook signature verification.

Three pure verifiers (no IO — easy to unit-test):
  - base64 HMAC-SHA256 of raw body (Shopify-style)
  - hex HMAC-SHA256 of raw body
  - timestamped HMAC-SHA256 of `{ts}.{body}` with replay protection
    (Stripe-style — many providers use this shape)

Plus one request-level FastAPI dependency, `verify_shopify_signature`,
declared explicitly on the webhook route. It fails closed: a missing
secret, a missing header, or a mismatch all reject the request. The
secret's presence is additionally enforced at startup
(`app.config.require_startup_settings`) so a misconfigured deploy refuses
to boot instead of silently accepting unsigned traffic on a public URL.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

from fastapi import HTTPException, Request

from app.config import get_settings

logger = logging.getLogger(__name__)

SHOPIFY_HMAC_HEADER = "X-Shopify-Hmac-Sha256"


# ---------------------------------------------------------------------------
# Pure verifiers — no IO. Easy to unit-test.
# ---------------------------------------------------------------------------

def verify_hmac_sha256_base64(secret: str, body: bytes, signature_b64: str) -> bool:
    """Shopify-style: base64(HMAC-SHA256(secret, body))."""
    if not secret or not signature_b64:
        return False
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("utf-8")
    return hmac.compare_digest(expected, signature_b64)


def verify_hmac_sha256_hex(secret: str, body: bytes, signature_hex: str) -> bool:
    """hex(HMAC-SHA256(secret, body)). Common for generic webhook providers."""
    if not secret or not signature_hex:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_hex.lower())


def parse_signature_header(header: str) -> dict[str, str]:
    """Parse `t=...,s=...` style headers into a dict.

    Returns empty dict on malformed input — callers should fail closed.
    """
    parts: dict[str, str] = {}
    if not header:
        return parts
    for chunk in header.split(","):
        chunk = chunk.strip()
        if "=" in chunk:
            k, _, v = chunk.partition("=")
            parts[k.strip()] = v.strip()
    return parts


def verify_timestamped_signature(
    secret: str,
    body: bytes,
    signature_header: str,
    max_age_seconds: int = 300,
    *,
    now: float | None = None,
) -> bool:
    """Verify `t=<ts>,s=<hex_sig>` header where sig = HMAC-SHA256(secret, "{ts}.{body}").

    Rejects timestamps older than max_age_seconds for replay protection.
    `now` is injectable for tests.
    """
    parsed = parse_signature_header(signature_header)
    ts_str = parsed.get("t")
    provided = parsed.get("s")
    if not ts_str or not provided:
        return False
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - ts) > max_age_seconds:
        return False
    signed = ts_str.encode("utf-8") + b"." + body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


# ---------------------------------------------------------------------------
# Request-level dependency — declared explicitly on the webhook route.
# ---------------------------------------------------------------------------

async def verify_shopify_signature(request: Request) -> bytes:
    """FastAPI dependency: verify the Shopify HMAC over the raw request body.

    Returns the raw body bytes (also cached on `request.state._cached_body`)
    so the handler never re-reads the stream.

    Fail-closed contract — there is no environment in which this is skipped:
      - secret not configured → 500. A deployment bug, not a client error;
        Shopify retries non-2xx for ~48h, so fixing the env var recovers
        the events that arrived in the meantime.
      - header missing/empty  → 401
      - HMAC mismatch         → 401
    Never logs the secret or the presented signature.
    """
    body = await _read_and_cache_body(request)

    secret = get_settings().shopify_webhook_secret
    if not secret:
        logger.error(
            "SHOPIFY_WEBHOOK_SECRET not configured — rejecting webhook",
            extra={"path": request.url.path},
        )
        raise HTTPException(status_code=500, detail="webhook signing secret not configured")

    signature = request.headers.get(SHOPIFY_HMAC_HEADER, "")
    if not signature:
        logger.warning(
            "shopify webhook rejected: missing %s header",
            SHOPIFY_HMAC_HEADER,
            extra={"path": request.url.path},
        )
        raise HTTPException(status_code=401, detail="missing signature")

    if not verify_hmac_sha256_base64(secret, body, signature):
        logger.warning(
            "shopify webhook rejected: hmac mismatch",
            extra={"path": request.url.path},
        )
        raise HTTPException(status_code=401, detail="invalid signature")

    return body


async def _read_and_cache_body(request: Request) -> bytes:
    """Read the body once and cache it on request.state so handlers can re-read."""
    if not hasattr(request.state, "_cached_body"):
        request.state._cached_body = await request.body()
    return request.state._cached_body  # type: ignore[no-any-return]
