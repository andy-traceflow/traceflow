"""Spam-scoring tests — Twilio Lookup risk mapping and degradation.

The HTTP layer is mocked so the suite runs offline. The focus is the prime
directive: any missing-credential, transport, HTTP, or parse failure returns
None ("no signal, not spam") so a real lead is never dropped.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import pytest

from app.services import spam
from app.services.spam import SpamRisk, is_spam, score_spam_risk


def _client_returning(resp: Any) -> MagicMock:
    """Build a mock for httpx.AsyncClient that yields a client whose .get
    returns `resp` (or raises, if resp is an exception)."""
    client = AsyncMock()
    if isinstance(resp, Exception):
        client.get = AsyncMock(side_effect=resp)
    else:
        client.get = AsyncMock(return_value=resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


def _resp(status: int, body: Any) -> Mock:
    resp = Mock(status_code=status)
    if isinstance(body, Exception):
        resp.json = Mock(side_effect=body)
    else:
        resp.json = Mock(return_value=body)
    return resp


def _with_creds() -> Any:
    settings = Mock()
    settings.twilio_account_sid = "AC-test"
    settings.twilio_auth_token = "tok-test"
    return settings


# ---------------------------------------------------------------------------
# is_spam — threshold floors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("risk", "threshold", "expected"),
    [
        (SpamRisk.high, "moderate", True),       # default floor is high
        (SpamRisk.moderate, "moderate", False),  # moderate doesn't clear the high floor
        (SpamRisk.high, "permissive", True),
        (SpamRisk.moderate, "permissive", False),
        (SpamRisk.moderate, "strict", True),     # strict drops moderate too
        (SpamRisk.low, "strict", False),         # low is NEVER dropped
        (SpamRisk.high, "nonsense", True),       # unknown threshold → default floor (high)
    ],
)
def test_is_spam_threshold_floors(risk: SpamRisk, threshold: str, expected: bool):
    assert is_spam(risk, threshold) is expected


# ---------------------------------------------------------------------------
# line type → risk mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("line_type", "expected"),
    [
        ("nonFixedVoip", SpamRisk.high),
        ("tollFree", SpamRisk.moderate),
        ("premium", SpamRisk.moderate),
        ("mobile", SpamRisk.low),
        ("landline", SpamRisk.low),
        ("fixedVoip", SpamRisk.low),
        ("unknown", SpamRisk.low),
        (None, SpamRisk.low),
    ],
)
def test_risk_from_line_type(line_type: str | None, expected: SpamRisk):
    assert spam._risk_from_line_type(line_type) == expected


# ---------------------------------------------------------------------------
# score_spam_risk — happy path + degradation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_score_none_without_credentials():
    settings = Mock()
    settings.twilio_account_sid = ""
    settings.twilio_auth_token = ""
    with patch("app.services.spam.get_settings", return_value=settings):
        assert await score_spam_risk("+15551234567") is None


@pytest.mark.asyncio
async def test_score_high_for_non_fixed_voip():
    resp = _resp(200, {"line_type_intelligence": {"type": "nonFixedVoip"}})
    with (
        patch("app.services.spam.get_settings", return_value=_with_creds()),
        patch("app.services.spam.httpx.AsyncClient", new=_client_returning(resp)),
    ):
        assert await score_spam_risk("+15551234567") == SpamRisk.high


@pytest.mark.asyncio
async def test_score_low_for_mobile():
    resp = _resp(200, {"line_type_intelligence": {"type": "mobile"}})
    with (
        patch("app.services.spam.get_settings", return_value=_with_creds()),
        patch("app.services.spam.httpx.AsyncClient", new=_client_returning(resp)),
    ):
        assert await score_spam_risk("+15551234567") == SpamRisk.low


@pytest.mark.asyncio
async def test_score_low_when_field_missing():
    resp = _resp(200, {})  # add-on not enabled / field absent → degrade to low
    with (
        patch("app.services.spam.get_settings", return_value=_with_creds()),
        patch("app.services.spam.httpx.AsyncClient", new=_client_returning(resp)),
    ):
        assert await score_spam_risk("+15551234567") == SpamRisk.low


@pytest.mark.asyncio
async def test_score_none_on_http_error():
    resp = _resp(404, {"message": "not found"})
    with (
        patch("app.services.spam.get_settings", return_value=_with_creds()),
        patch("app.services.spam.httpx.AsyncClient", new=_client_returning(resp)),
    ):
        assert await score_spam_risk("+15551234567") is None


@pytest.mark.asyncio
async def test_score_none_on_non_json():
    resp = _resp(200, ValueError("not json"))
    with (
        patch("app.services.spam.get_settings", return_value=_with_creds()),
        patch("app.services.spam.httpx.AsyncClient", new=_client_returning(resp)),
    ):
        assert await score_spam_risk("+15551234567") is None


@pytest.mark.asyncio
async def test_score_none_on_transport_error():
    with (
        patch("app.services.spam.get_settings", return_value=_with_creds()),
        patch(
            "app.services.spam.httpx.AsyncClient",
            new=_client_returning(httpx.ConnectError("boom")),
        ),
    ):
        assert await score_spam_risk("+15551234567") is None
