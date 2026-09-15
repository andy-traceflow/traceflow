"""Worker policy and transitions, against the in-memory store. No IO."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest

from app.models.event import Event, EventStatus
from app.pipeline import (
    PermanentDeliveryError,
    Pipeline,
    TransformError,
    TransientDeliveryError,
    passthrough,
)
from app.worker import (
    BACKOFF_SCHEDULE_SECONDS,
    MAX_ATTEMPTS,
    PROCESSING_LEASE_SECONDS,
    backoff_seconds,
    classify_failure,
    process_one,
    run_once,
)
from tests.fakes import InMemoryEventStore


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Alerts:
    def __init__(self) -> None:
        self.fired: list[tuple[Event, str]] = []

    async def __call__(self, event: Event, error: str) -> None:
        self.fired.append((event, error))


def _fixed_backoff(attempt: int) -> float:
    return float(BACKOFF_SCHEDULE_SECONDS[attempt - 1])


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://dest.example/api")
    resp = httpx.Response(status, request=req, text=f"status {status}")
    return httpx.HTTPStatusError("boom", request=req, response=resp)


class Destination:
    """Scripted deliver(): raise the queued exceptions in order, then succeed."""

    def __init__(self, *failures: BaseException) -> None:
        self.failures = list(failures)
        self.delivered: list[dict[str, Any]] = []

    async def __call__(self, record: dict[str, Any]) -> str:
        if self.failures:
            raise self.failures.pop(0)
        self.delivered.append(record)
        return f"ext-{len(self.delivered)}"


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock: Clock) -> InMemoryEventStore:
    return InMemoryEventStore(now=clock)


@pytest.fixture
def alerts() -> Alerts:
    return Alerts()


async def _received(store: InMemoryEventStore, webhook_id: str = "wh-1") -> UUID:
    event_id = await store.insert(
        source="shopify",
        topic="orders/create",
        webhook_id=webhook_id,
        shop_domain="example.myshopify.com",
        payload={"id": 1, "total_price": "10.00"},
    )
    assert event_id is not None
    return event_id


async def _claim_one(store: InMemoryEventStore) -> Event:
    (event,) = await store.claim(limit=1, lease_seconds=PROCESSING_LEASE_SECONDS)
    return event


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


def test_backoff_schedule_is_1m_5m_15m_1h_6h():
    assert BACKOFF_SCHEDULE_SECONDS == (60, 300, 900, 3600, 21600)
    for attempt, base in enumerate(BACKOFF_SCHEDULE_SECONDS, start=1):
        assert backoff_seconds(attempt, rng=lambda: 0.5) == pytest.approx(base)


def test_backoff_jitter_is_bounded_to_plus_minus_20_percent():
    assert backoff_seconds(3, rng=lambda: 0.0) == pytest.approx(900 * 0.8)
    assert backoff_seconds(3, rng=lambda: 0.999999) == pytest.approx(900 * 1.2, rel=1e-5)


def test_backoff_clamps_beyond_schedule():
    assert backoff_seconds(99, rng=lambda: 0.5) == pytest.approx(21600)


def test_backoff_rejects_zero_attempt():
    with pytest.raises(ValueError):
        backoff_seconds(0)


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (PermanentDeliveryError("nope"), "permanent"),
        (TransientDeliveryError("later"), "transient"),
        (TransformError("bad mapping"), "permanent"),
        (ValueError("validation"), "permanent"),
        (_http_error(400), "permanent"),
        (_http_error(401), "permanent"),
        (_http_error(404), "permanent"),
        (_http_error(422), "permanent"),
        (_http_error(408), "transient"),
        (_http_error(429), "transient"),
        (_http_error(500), "transient"),
        (_http_error(502), "transient"),
        (_http_error(503), "transient"),
        (httpx.ConnectError("refused"), "transient"),
        (httpx.ReadTimeout("slow"), "transient"),
        (RuntimeError("unknown"), "transient"),
    ],
)
def test_classify_failure(exc, expected):
    assert classify_failure(exc) == expected


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


async def test_successful_delivery_marks_delivered(store, clock, alerts):
    event_id = await _received(store)
    dest = Destination()
    event = await _claim_one(store)
    assert event.status == EventStatus.PROCESSING

    status = await process_one(
        store, event, Pipeline(passthrough, dest), alert=alerts, now=clock.now
    )

    assert status == EventStatus.DELIVERED
    row = store.row(event_id)
    assert row.status == EventStatus.DELIVERED
    assert row.attempts == 1
    assert row.external_id == "ext-1"
    assert row.delivered_at == clock.now
    assert row.next_retry_at is None
    assert dest.delivered == [{"id": 1, "total_price": "10.00"}]
    assert alerts.fired == []


async def test_transient_failure_schedules_retry_and_is_not_delivered(store, clock, alerts):
    """The regression that motivated this migration."""
    event_id = await _received(store)
    dest = Destination(_http_error(503))
    event = await _claim_one(store)

    status = await process_one(
        store, event, Pipeline(passthrough, dest), alert=alerts,
        backoff=_fixed_backoff, now=clock.now,
    )

    assert status == EventStatus.RECEIVED
    row = store.row(event_id)
    assert row.status == EventStatus.RECEIVED
    assert row.delivered_at is None
    assert row.external_id is None
    assert row.attempts == 1
    assert "HTTP 503" in (row.last_error or "")
    assert row.next_retry_at == clock.now + timedelta(seconds=60)
    assert alerts.fired == []


async def test_failed_event_is_not_claimable_until_backoff_elapses(store, clock):
    await _received(store)
    dest = Destination(_http_error(503))
    event = await _claim_one(store)
    await process_one(store, event, Pipeline(passthrough, dest), backoff=_fixed_backoff, now=clock.now)

    assert await store.claim(limit=10, lease_seconds=1) == []
    clock.tick(59)
    assert await store.claim(limit=10, lease_seconds=1) == []
    clock.tick(1)
    (again,) = await store.claim(limit=10, lease_seconds=1)
    assert again.attempts == 1


async def test_redelivery_of_a_failed_event_is_absorbed_and_original_stays_retryable(
    store, clock
):
    """Old bug: is_duplicate() recorded the id before processing, so a crash
    mid-process made Shopify's retry a 'duplicate' and lost the event."""
    event_id = await _received(store, webhook_id="wh-dup")
    event = await _claim_one(store)
    await process_one(
        store, event, Pipeline(passthrough, Destination(_http_error(502))),
        backoff=_fixed_backoff, now=clock.now,
    )

    # Shopify redelivers the same webhook id.
    assert await store.insert(
        source="shopify", topic="orders/create", webhook_id="wh-dup",
        shop_domain=None, payload={"id": 1},
    ) is None
    assert len(store.events) == 1

    # ...and the original is still on the retry path, not lost.
    clock.tick(60)
    (again,) = await store.claim(limit=10, lease_seconds=1)
    assert again.id == event_id
    assert again.attempts == 1


