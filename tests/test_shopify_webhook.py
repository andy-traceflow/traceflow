"""Handler contract: verify → persist → 200. Nothing else."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models.event import EventStatus
from app.services.events import get_event_store
from tests.fakes import InMemoryEventStore

SECRET = "shpss_test_secret_do_not_use"
HMAC_HEADER = "X-Shopify-Hmac-Sha256"
ORDER = {
    "id": 820982911946154508,
    "email": "jon@example.com",
    "total_price": "254.98",
    "shipping_address": {"first_name": "Jon", "last_name": "Snow", "company": "Night's Watch"},
}
BODY = json.dumps(ORDER).encode()


def _sign(body: bytes, secret: str = SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def _headers(body: bytes, **extra: str) -> dict[str, str]:
    h = {
        HMAC_HEADER: _sign(body),
        "X-Shopify-Webhook-Id": "b54557e4-8b2d-4a63-9a58-1234567890ab",
        "X-Shopify-Topic": "orders/create",
        "X-Shopify-Shop-Domain": "example.myshopify.com",
    }
    h.update(extra)
    return h


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused")
    monkeypatch.setenv("DESTINATION", "slack")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def store() -> InMemoryEventStore:
    return InMemoryEventStore()


@pytest.fixture
def client(store: InMemoryEventStore):
    app.dependency_overrides[get_event_store] = lambda: store
    try:
        yield TestClient(app)  # no lifespan: the route touches no real DB
    finally:
        app.dependency_overrides.pop(get_event_store, None)


URL = "/webhooks/shopify/orders/create"


def test_persists_one_received_row_with_headers_captured(client, store):
    r = client.post(URL, content=BODY, headers=_headers(BODY))
    assert r.status_code == 200

    (event,) = store.events.values()
    assert event.source == "shopify"
    assert event.topic == "orders/create"
    assert event.webhook_id == "b54557e4-8b2d-4a63-9a58-1234567890ab"
    assert event.shop_domain == "example.myshopify.com"
    assert event.payload == ORDER
    assert event.status == EventStatus.RECEIVED
    assert event.attempts == 0
    assert event.delivered_at is None


def test_same_webhook_id_twice_creates_exactly_one_row(client, store):
    for _ in range(3):
        r = client.post(URL, content=BODY, headers=_headers(BODY))
        assert r.status_code == 200
    assert len(store.events) == 1


def test_header_topic_wins_over_path_segment(client, store):
    r = client.post(
        "/webhooks/shopify/whatever/path", content=BODY, headers=_headers(BODY)
    )
    assert r.status_code == 200
    (event,) = store.events.values()
    assert event.topic == "orders/create"


def test_path_topic_is_the_fallback_when_header_missing(client, store):
    headers = _headers(BODY)
    del headers["X-Shopify-Topic"]
    r = client.post("/webhooks/shopify/orders/updated", content=BODY, headers=headers)
    assert r.status_code == 200
    (event,) = store.events.values()
    assert event.topic == "orders/updated"


def test_missing_webhook_id_falls_back_to_body_hash_and_still_dedupes(client, store):
    headers = _headers(BODY)
    del headers["X-Shopify-Webhook-Id"]
    for _ in range(2):
        assert client.post(URL, content=BODY, headers=headers).status_code == 200
    (event,) = store.events.values()
    assert event.webhook_id == "sha256:" + hashlib.sha256(BODY).hexdigest()


def test_invalid_json_is_acknowledged_and_not_stored(client, store):
    junk = b"{not json"
    r = client.post(URL, content=junk, headers=_headers(junk))
    assert r.status_code == 200
    assert store.events == {}


def test_store_failure_is_not_acknowledged(client, store):
    """Durable first: if the row cannot be written, do not 200 — let Shopify retry."""
    store.fail_insert_with = RuntimeError("DB pool not initialized")
    r = client.post(URL, content=BODY, headers=_headers(BODY))
    assert r.status_code == 503
    assert store.events == {}


def test_bad_signature_never_reaches_the_store(client, store):
    r = client.post(URL, content=BODY, headers=_headers(BODY, **{HMAC_HEADER: _sign(BODY, "x")}))
    assert r.status_code == 401
    assert store.events == {}
