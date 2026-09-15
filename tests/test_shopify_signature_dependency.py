"""Route-level tests for the Shopify HMAC dependency and the startup gate.

The pure verifiers are covered in test_webhook_signature.py. These exercise
the fail-closed contract at the two boundaries that matter in a deployment:
the webhook route (per request) and the lifespan handler (at boot).
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings, require_startup_settings
from app.main import app
from app.services.events import get_event_store
from tests.fakes import InMemoryEventStore

SECRET = "shpss_test_secret_do_not_use"
BODY = b'{"id": 820982911946154508, "email": "jon@example.com", "total_price": "254.98"}'
URL = "/webhooks/shopify/orders/create"
HEADER = "X-Shopify-Hmac-Sha256"


def _sign(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


@pytest.fixture(autouse=True)
def _configured_secret(monkeypatch: pytest.MonkeyPatch):
    """Every test starts with the secret configured; individual tests remove it."""
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused")
    monkeypatch.setenv("DESTINATION", "slack")
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    get_settings.cache_clear()
    # Accepted requests go on to persist; give them an in-memory store.
    app.dependency_overrides[get_event_store] = InMemoryEventStore
    yield
    app.dependency_overrides.pop(get_event_store, None)
    get_settings.cache_clear()


def _unset_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHOPIFY_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    # Not a context manager on purpose: lifespan (DB pool) is not started.
    # The route under test touches no database.
    return TestClient(app)


# ---------------------------------------------------------------------------
# Per-request verification
# ---------------------------------------------------------------------------


def test_valid_signature_is_accepted(client: TestClient) -> None:
    r = client.post(URL, content=BODY, headers={HEADER: _sign(SECRET, BODY)})
    assert r.status_code == 200


def test_tampered_body_is_rejected(client: TestClient) -> None:
    sig = _sign(SECRET, BODY)
    r = client.post(URL, content=BODY + b" ", headers={HEADER: sig})
    assert r.status_code == 401


def test_wrong_secret_is_rejected(client: TestClient) -> None:
    r = client.post(URL, content=BODY, headers={HEADER: _sign("not-the-secret", BODY)})
    assert r.status_code == 401


def test_missing_header_is_rejected(client: TestClient) -> None:
    r = client.post(URL, content=BODY)
    assert r.status_code == 401


def test_empty_header_is_rejected(client: TestClient) -> None:
    r = client.post(URL, content=BODY, headers={HEADER: ""})
    assert r.status_code == 401


def test_missing_secret_fails_closed_even_for_a_correctly_signed_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No configured secret → nothing to verify against → reject. Never skip."""
    sig = _sign(SECRET, BODY)  # signed with what *would* be the right secret
    _unset_secret(monkeypatch)
    r = client.post(URL, content=BODY, headers={HEADER: sig})
    assert r.status_code == 500


def test_missing_secret_fails_closed_regardless_of_environment(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old code skipped verification when ENVIRONMENT != production. Gone."""
    for env in ("development", "test", "staging", "", "prod"):
        monkeypatch.setenv("ENVIRONMENT", env)
        _unset_secret(monkeypatch)
        r = client.post(URL, content=BODY, headers={HEADER: _sign(SECRET, BODY)})
        assert r.status_code == 500, f"ENVIRONMENT={env!r} let an unverified webhook through"


# ---------------------------------------------------------------------------
# Signature check runs before the JSON-parse shortcut
# ---------------------------------------------------------------------------


def test_unparseable_json_with_valid_signature_is_acknowledged(client: TestClient) -> None:
    """200 so Shopify stops retrying a body that will never parse."""
    junk = b"this is not json"
    r = client.post(URL, content=junk, headers={HEADER: _sign(SECRET, junk)})
    assert r.status_code == 200


def test_unparseable_json_with_bad_signature_is_still_rejected(client: TestClient) -> None:
    """The 200-on-bad-JSON shortcut must never be reachable without a valid HMAC."""
    junk = b"this is not json"
    r = client.post(URL, content=junk, headers={HEADER: _sign("wrong", junk)})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Startup gate
# ---------------------------------------------------------------------------


def test_require_startup_settings_passes_when_configured() -> None:
    require_startup_settings()  # must not raise


def test_require_startup_settings_names_the_missing_var(monkeypatch: pytest.MonkeyPatch) -> None:
    _unset_secret(monkeypatch)
    with pytest.raises(RuntimeError, match="SHOPIFY_WEBHOOK_SECRET"):
        require_startup_settings()


def test_lifespan_refuses_to_start_without_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app must not come up at all — not 'come up and 500 every webhook'."""
    _unset_secret(monkeypatch)
    with pytest.raises(RuntimeError, match="SHOPIFY_WEBHOOK_SECRET"):
        with TestClient(app):
            pass
