"""Webhook signature verification entrypoint.

Thin re-export of services/webhook_signature.py, kept for symmetry with
the rest of the middleware tree. Verification is NOT middleware — it is
the `verify_shopify_signature` FastAPI dependency declared explicitly on
the webhook route. Most code should import from services directly.
"""

from app.services.webhook_signature import (  # re-export
    SHOPIFY_HMAC_HEADER,
    parse_signature_header,
    verify_hmac_sha256_base64,
    verify_hmac_sha256_hex,
    verify_shopify_signature,
    verify_timestamped_signature,
)

__all__ = [
    "SHOPIFY_HMAC_HEADER",
    "parse_signature_header",
    "verify_hmac_sha256_base64",
    "verify_hmac_sha256_hex",
    "verify_shopify_signature",
    "verify_timestamped_signature",
]
