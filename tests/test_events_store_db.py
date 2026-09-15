"""PostgresEventStore against a real database. Skips without TEST_DB_URL.

Requires migrations/001_create_events.sql applied (CI does this).
These are the guarantees the in-memory fake only imitates: the unique
constraint, FOR UPDATE SKIP LOCKED, and the lease expiry.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.config import get_settings
from app.db import close_pool, get_connection, init_pool
from app.models.event import Event, EventStatus
from app.pipeline import Pipeline, passthrough
from app.services.events import PostgresEventStore
from app.worker import run_once


@pytest.fixture
async def store(db_url: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SUPABASE_DB_URL", db_url)
    get_settings.cache_clear()
    await init_pool()
    try:
        async with get_connection() as conn:
            await conn.execute("TRUNCATE events")
        yield PostgresEventStore()
    finally:
        await close_pool()
        get_settings.cache_clear()


async def _insert(store: PostgresEventStore, webhook_id: str, payload: dict[str, Any] | None = None):
    return await store.insert(
        source="shopify",
        topic="orders/create",
        webhook_id=webhook_id,
        shop_domain="example.myshopify.com",
        payload=payload if payload is not None else {"id": 1, "line_items": [{"sku": "A"}]},
    )


async def test_same_webhook_id_creates_exactly_one_row(store):
    first = await _insert(store, "wh-1")
    second = await _insert(store, "wh-1")
    assert first is not None
    assert second is None
    async with get_connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM events") == 1


async def test_jsonb_round_trips_as_dict(store):
    await _insert(store, "wh-json")
    (event,) = await store.claim(limit=1, lease_seconds=60)
    assert event.payload == {"id": 1, "line_items": [{"sku": "A"}]}


async def test_concurrent_claims_never_overlap(store):
    ids = {await _insert(store, f"wh-{i}") for i in range(20)}

    a, b = await asyncio.gather(
        store.claim(limit=20, lease_seconds=60),
        store.claim(limit=20, lease_seconds=60),
    )
    claimed_a = {e.id for e in a}
    claimed_b = {e.id for e in b}

    assert claimed_a.isdisjoint(claimed_b)
    assert claimed_a | claimed_b == ids
    assert await store.claim(limit=20, lease_seconds=60) == []


async def test_failed_delivery_stays_retryable_and_redelivery_is_absorbed(store):
    event_id = await _insert(store, "wh-fail")
    (event,) = await store.claim(limit=1, lease_seconds=60)

    await store.schedule_retry(
        event.id,
        attempts=1,
        error="HTTP 503",
        next_retry_at=datetime.now(UTC) - timedelta(seconds=1),  # already due
    )
    assert await _insert(store, "wh-fail") is None  # redelivery absorbed

    (again,) = await store.claim(limit=1, lease_seconds=60)
    assert again.id == event_id
    assert again.attempts == 1
    assert again.status == EventStatus.PROCESSING
    assert again.delivered_at is None


async def test_expired_lease_is_reclaimed(store):
    await _insert(store, "wh-lease")
    (event,) = await store.claim(limit=1, lease_seconds=0)
    assert await store.claim(limit=1, lease_seconds=60) != []  # 0s lease already expired

    # A live lease is respected.
    (again,) = await store.claim(limit=1, lease_seconds=0)
    assert again.id == event.id


async def test_terminal_states_are_never_claimed(store):
    dead_id = await _insert(store, "wh-dead")
    ok_id = await _insert(store, "wh-ok")
    for e in await store.claim(limit=2, lease_seconds=60):
        if e.id == dead_id:
            await store.mark_dead(e.id, attempts=5, error="gave up")
        else:
            await store.mark_delivered(e.id, attempts=1, external_id="ext-1")
    assert await store.claim(limit=10, lease_seconds=60) == []
    async with get_connection() as conn:
        rows = await conn.fetch("SELECT id, status, delivered_at FROM events ORDER BY status")
    by_id = {r["id"]: r for r in rows}
    assert by_id[dead_id]["status"] == "dead"
    assert by_id[ok_id]["status"] == "delivered"
    assert by_id[ok_id]["delivered_at"] is not None


async def test_operational_reads_and_replay(store):
    dead_id = await _insert(store, "wh-dead")
    ok_id = await _insert(store, "wh-ok")
    pending_id = await _insert(store, "wh-pending")
    for e in await store.claim(limit=2, lease_seconds=60):
        if e.id == dead_id:
            await store.mark_dead(e.id, attempts=5, error="gave up")
        else:
            await store.mark_delivered(e.id, attempts=1, external_id="ext-1")

    assert await store.count_by_status() == {"received": 1, "processing": 0, "delivered": 1, "dead": 1}
    assert await store.last_delivered_at() is not None

    dead_list = await store.list_events(status=EventStatus.DEAD, limit=10)
    assert [e.id for e in dead_list] == [dead_id]
    everything = await store.list_events(status=None, limit=10)
    assert {e.id for e in everything} == {dead_id, ok_id, pending_id}
    assert everything[0].received_at >= everything[-1].received_at  # newest first

    assert (await store.get(dead_id)).last_error == "gave up"
    assert await store.get(uuid4()) is None

    assert await store.replay(ok_id) is None  # only dead events replay
    replayed = await store.replay(dead_id)
    assert replayed is not None
    assert replayed.status == EventStatus.RECEIVED
    assert replayed.attempts == 0
    assert replayed.next_retry_at is None
    claimed_ids = {e.id for e in await store.claim(limit=10, lease_seconds=60)}
    assert dead_id in claimed_ids


async def test_two_workers_running_simultaneously_never_deliver_the_same_event_twice(store):
    """End to end through run_once(): two workers, one table, 30 events, 30 deliveries."""
    n = 30
    for i in range(n):
        await _insert(store, f"wh-{i}", payload={"n": i})

    delivered: list[int] = []

    async def deliver(record: dict[str, Any]) -> str:
        await asyncio.sleep(0.002)  # yield so the two workers interleave
        delivered.append(record["n"])
        return f"ext-{record['n']}"

    async def no_alert(event: Event, error: str) -> None:
        pass

    pipeline = Pipeline(passthrough, deliver)
    a, b = await asyncio.gather(
        run_once(store, pipeline, batch_size=4, alert=no_alert),
        run_once(store, pipeline, batch_size=4, alert=no_alert),
    )

    assert a + b == n
    assert a > 0 and b > 0  # both workers actually took part
    assert sorted(delivered) == list(range(n))  # each event exactly once
    assert (await store.count_by_status())["delivered"] == n
    assert await store.claim(limit=10, lease_seconds=60) == []
