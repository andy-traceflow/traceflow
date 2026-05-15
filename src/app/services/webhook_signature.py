"""Vendor-neutral webhook signature verification helpers.

Three patterns supported:
  - base64 HMAC-SHA256 of raw body (Shopify-style)
  - hex HMAC-SHA256 of raw body
  - timestamped HMAC-SHA256 of `{ts}.{body}` with replay protection
    (Stripe-style — many providers use this shape)

Verifiers are pure functions over (secret, body, signature). The
`verify_signature_for_request()` dispatcher resolves the right
verifier and the right secret based on the route and the client's
config.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from uuid import UUID

import httpx
from fastapi import Request

from app.config import get_settings

logger = logging.getLogger(__name__)


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
# Request-level dispatcher — used by tenant_resolver middleware
# ---------------------------------------------------------------------------

async def verify_signature_for_request(request: Request, client_id: UUID) -> None:
    """Verify the inbound webhook against the per-client signing secret.

    Raises PermissionError on failure (mapped to 401 by the middleware).
    No-op if signature verification is disabled in development.

    Provider is inferred from the URL path. Body is read once and cached
    on the request so downstream handlers don't pay the I/O cost again.
    """
    path = request.url.path
    body = await _read_and_cache_body(request)
    secret = await _load_signing_secret(client_id, _infer_integration(path))

    settings = get_settings()
    if not secret:
        # In dev we let unsigned webhooks through to make local testing
        # ergonomic. In prod we fail closed.
        if settings.is_production:
            raise PermissionError("no signing secret configured for tenant")
        logger.warning(
            "skipping signature check — no secret configured (dev only)",
            extra={"path": path, "client_id": str(client_id)},
        )
        return

    if path.startswith("/webhooks/shopify/"):
        sig = request.headers.get("X-Shopify-Hmac-Sha256", "")
        if not verify_hmac_sha256_base64(secret, body, sig):
            raise PermissionError("shopify hmac mismatch")

    elif path.startswith("/webhooks/twilio/"):
        # Twilio's signature is X-Twilio-Signature: base64(HMAC-SHA1(authToken, url + sorted_params)).
        # See: https://www.twilio.com/docs/usage/security
        # Verification is more involved than HMAC-of-body; we leave it as a
        # follow-up in app/services/twilio_signature.py. For now, fail closed
        # in production until that lands.
        if settings.is_production:
            raise PermissionError("twilio signature verification not yet implemented")
        return

    elif path.startswith("/webhooks/generic/"):
        # Generic webhook: per-config; the handler itself validates.
        # Middleware can't know the algorithm without loading the row.
        return

    elif path.startswith("/webhooks/crm/"):
        sig_header = request.headers.get("X-Signature") or request.headers.get("X-Webhook-Signature", "")
        if not verify_hmac_sha256_hex(secret, body, sig_header):
            raise PermissionError("crm hmac mismatch")

    # Unknown webhook path — let it through; route will 404 if invalid.


async def _read_and_cache_body(request: Request) -> bytes:
    """Read the body once and cache it on request.state so handlers can re-read."""
    if not hasattr(request.state, "_cached_body"):
        request.state._cached_body = await request.body()
    return request.state._cached_body  # type: ignore[no-any-return]


def _infer_integration(path: str) -> str:
    if path.startswith("/webhooks/shopify/"):
        return "shopify"
    if path.startswith("/webhooks/twilio/"):
        return "twilio"
    if path.startswith("/webhooks/crm/"):
        return "crm"
    if path.startswith("/webhooks/generic/"):
        return "generic"
    return ""


async def _load_signing_secret(client_id: UUID, integration: str) -> str | None:
    """Look up the per-client signing secret for this integration.

    Uses the Supabase REST API with the service role key (RLS-bypassing
    admin lookup) to avoid setting tenant context before signature
    verification — chicken-and-egg.
    """
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_key:
        return None

    url = f"{settings.supabase_url}/rest/v1/client_configs"
    headers = {
        "apikey": settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Accept": "application/json",
    }
    params = {
        "client_id": f"eq.{client_id}",
        "select": "webhook_signing_secrets",
        "limit": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            rows = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.exception("failed to load signing secret", exc_info=e)
        return None

    if not rows:
        return None
    secrets = rows[0].get("webhook_signing_secrets") or {}
    return secrets.get(integration)
