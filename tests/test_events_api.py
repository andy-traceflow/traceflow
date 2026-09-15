"""/events triage + replay behind the ADMIN_TOKEN bearer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models.event import Event, EventStatus
from app.services.events import get_event_store
from tests.fakes import InMemoryEventStore

TOKEN = "admin-token-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused")
    monkeypatch.setenv("DESTINATION", "slack")
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
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
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_event_store, None)


def _seed(store: InMemoryEventStore, status: EventStatus, *, age_s: int = 0, attempts: int = 0) -> UUID:
    e = Event(
        id=uuid4(), source="shopify", topic="orders/create", webhook_id=str(uuid4()),
        payload={"id": 1}, status=status, attempts=attempts,
        last_error="HTTP 422" if status == EventStatus.DEAD else None,
        received_at=datetime.now(UTC) - timedelta(seconds=age_s),
    )
    store.events[e.id] = e
    return e.id


# ---------------------------------------------------------------------------
# Auth — fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}],
    ids=["missing", "wrong", "not-bearer", "empty"],
)
def test_rejects_without_valid_token(client, store, headers):
    dead = _seed(store, EventStatus.DEAD)
    assert client.get("/events", headers=headers).status_code == 401
    assert client.get(f"/events/{dead}", headers=headers).status_code == 401
    assert client.post(f"/events/{dead}/replay", headers=headers).status_code == 401
    assert store.row(dead).status == EventStatus.DEAD  # nothing happened


def test_rejects_everything_when_no_token_configured(client, store, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN")
    get_settings.cache_clear()
    assert client.get("/events", headers=AUTH).status_code == 401


# ---------------------------------------------------------------------------
# List / get
# ---------------------------------------------------------------------------


def test_list_filters_by_status_newest_first(client, store):
    old_dead = _seed(store, EventStatus.DEAD, age_s=100)
    new_dead = _seed(store, EventStatus.DEAD, age_s=1)
    _seed(store, EventStatus.DELIVERED)

    r = client.get("/events?status=dead", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "dead"
    assert body["count"] == 2
    assert [e["id"] for e in body["events"]] == [str(new_dead), str(old_dead)]
    assert body["events"][0]["last_error"] == "HTTP 422"
    assert body["events"][0]["payload"] == {"id": 1}


def test_list_without_status_returns_all_with_limit(client, store):
    for _ in range(5):
        _seed(store, EventStatus.DELIVERED)
    body = client.get("/events?limit=3", headers=AUTH).json()
    assert body["status"] is None
    assert body["count"] == 3


def test_invalid_status_is_422(client):
    assert client.get("/events?status=bogus", headers=AUTH).status_code == 422
    assert client.get("/events?limit=0", headers=AUTH).status_code == 422
    assert client.get("/events?limit=999", headers=AUTH).status_code == 422


def test_get_one(client, store):
    dead = _seed(store, EventStatus.DEAD)
    r = client.get(f"/events/{dead}", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["id"] == str(dead)
    assert client.get(f"/events/{uuid4()}", headers=AUTH).status_code == 404
    assert client.get("/events/not-a-uuid", headers=AUTH).status_code == 422


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def test_replay_resets_dead_to_received_with_fresh_budget(client, store):
    dead = _seed(store, EventStatus.DEAD, attempts=5)
    r = client.post(f"/events/{dead}/replay", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "received"
    assert body["attempts"] == 0
    assert body["next_retry_at"] is None
    assert body["last_error"] == "HTTP 422"  # history kept until the next attempt overwrites it

    row = store.row(dead)
    assert row.status == EventStatus.RECEIVED
    assert row.attempts == 0


async def test_replayed_event_is_claimable_by_the_worker(client, store):
    dead = _seed(store, EventStatus.DEAD, attempts=5)
    client.post(f"/events/{dead}/replay", headers=AUTH)
    (claimed,) = await store.claim(limit=10, lease_seconds=60)
    assert claimed.id == dead


@pytest.mark.parametrize("status", [EventStatus.RECEIVED, EventStatus.PROCESSING, EventStatus.DELIVERED])
def test_replay_only_dead(client, store, status):
    event_id = _seed(store, status)
    r = client.post(f"/events/{event_id}/replay", headers=AUTH)
    assert r.status_code == 409
    assert status.value in r.json()["detail"]
    assert store.row(event_id).status == status


def test_replay_unknown_is_404(client):
    assert client.post(f"/events/{uuid4()}/replay", headers=AUTH).status_code == 404
