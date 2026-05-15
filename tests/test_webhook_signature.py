"""Pure-function webhook signature verifier tests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from app.services.webhook_signature import (
    parse_signature_header,
    verify_hmac_sha256_base64,
    verify_hmac_sha256_hex,
    verify_timestamped_signature,
)

SECRET = "test-secret-do-not-use-in-prod"
BODY = b'{"event": "test", "data": {"id": 123}}'


def _sign_b64(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def _sign_hex(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Base64 HMAC (Shopify-style)
# ---------------------------------------------------------------------------

def test_base64_signature_accepts_valid():
    sig = _sign_b64(SECRET, BODY)
    assert verify_hmac_sha256_base64(SECRET, BODY, sig) is True


def test_base64_signature_rejects_tampered_body():
    sig = _sign_b64(SECRET, BODY)
    tampered = BODY + b"x"
    assert verify_hmac_sha256_base64(SECRET, tampered, sig) is False


def test_base64_signature_rejects_wrong_secret():
    sig = _sign_b64(SECRET, BODY)
    assert verify_hmac_sha256_base64("wrong-secret", BODY, sig) is False


def test_base64_signature_rejects_empty_inputs():
    assert verify_hmac_sha256_base64("", BODY, "anything") is False
    assert verify_hmac_sha256_base64(SECRET, BODY, "") is False


# ---------------------------------------------------------------------------
# Hex HMAC
# ---------------------------------------------------------------------------

def test_hex_signature_accepts_valid():
    sig = _sign_hex(SECRET, BODY)
    assert verify_hmac_sha256_hex(SECRET, BODY, sig) is True


def test_hex_signature_is_case_insensitive():
    sig = _sign_hex(SECRET, BODY).upper()
    assert verify_hmac_sha256_hex(SECRET, BODY, sig) is True


def test_hex_signature_rejects_tampered_body():
    sig = _sign_hex(SECRET, BODY)
    assert verify_hmac_sha256_hex(SECRET, BODY + b"!", sig) is False


# ---------------------------------------------------------------------------
# Timestamped signatures with replay protection
# ---------------------------------------------------------------------------

def test_timestamped_signature_accepts_valid():
    ts = int(time.time())
    signed = f"{ts}".encode() + b"." + BODY
    sig = hmac.new(SECRET.encode(), signed, hashlib.sha256).hexdigest()
    header = f"t={ts},s={sig}"
    assert verify_timestamped_signature(SECRET, BODY, header) is True


def test_timestamped_signature_rejects_stale():
    """Replay protection: older than max_age should fail."""
    ts = int(time.time()) - 600  # 10 min ago, default max_age is 300
    signed = f"{ts}".encode() + b"." + BODY
    sig = hmac.new(SECRET.encode(), signed, hashlib.sha256).hexdigest()
    header = f"t={ts},s={sig}"
    assert verify_timestamped_signature(SECRET, BODY, header) is False


def test_timestamped_signature_rejects_malformed_header():
    assert verify_timestamped_signature(SECRET, BODY, "") is False
    assert verify_timestamped_signature(SECRET, BODY, "garbage") is False
    assert verify_timestamped_signature(SECRET, BODY, "t=not_a_number,s=abc") is False


def test_timestamped_signature_accepts_with_injectable_now():
    """Injectable clock keeps tests deterministic regardless of wall time."""
    frozen_now = 1_700_000_000.0
    ts = int(frozen_now)
    signed = f"{ts}".encode() + b"." + BODY
    sig = hmac.new(SECRET.encode(), signed, hashlib.sha256).hexdigest()
    header = f"t={ts},s={sig}"
    assert verify_timestamped_signature(SECRET, BODY, header, now=frozen_now) is True


# ---------------------------------------------------------------------------
# Header parser
# ---------------------------------------------------------------------------

def test_parse_signature_header_basic():
    assert parse_signature_header("t=123,s=abc") == {"t": "123", "s": "abc"}


def test_parse_signature_header_handles_whitespace():
    assert parse_signature_header(" t = 123 , s = abc ") == {"t": "123", "s": "abc"}


def test_parse_signature_header_empty():
    assert parse_signature_header("") == {}
