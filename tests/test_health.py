"""GET /health: DB + destination checks, dead count, last delivery, caching, status codes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.registry import register_adapter, reset_registry
from app.config import get_settings
from app.main import app
from app.models.event import Event, EventStatus
from app.routers import health as health_module
from app.services.events import get_event_store
from tests.fakes import InMemoryEventStore


class FakeDestination:
    name = "fake"
    healthy = True
    calls = 0

    async def upsert_record(self, record: dict[str, Any]) -> str:
        return "x"

    async def health_check(self) -> bool:
        FakeDestination.calls += 1
        return FakeDestination.healthy


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused")
    monkeypatch.setenv("DESTINATION", "fake")
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    get_settings.cache_clear()
    reset_registry()
    register_adapter("fake", FakeDestination)
    FakeDestination.healthy = True
    FakeDestination.calls = 0
    health_module.reset_destination_cache()
    yield
    health_module.reset_destination_cache()
    reset_registry()
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


def _seed(store: InMemoryEventStore, status: EventStatus, delivered_at: datetime | None = None) -> None:
    e = Event(
        id=uuid4(), source="shopify", topic="orders/create", webhook_id=str(uuid4()),
        payload={}, status=status, received_at=datetime.now(UTC), delivered_at=delivered_at,
    )
    store.events[e.id] = e


def test_healthy(client, store):
    delivered = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    _seed(store, EventStatus.DELIVERED, delivered_at=delivered)
    _seed(store, EventStatus.DELIVERED, delivered_at=datetime(2026, 9, 14, tzinfo=UTC))
    _seed(store, EventStatus.RECEIVED)

    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"database": True, "destination": True}
    assert body["destination"] == "fake"
    assert body["events"] == {"received": 1, "processing": 0, "delivered": 2, "dead": 0}
    assert body["dead_events"] == 0
    assert body["last_delivered_at"] == delivered.isoformat()


def test_dead_events_are_counted(client, store):
    _seed(store, EventStatus.DEAD)
    _seed(store, EventStatus.DEAD)
    body = client.get("/health").json()
    assert body["dead_events"] == 2
    assert body["status"] == "ok"  # dead events are a triage signal, not an outage


def test_destination_down_is_degraded_but_200(client):
    FakeDestination.healthy = False
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert r.json()["checks"] == {"database": True, "destination": False}


def test_database_down_is_503(client, store):
    store.fail_reads_with = RuntimeError("DB pool not initialized")
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] is False
    assert body["dead_events"] is None


def test_destination_check_is_cached(client):
    client.get("/health")
    client.get("/health")
    client.get("/health")
    assert FakeDestination.calls == 1


def test_destination_exception_is_false_not_500(client):
    async def boom() -> bool:
        raise RuntimeError("network")

    FakeDestination.health_check = staticmethod(boom)  # type: ignore[assignment]
    try:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["checks"]["destination"] is False
    finally:
        del FakeDestination.health_check
