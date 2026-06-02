"""Twilio request signature verification.

Twilio signs each webhook with the X-Twilio-Signature header:

    base64( HMAC-SHA1( auth_token, url + concat(sorted POST params) ) )

where each sorted param contributes `key + value` with no separators and
`url` is the exact public URL Twilio POSTed to.
See https://www.twilio.com/docs/usage/security
"""

from __future__ import annotations

import base64
import hashlib
import hmac


def compute_twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Compute the expected X-Twilio-Signature value for a request."""
    data = url
    for key in sorted(params):
        data += key + (params[key] or "")
    digest = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify_twilio_signature(
    auth_token: str,
    url: str,
    params: dict[str, str],
    signature: str,
) -> bool:
    """Return True if `signature` matches the expected Twilio signature.

    Fails closed on empty auth_token or empty signature.
    """
    if not auth_token or not signature:
        return False
    expected = compute_twilio_signature(auth_token, url, params)
    return hmac.compare_digest(expected, signature)
