"""Unit tests for the tenant_resolver middleware's path extraction.

The integration behavior (ContextVar threading → DB connection setting
→ RLS enforcement) is covered end-to-end by tests/test_tenant_isolation.py.
This file covers the pure-function piece: given a URL path, do we
extract the right client_id (or correctly return None)?
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.middleware.tenant_resolver import _extract_client_id_from_path

_VALID = UUID("aabbccdd-1122-3344-5566-77889900ffee")  # contains a–f so case-sensitivity tests mean something


# ---------------------------------------------------------------------------
# Happy paths — every webhook URL shape we publish to integrations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        f"/webhooks/twilio/sms/{_VALID}",
        f"/webhooks/twilio/voice/{_VALID}",
        f"/webhooks/twilio/sms/{_VALID}/",  # trailing slash tolerated
        f"/webhooks/shopify/{_VALID}",
        f"/webhooks/shopify/{_VALID}/",
        f"/webhooks/crm/ghl/{_VALID}",
        f"/webhooks/crm/monday/{_VALID}",
        f"/webhooks/crm/hubspot/{_VALID}/",
        f"/webhooks/generic/{_VALID}/custom-endpoint",
        f"/webhooks/generic/{_VALID}/missed-call-callback/",
    ],
)
def test_extracts_client_id_from_valid_webhook_paths(path: str) -> None:
    assert _extract_client_id_from_path(path) == _VALID


# ---------------------------------------------------------------------------
# Negative paths — anything else returns None so the middleware lets the
# request pass without tenant context (health checks, docs, root, etc.)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/health",
        "/docs",
        "/openapi.json",
        "/api/kb",
        "/api/calculator/quote",
        "/webhooks/",                                          # missing channel + id
        "/webhooks/twilio/sms",                                 # missing id
        f"/webhooks/twilio/{_VALID}",                           # missing channel segment
        f"/webhooks/shopify",                                   # missing id
        f"/webhooks/crm/{_VALID}",                              # missing provider segment
        f"/webhooks/generic/{_VALID}",                          # missing endpoint segment
        "/webhooks/twilio/sms/not-a-uuid",                      # malformed id
        "/webhooks/shopify/abc123",                             # malformed id
        f"/WEBHOOKS/twilio/sms/{_VALID}",                       # case-sensitive: uppercase fails
    ],
)
def test_returns_none_for_paths_without_tenant_id(path: str) -> None:
    assert _extract_client_id_from_path(path) is None


# ---------------------------------------------------------------------------
# Edge cases worth pinning down so future regex changes don't regress them
# ---------------------------------------------------------------------------

def test_partial_uuid_is_rejected() -> None:
    """Truncated UUIDs must not match — the regex requires exactly 36 hex+dash chars."""
    short = "11111111-2222-3333-4444-55555555555"  # one char short
    assert _extract_client_id_from_path(f"/webhooks/shopify/{short}") is None


def test_uppercase_uuid_is_rejected() -> None:
    """UUIDs in webhook URLs must be lowercase to match the [0-9a-f-] character class."""
    upper = str(_VALID).upper()
    assert _extract_client_id_from_path(f"/webhooks/shopify/{upper}") is None


def test_extra_path_segments_after_id_do_not_break_match() -> None:
    """Generic webhook URLs carry an arbitrary endpoint segment after the id."""
    assert (
        _extract_client_id_from_path(f"/webhooks/generic/{_VALID}/some-action")
        == _VALID
    )
