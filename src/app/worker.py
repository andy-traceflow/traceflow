"""Delivery worker: claims events from the store, transforms, delivers.

Run as an always-on Render background worker:
    python -m app.worker

Or drain the queue once and exit (cron-style, for cheap deployments):
    python -m app.worker --once

Retry policy
------------
Exponential backoff with ±20% jitter: 1m, 5m, 15m, 1h, 6h. After the 5th
failed attempt the event is marked `dead` and an alert fires. Permanent
failures (destination 4xx other than 408/429, validation errors, transform
errors) skip the schedule and go straight to `dead`.

Concurrency
-----------
`store.claim()` uses FOR UPDATE SKIP LOCKED, so any number of workers can
run against the same table. A claim carries a lease; if the worker dies
mid-event the lease expires and another worker reclaims the row.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from pydantic import ValidationError

from app.config import get_settings, require_startup_settings
from app.db import close_pool, init_pool
from app.models.event import Event, EventStatus
from app.pipeline import (
    PermanentDeliveryError,
    Pipeline,
    TransformError,
    TransientDeliveryError,
    build_pipeline,
)
from app.services.events import EventStore, get_event_store

logger = logging.getLogger(__name__)

BACKOFF_SCHEDULE_SECONDS: tuple[int, ...] = (60, 300, 900, 3600, 21600)  # 1m 5m 15m 1h 6h
MAX_ATTEMPTS = 5
PROCESSING_LEASE_SECONDS = 300  # must exceed the slowest adapter timeout (30s) comfortably
DEFAULT_BATCH_SIZE = 10
DEFAULT_POLL_INTERVAL_SECONDS = 5.0

# 4xx statuses that mean "try again later", not "this record is wrong".
_TRANSIENT_4XX = frozenset({408, 429})

FailureKind = Literal["permanent", "transient"]
AlertFn = Callable[[Event, str], Awaitable[None]]
BackoffFn = Callable[[int], float]


# ---------------------------------------------------------------------------
# Pure policy — no IO
# ---------------------------------------------------------------------------


def backoff_seconds(attempt: int, *, rng: Callable[[], float] = random.random) -> float:
    """Delay before retry number `attempt` (1-based), with ±20% jitter.

    Attempts beyond the schedule length clamp to the last entry. `rng` is
    injectable for tests; it must return a float in [0, 1).
    """
    if attempt < 1:
        raise ValueError("attempt is 1-based")
    base = BACKOFF_SCHEDULE_SECONDS[min(attempt, len(BACKOFF_SCHEDULE_SECONDS)) - 1]
    return base * (0.8 + 0.4 * rng())


def classify_failure(exc: BaseException) -> FailureKind:
    """Decide whether retrying could help.

    Unknown exception types are treated as transient: retrying a blip five
    times and then dead-lettering with an alert loses nothing, whereas
    guessing "permanent" for a bug we have not seen would drop the event
    on the first try.
    """
    if isinstance(exc, PermanentDeliveryError | TransformError | ValidationError | ValueError):
        return "permanent"
    if isinstance(exc, TransientDeliveryError):
        return "transient"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in _TRANSIENT_4XX or status >= 500:
            return "transient"
        if 400 <= status < 500:
            return "permanent"
        return "transient"
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return "transient"
    return "transient"


def describe_failure(exc: BaseException) -> str:
    """One-line, bounded description for `events.last_error`."""
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text[:300].replace("\n", " ")
        return f"HTTP {exc.response.status_code} from {exc.request.url}: {body}"
    text = f"{type(exc).__name__}: {exc}"
    return text[:2000]


async def log_alert(event: Event, error: str) -> None:
    """Default alert sink: an ERROR log line. Phase 6 replaces this with the
    Slack incoming-webhook poster (ALERT_WEBHOOK_URL)."""
    logger.error(
        "event dead-lettered",
        extra={
            "event_id": str(event.id),
            "webhook_id": event.webhook_id,
            "topic": event.topic,
            "attempts": event.attempts,
            "error": error,
        },
    )


# ---------------------------------------------------------------------------
# Per-event processing
# ---------------------------------------------------------------------------


async def process_one(
    store: EventStore,
    event: Event,
    pipeline: Pipeline,
    *,
    alert: AlertFn = log_alert,
    backoff: BackoffFn = backoff_seconds,
    now: datetime | None = None,
) -> EventStatus:
    """Transform + deliver one claimed event and record the outcome.

    Returns the event's new status. Never raises for delivery failures —
    those become retry/dead transitions. `now` and `backoff` are
    injectable for tests.
    """
    started = time.perf_counter()
    attempts = event.attempts + 1
    current = now if now is not None else datetime.now(UTC)

    try:
        try:
            record = pipeline.transform(event)
        except Exception as exc:  # any transform failure is permanent for this payload
            raise TransformError(f"{type(exc).__name__}: {exc}") from exc
        external_id = await pipeline.deliver(record)
    except Exception as exc:
        error = describe_failure(exc)
        kind = classify_failure(exc)
        if kind == "permanent" or attempts >= MAX_ATTEMPTS:
            await store.mark_dead(event.id, attempts=attempts, error=error)
            _log_transition(event, EventStatus.DEAD, attempts, started, error=error, kind=kind)
            await _fire_alert(alert, event.model_copy(update={"attempts": attempts}), error)
            return EventStatus.DEAD

        delay = backoff(attempts)
        next_retry_at = current + timedelta(seconds=delay)
        await store.schedule_retry(
            event.id, attempts=attempts, error=error, next_retry_at=next_retry_at
        )
        _log_transition(
            event,
            EventStatus.RECEIVED,
            attempts,
            started,
            error=error,
            kind=kind,
            next_retry_at=next_retry_at.isoformat(),
        )
        return EventStatus.RECEIVED

    await store.mark_delivered(event.id, attempts=attempts, external_id=external_id)
    _log_transition(event, EventStatus.DELIVERED, attempts, started, external_id=external_id)
    return EventStatus.DELIVERED


async def _fire_alert(alert: AlertFn, event: Event, error: str) -> None:
    """An alert sink failing must never take the worker down with it."""
    try:
        await alert(event, error)
    except Exception:
        logger.exception("alert delivery failed", extra={"event_id": str(event.id)})


def _log_transition(
    event: Event,
    status: EventStatus,
    attempts: int,
    started: float,
    **fields: object,
) -> None:
    duration_ms = round((time.perf_counter() - started) * 1000)
    logger.info(
        "event %s",
        status.value,
        extra={
            "event_id": str(event.id),
            "webhook_id": event.webhook_id,
            "topic": event.topic,
            "status": status.value,
            "attempts": attempts,
            "duration_ms": duration_ms,
            **fields,
        },
    )


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


async def run_once(
    store: EventStore,
    pipeline: Pipeline,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    alert: AlertFn = log_alert,
) -> int:
    """Drain everything that is due right now. Returns the number processed.

    Terminates because every processed event either leaves the due set
    (delivered/dead) or gets a future `next_retry_at`.
    """
    processed = 0
    while True:
        events = await store.claim(limit=batch_size, lease_seconds=PROCESSING_LEASE_SECONDS)
        if not events:
            return processed
        for event in events:
            await process_one(store, event, pipeline, alert=alert)
            processed += 1


async def run_forever(
    store: EventStore,
    pipeline: Pipeline,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    alert: AlertFn = log_alert,
) -> None:
    """Poll until cancelled. Sleeps only when the queue is empty."""
    while True:
        processed = await run_once(store, pipeline, batch_size=batch_size, alert=alert)
        if processed == 0:
            await asyncio.sleep(poll_interval)


async def _main_async(args: argparse.Namespace, pipeline: Pipeline) -> int:
    await init_pool()
    try:
        store = get_event_store()
        if args.once:
            n = await run_once(store, pipeline, batch_size=args.batch_size)
            logger.info("worker --once finished", extra={"processed": n})
        else:
            logger.info("worker started", extra={"batch_size": args.batch_size})
            await run_forever(
                store, pipeline, batch_size=args.batch_size, poll_interval=args.poll_interval
            )
        return 0
    finally:
        await close_pool()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker", description=__doc__)
    parser.add_argument("--once", action="store_true", help="drain due events and exit")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    args = parser.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    require_startup_settings(settings)  # fail closed: no DB URL → no worker
    pipeline = build_pipeline()  # fail closed: bad DESTINATION / missing creds → no worker

    try:
        return asyncio.run(_main_async(args, pipeline))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