async def test_permanent_4xx_goes_straight_to_dead_with_one_alert(store, clock, alerts):
    event_id = await _received(store)
    event = await _claim_one(store)

    status = await process_one(
        store, event, Pipeline(passthrough, Destination(_http_error(422))),
        alert=alerts, now=clock.now,
    )

    assert status == EventStatus.DEAD
    row = store.row(event_id)
    assert row.status == EventStatus.DEAD
    assert row.attempts == 1  # no retries burned
    assert "HTTP 422" in (row.last_error or "")
    assert len(alerts.fired) == 1
    assert alerts.fired[0][0].id == event_id
    assert await store.claim(limit=10, lease_seconds=1) == []


async def test_transform_error_is_permanent(store, clock, alerts):
    event_id = await _received(store)
    event = await _claim_one(store)

    def bad_transform(_: Event) -> dict[str, Any]:
        raise KeyError("Order ID")

    status = await process_one(
        store, event, Pipeline(bad_transform, Destination()), alert=alerts, now=clock.now
    )
    assert status == EventStatus.DEAD
    assert "KeyError" in (store.row(event_id).last_error or "")
    assert len(alerts.fired) == 1


async def test_five_transient_failures_produce_exactly_one_dead_row_and_one_alert(
    store, clock, alerts
):
    event_id = await _received(store)
    dest = Destination(*[_http_error(503)] * 10)  # never recovers
    pipeline = Pipeline(passthrough, dest)

    outcomes: list[EventStatus] = []
    for _ in range(MAX_ATTEMPTS):
        (event,) = await store.claim(limit=10, lease_seconds=PROCESSING_LEASE_SECONDS)
        outcomes.append(
            await process_one(store, event, pipeline, alert=alerts, backoff=_fixed_backoff, now=clock.now)
        )
        clock.tick(8 * 3600)  # beyond the largest backoff

    assert outcomes == [EventStatus.RECEIVED] * (MAX_ATTEMPTS - 1) + [EventStatus.DEAD]
    row = store.row(event_id)
    assert row.status == EventStatus.DEAD
    assert row.attempts == MAX_ATTEMPTS
    assert len(store.by_status(EventStatus.DEAD)) == 1
    assert len(alerts.fired) == 1
    assert await store.claim(limit=10, lease_seconds=1) == []  # dead is terminal


async def test_alert_failure_does_not_break_the_worker(store, clock):
    event_id = await _received(store)
    event = await _claim_one(store)

    async def exploding_alert(event: Event, error: str) -> None:
        raise RuntimeError("slack is down")

    status = await process_one(
        store, event, Pipeline(passthrough, Destination(PermanentDeliveryError("no"))),
        alert=exploding_alert, now=clock.now,
    )
    assert status == EventStatus.DEAD
    assert store.row(event_id).status == EventStatus.DEAD


async def test_expired_processing_lease_is_reclaimed(store, clock):
    """A worker that dies mid-event must not strand the row in `processing`."""
    await _received(store)
    (event,) = await store.claim(limit=1, lease_seconds=300)
    assert event.status == EventStatus.PROCESSING

    assert await store.claim(limit=1, lease_seconds=300) == []  # lease held
    clock.tick(301)
    (reclaimed,) = await store.claim(limit=1, lease_seconds=300)
    assert reclaimed.id == event.id


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


async def test_run_once_drains_everything_due_and_returns_count(store, clock, alerts):
    for i in range(7):
        await _received(store, webhook_id=f"wh-{i}")
    dest = Destination(_http_error(503))  # first one fails transiently, rest succeed

    processed = await run_once(store, Pipeline(passthrough, dest), batch_size=3, alert=alerts)

    assert processed == 7
    assert len(store.by_status(EventStatus.DELIVERED)) == 6
    assert len(store.by_status(EventStatus.RECEIVED)) == 1  # scheduled for retry, not re-run
    assert await run_once(store, Pipeline(passthrough, dest), alert=alerts) == 0
