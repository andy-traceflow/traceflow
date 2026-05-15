"""Webhook signature verification entrypoint.

Thin wrapper that dispatches to provider-specific verifiers in
services/webhook_signature.py. Kept here for symmetry with the rest
of the middleware tree; most code should import from services.
"""

from app.services.webhook_signature import (  # re-export
    parse_signature_header,
    verify_hmac_sha256_base64,
    verify_hmac_sha256_hex,
    verify_signature_for_request,
    verify_timestamped_signature,
)

__all__ = [
    "parse_signature_header",
    "verify_hmac_sha256_base64",
    "verify_hmac_sha256_hex",
    "verify_signature_for_request",
    "verify_timestamped_signature",
]
